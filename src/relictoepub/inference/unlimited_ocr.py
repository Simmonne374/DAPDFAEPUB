"""Modulo 2 — Wrapper HuggingFace Transformers per Unlimited-OCR.

Questo modulo racchiude la complessità di:

* caricare il modello (con o senza quantizzazione)
* preparare correttamente i parametri esatti del paper
  (``ngram_no_repeat_size``, ``ngram_window``, ``image_size``, …)
* fare il ``generate()`` su un batch di pagine
* restituire Markdown strutturato pronto per la conversione EPUB

Implementazione progettata per essere **lazy**: il modello da 6 GB
viene caricato solo alla prima chiamata (``run()`` o ``run_batch()``),
non all'import. Così la CLI rimane veloce e l'UI Gradio può
istanziarsi senza saturare la VRAM al boot.
"""

from __future__ import annotations

import contextlib
import io
import logging
import queue
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path


from relictoepub.inference.config import (
    InferenceConfig,
    OCRPageResult,
    QuantizationMode,
)

logger = logging.getLogger(__name__)


class _QueueWriter(io.TextIOBase):
    """``io.TextIOBase`` minimale che scarica ogni ``write()`` in una ``queue.Queue``.

    Usato insieme a :func:`contextlib.redirect_stdout` per catturare
    l'output del modello (che emette i token via ``print``) senza
    manipolare lo ``sys.stdout`` globale — più thread-safe e robusto.
    """

    def __init__(self, q: queue.Queue) -> None:
        self._q = q

    def write(self, s: str) -> int:  # type: ignore[override]
        if s:
            self._q.put(s)
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        return None


class UnlimitedOCRRunner:
    """Wrapper del modello Baidu ``Unlimited-OCR``.

    Esempio:
        >>> runner = UnlimitedOCRRunner(InferenceConfig(quantization="int4"))
        >>> result = runner.run_batch([Path("p1.png"), Path("p2.png")])
        >>> print(result.markdown[:200])

    Note:
        Il modello viene caricato al primo accesso a :meth:`run` o
        :meth:`run_batch` (lazy init), oppure esplicitamente via
        :meth:`load_model`.
    """

    def __init__(self, config: InferenceConfig | None = None) -> None:
        self.config = config or InferenceConfig()
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Lazy init
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        """Carica il modello, il processor e il tokenizer in memoria."""
        if self._loaded:
            return

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - dipendenze obbligatorie
            raise RuntimeError(
                "Per usare UnlimitedOCRRunner servono: torch, transformers "
                "(>=4.45). Installale con `uv pip install -e \".[cuda118]\"`."
            ) from exc

        device = self.config.resolve_device()
        dtype_map = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }
        torch_dtype = dtype_map.get(self.config.dtype, torch.bfloat16)

        kwargs = {
            "trust_remote_code": True,
            "torch_dtype": torch_dtype,
        }
        if self.config.cache_dir is not None:
            kwargs["cache_dir"] = str(self.config.cache_dir)

        if self.config.quantization != QuantizationMode.NONE:
            try:
                from transformers import BitsAndBytesConfig
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "Per la quantizzazione servono bitsandbytes e accelerate."
                ) from exc

            quant_mode = self.config.quantization
            if quant_mode == QuantizationMode.INT4:
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch_dtype,
                    bnb_4bit_use_double_quant=True,
                )
            elif quant_mode == QuantizationMode.INT8:
                bnb_config = BitsAndBytesConfig(
                    load_in_8bit=True,
                )
            else:  # pragma: no cover - difesa
                bnb_config = None
            kwargs["quantization_config"] = bnb_config
            kwargs.pop("torch_dtype", None)

        logger.info(
            "Caricamento modello %s (device=%s, quant=%s, dtype=%s)",
            self.config.model_id, device,
            self.config.quantization.value, self.config.dtype,
        )
        t0 = time.perf_counter()

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.config.model_id,
                trust_remote_code=True,
                cache_dir=str(self.config.cache_dir) if self.config.cache_dir else None,
            )
            self._model = AutoModel.from_pretrained(
                self.config.model_id, **kwargs
            )
        except Exception as exc:
            raise RuntimeError(
                f"Impossibile caricare il modello {self.config.model_id!r}. "
                "Verifica la connessione internet o pre-scaricalo con "
                "`huggingface-cli download baidu/Unlimited-OCR`. "
                f"Errore originale: {exc}"
            ) from exc

        if device != "cpu" and self.config.quantization == QuantizationMode.NONE:
            self._model = self._model.to(device)

        self._model.eval()
        self._loaded = True

        elapsed = time.perf_counter() - t0
        logger.info("Modello caricato in %.1f s", elapsed)

    # ------------------------------------------------------------------
    # Inference API
    # ------------------------------------------------------------------

    def run(self, image_path: str | Path) -> OCRPageResult:
        """OCR di una singola pagina."""
        return self.run_batch([Path(image_path)])

    def run_batch_iter(self, image_paths: Sequence[str | Path]) -> Iterator[tuple[str, str]]:
        """Esegue l'OCR yielding del testo parziale in tempo reale (stream dei token).

        Il modello Unlimited-OCR scrive i suoi token grezzi su stdout;
        qui li catturiamo tramite ``contextlib.redirect_stdout``
        (thread-safe, non globale) instradandoli in una ``queue.Queue``,
        senza più manipolare ``sys.stdout`` manualmente.
        """
        if not image_paths:
            yield "", "done"
            return
        self.load_model()

        import tempfile

        prompt = self.config.prompt_template
        ngram_window = self.config.ngram_window_multi if len(image_paths) > 1 else 128
        temp_dir = tempfile.gettempdir()

        q: queue.Queue[str] = queue.Queue()
        writer = _QueueWriter(q)
        result_container: dict[str, object] = {}
        infer_exception: dict[str, BaseException] = {}

        def worker() -> None:
            # redirect_stdout è thread-safe: push del writer e pop
            # automatico, anche se il thread termina per eccezione.
            with contextlib.redirect_stdout(writer):
                try:
                    if len(image_paths) == 1:
                        decoded = self._model.infer(  # type: ignore[union-attr]
                            self._tokenizer,
                            prompt=prompt,
                            image_file=str(image_paths[0]),
                            eval_mode=True,
                            max_length=self.config.max_new_tokens,
                            no_repeat_ngram_size=self.config.ngram_no_repeat_size,
                            ngram_window=ngram_window,
                            output_path=temp_dir,
                        )
                    else:
                        decoded, _ = self._model.infer_multi(  # type: ignore[union-attr]
                            self._tokenizer,
                            prompt=prompt,
                            image_files=[str(p) for p in image_paths],
                            image_size=1024,
                            max_length=self.config.max_new_tokens,
                            no_repeat_ngram_size=self.config.ngram_no_repeat_size,
                            ngram_window=ngram_window,
                            output_path=temp_dir,
                        )
                    result_container["decoded"] = decoded
                except Exception as exc:  # noqa: BLE001
                    infer_exception["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        accumulated_text = ""
        try:
            while thread.is_alive() or not q.empty():
                try:
                    token = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                accumulated_text += token
                yield accumulated_text, "running"
        finally:
            thread.join(timeout=1.0)

        if "error" in infer_exception:
            raise infer_exception["error"]

        decoded = result_container.get("decoded", accumulated_text)
        yield decoded, "done"

    def run_batch(self, image_paths: Sequence[str | Path]) -> OCRPageResult:
        """OCR di un batch di pagine (multi-page one-shot).

        Il numero di pagine deve stare sotto il limite del context
        del modello (32K token, paper §3.4). Per PDF molto lunghi
        la pipeline esterna deve gestire il batching.
        """
        final_decoded = ""
        for partial_text, status in self.run_batch_iter(image_paths):
            if status == "done":
                final_decoded = partial_text

        raw_text = final_decoded.strip()
        markdown = self._strip_image_tokens(raw_text)

        return OCRPageResult(
            markdown=markdown,
            page_separators=raw_text.count("<page>") or 1,
            raw_text=raw_text,
            extra={
                "batch_size": len(image_paths),
                "quantization": self.config.quantization.value,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_image_tokens(text: str) -> str:
        """Rimuove i token immagine tipici del multimodal prompt."""
        # Alcuni wrapper aggiungono tag <image> ripetuti o placeholder
        for token in ("<image>", "<|image|>", "<image_1>"):
            text = text.replace(token, "")
        # Collassa righe vuote multiple in una sola
        lines = [ln.rstrip() for ln in text.splitlines()]
        cleaned: list[str] = []
        prev_empty = False
        for ln in lines:
            if not ln.strip():
                if prev_empty:
                    continue
                prev_empty = True
                cleaned.append("")
            else:
                prev_empty = False
                cleaned.append(ln)
        return "\n".join(cleaned).strip()

    def unload(self) -> None:
        """Scarica il modello dalla memoria (utile per testing/notebook)."""
        if not self._loaded:
            return
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:  # pragma: no cover
            pass
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._loaded = False
        logger.info("Modello scaricato dalla memoria")


__all__ = ["UnlimitedOCRRunner"]
