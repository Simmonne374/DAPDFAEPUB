"""Orchestratore end-to-end: PDF → EPUB3.

La classe :class:`Pipeline` è l'unico punto di contatto che la CLI e
la UI Gradio usano. Espone un'API sincrona (con aggiornamenti di stato)
e un'API generator-based (``run_iter``) che emette eventi
``ProgressEvent`` consumabili dalla UI live.

Flusso:
1. **Ingest** (PyMuPDF) → PNG 300 DPI + 1024×1024
2. **OCR** (Unlimited-OCR, 4-bit) → Markdown per batch di N pagine
3. **Clean** → testo normalizzato, BBox estratti
4. **Crop** → immagini ritagliate con Pillow
5. **Optimize** → WebP grayscale ottimizzato E-ink
6. **Compile** (pypandoc + ebooklib) → file ``.epub``
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from relictoepub.checkpoint import (
    CheckpointConfigMismatchError,
    CheckpointMismatchError,
    CheckpointState,
    CheckpointStore,
    new_checkpoint_state,
)
from relictoepub.compile.build_epub import BookMetadata, build_epub
from relictoepub.inference.config import InferenceConfig, QuantizationMode
from relictoepub.inference.unlimited_ocr import OCRCancelledError, UnlimitedOCRRunner
from relictoepub.ingest import IngestResult, render_pdf
from relictoepub.postprocess.bbox_crop import (
    BBox,
    crop_image_from_bbox_with_box,
)
from relictoepub.postprocess.text_clean import clean_text
from relictoepub.postprocess.webp_optim import optimize_batch

logger = logging.getLogger(__name__)


def _build_figure(img_html: str, caption: str | None) -> str:
    """Costruisce il markup ``<figure>`` XHTML con ``<figcaption>`` opzionale.

    Issue #10: il modello Unlimited-OCR emette le caption come token separati
    che vanno accoppiati all'immagine/figura/tabella che li precede.
    Restituisce una stringa pronta da inserire nel markdown intermedio.
    """
    if caption:
        # Escape di base per evitare di rompere il markup.
        safe_caption = (
            caption.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return (
            f'\n\n<figure style="margin:1em 0;text-align:center;">'
            f'{img_html}'
            f'<figcaption style="font-style:italic;color:#444;'
            f'font-size:0.95em;margin-top:0.4em;">{safe_caption}</figcaption>'
            f'</figure>\n\n'
        )
    return (
        f'\n\n<figure style="margin:1em 0;text-align:center;">{img_html}</figure>\n\n'
    )


# B32/B50/B51: pattern regex hoisted a compile-time per evitare di
# ricompilare per ogni pagina (miglioramento performance significativo
# su libri di centinaia di pagine).
_DET_PATTERN = re.compile(
    r"<\|det\|>([^\[]+)\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]<\|/det\|>"
)
_LAYOUT_TAG_RE = re.compile(
    r"<\|det\|>(footer|page_number|header)\[[^\]]+\]<\|/det\|>([^\n<]+)"
)
_EMPTY_LAYOUT_RE = re.compile(
    r"<\|det\|>(?:footer|page_number|header)\[[^\]]+\]<\|/det\|>"
)


@dataclass
class ProgressEvent:
    """Singolo evento di progresso emesso dalla pipeline.

    Attributes:
        phase: fase corrente (``"rendering"``, ``"ocr"``, ``"cropping"``,
            ``"optimizing"``, ``"compiling"``, ``"done"``, ``"error"``).
        message: descrizione human-readable.
        current: elemento corrente (pagina, immagine...).
        total: totale elementi della fase.
        percent: 0-100 (per comodità della UI).
        extra: dati extra (path, statistiche...).
    """

    phase: str
    message: str = ""
    current: int = 0
    total: int = 0
    percent: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class ModelNotFoundError(RuntimeError):
    """Eccezione tipizzata sollevata quando il modello Unlimited-OCR non è stato
    scaricato nella cache locale di HuggingFace.

    Attributes:
        model_id: identificativo HuggingFace del modello mancante.
        cache_dir: directory di cache attesa.
    """

    def __init__(self, model_id: str, cache_dir: Path | None = None) -> None:
        self.model_id = model_id
        self.cache_dir = cache_dir
        cache_hint = f" (cache: {cache_dir})" if cache_dir else ""
        super().__init__(
            f"Modello '{model_id}' non trovato nella cache HuggingFace{cache_hint}. "
            f"Scaricalo con `python scripts/download_model.py` oppure tramite il "
            f"pulsante 'Scarica modello' nella UI Gradio."
        )


class PipelineCancelledError(RuntimeError):
    """Eccezione tipizzata sollevata quando la pipeline viene cancellata
    esternamente (tipicamente dal bottone "Stop" della UI Gradio).

    Viene sollevata all'inizio del batch OCR successivo al flag di cancel.
    I batch già completati (e quindi i loro checkpoint) sono preservati.

    Attributes:
        completed_batches: numero di batch completati prima della cancellazione.
    """

    def __init__(self, completed_batches: int = 0) -> None:
        self.completed_batches = completed_batches
        super().__init__(
            f"Pipeline cancellata dopo {completed_batches} batch OCR completati. "
            f"I batch completati sono stati salvati nel checkpoint; rilancia "
            f"per riprendere."
        )


def check_model_available(model_id: str = "baidu/Unlimited-OCR") -> bool:
    """Controlla se il modello è presente nella cache di HuggingFace.

    Usa :func:`huggingface_hub.try_to_load_from_cache` per ogni file
    ``*.safetensors``: se nessun file è disponibile localmente, il modello
    è considerato assente.

    Args:
        model_id: ID HuggingFace del modello. Default ``"baidu/Unlimited-OCR"``.

    Returns:
        ``True`` se almeno un file ``.safetensors`` è in cache, ``False`` altrimenti.
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        # huggingface-hub non installato: trattiamo il modello come non disponibile
        return False

    try:
        # Cerca i file più comuni; il primo match positivo indica presenza
        for pattern in ("*.safetensors", "*.bin"):
            result = try_to_load_from_cache(model_id, pattern)
            # result è None se non trovato, oppure (path, etag) se cached
            if result is not None:
                path_or_tuple = result if isinstance(result, tuple) else (result,)
                if path_or_tuple and path_or_tuple[0] is not None:
                    return True
    except (OSError, ValueError, TypeError, AttributeError):
        # Qualsiasi errore (modello non esistente, no internet, ecc.) → non disponibile
        return False
    return False


@dataclass
class PipelineResult:
    """Risultato finale di una conversione."""

    output_path: Path
    pages_processed: int
    images_extracted: int
    total_seconds: float
    markdown_chars: int
    extra: dict[str, Any] = field(default_factory=dict)


class Pipeline:
    """Orchestratore della pipeline PDF → EPUB3.

    Esempio (CLI):
        >>> pipeline = Pipeline()
        >>> for event in pipeline.run_iter(Path("book.pdf"), Path("book.epub")):
        ...     print(event.phase, event.message)
        >>> # Risultato finale: Path("book.epub")

    Esempio (sincrono):
        >>> result = pipeline.run(Path("book.pdf"), Path("book.epub"))
    """

    def __init__(
        self,
        *,
        inference_config: InferenceConfig | None = None,
        dpi: int = 300,
        target_size: int = 1024,
        max_pages_per_batch: int = 20,
        eink_optimize: bool = True,
        metadata: BookMetadata | None = None,
            chapter_pages: int | None = None,
            work_dir: Path | None = None,
            checkpoint_store: CheckpointStore | None = None,
        ) -> None:
        self.inference_config = inference_config or InferenceConfig(
            quantization=QuantizationMode.INT4
        )
        self.dpi = dpi
        self.target_size = target_size
        # Sincronizza il batch size pipeline ↔ config modello
        self.max_pages_per_batch = min(
            max_pages_per_batch, max(1, self.inference_config.pages_per_batch)
        )
        self.inference_config.pages_per_batch = self.max_pages_per_batch
        self.eink_optimize = eink_optimize
        # Snapshot del metadata fornito al costruttore — non viene
        # sovrascritto da :meth:`run_iter`. Se l'utente passa un
        # ``BookMetadata``, lo rispettiamo; altrimenti ne creiamo uno
        # basato sul nome PDF al PRIMO :meth:`run_iter` (BUG #5/#6).
        self._initial_metadata = metadata
        self.metadata = metadata  # sarà settato da run_iter se None
        self.chapter_pages = chapter_pages
        self.work_dir = work_dir
        self.checkpoint_store = checkpoint_store
        self._runner: UnlimitedOCRRunner | None = None
        # Cancel token per interruption cooperativa. Settato da
        # :meth:`Pipeline.cancel` (chiamato dal bottone Stop di Gradio
        # o da ``KeyboardInterrupt`` nella CLI). Controllato a inizio
        # di ogni batch OCR → solleva :class:`PipelineCancelledError`.
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Cancel API (UI "Stop" button)
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Richiede la cancellazione cooperativa della pipeline.

        Effect: al prossimo checkpoint (inizio del batch OCR successivo)
        verrà sollevata :class:`PipelineCancelledError`. Il batch in
        corso non viene killato: completa normalmente e il suo stato
        viene salvato su checkpoint (se abilitato).

        Idempotente: chiamate multiple hanno lo stesso effetto.
        """
        if not self._cancel_event.is_set():
            logger.info("Pipeline.cancel() invocato: cancel richiesto.")
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """True se :meth:`cancel` è stato chiamato."""
        return self._cancel_event.is_set()

    def reset_cancel(self) -> None:
        """Resetta il cancel event per riusare la stessa istanza Pipeline."""
        self._cancel_event.clear()

    def resolve_cover_image(
        self,
        metadata: BookMetadata,
        ingest_result: IngestResult,
    ) -> Path | None:
        """Determina il path della cover image da usare per l'EPUB.

        Regole (in ordine di priorità):
        1. ``metadata.cover_image`` se impostato e il file esiste su disco.
        2. Prima pagina del PDF (``ingest_result.pages[0].original_path``)
           come fallback per libri senza cover dedicata.
        3. ``None`` se il PDF non ha pagine (caso degenere).

        Centralizzato qui (vs inline nel :meth:`run_iter`) per:
        * permettere ai test di validare la logica senza eseguire l'OCR;
        * dare un'unica sorgente di verità condivisa con eventuali altri
          consumer (es. UI Gradio che voglia mostrare l'anteprima).

        Args:
            metadata: metadata del libro; il campo ``cover_image`` viene
                onorato se impostato.
            ingest_result: risultato del rendering PDF.

        Returns:
            Path al file di cover (``PNG``/``WEBP``/...) oppure ``None``.
        """
        # 1) Cover esplicita dall'utente (vince sempre se esiste).
        user_cover = getattr(metadata, "cover_image", None)
        if user_cover is not None:
            user_cover_path = Path(user_cover)
            if user_cover_path.is_file():
                return user_cover_path
            logger.warning(
                "metadata.cover_image=%s non trovato su disco; "
                "fallback alla prima pagina del PDF.",
                user_cover_path,
            )

        # 2) Fallback: prima pagina del PDF.
        if ingest_result.pages:
            return ingest_result.pages[0].original_path

        # 3) PDF vuoto (caso degenere).
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(
        self,
        input_pdf: Path,
        output_epub: Path,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> PipelineResult:
        """Versione sincronizzata. Eventuali eventi arrivano al callback.

        Returns:
            :class:`PipelineResult` con i dati finali della conversione.
        """
        last_result: PipelineResult | None = None
        for event in self.run_iter(input_pdf, output_epub):
            if progress_callback is not None:
                progress_callback(event)
            if event.phase == "done":
                data = event.extra.get("result")
                if data:
                    last_result = PipelineResult(**data)
        if last_result is None:
            raise RuntimeError(
                "Pipeline terminata senza emettere l'evento 'done'. "
                "Controllare i log per errori."
            )
        return last_result

    def run_iter(
        self,
        input_pdf: Path,
        output_epub: Path,
    ) -> Iterator[ProgressEvent]:
        """Generatore di eventi di progresso; alla fine emette ``"done"``.

        Yields:
            Eventi :class:`ProgressEvent` per ogni fase.
        """
        input_pdf = Path(input_pdf)
        output_epub = Path(output_epub)
        # Nota: NON resettiamo ``_cancel_event`` qui. L'utente che
        # vuole riusare la stessa istanza ``Pipeline`` dopo una
        # cancellazione deve chiamare esplicitamente
        # :meth:`reset_cancel` (vedi ``test_pipeline_reset_cancel_allows_reuse``).
        # Altrimenti il cancel flag resterebbe "appiccicoso" tra run.

        # Cancel check pre-rendering (BUG #16): se l'utente ha già
        # richiesto cancel prima ancora di iniziare (es. clicca Stop
        # immediatamente dopo aver cliccato Converti), saltiamo il
        # costoso rendering PDF.
        if self._cancel_event.is_set():
            yield ProgressEvent(
                phase="cancelling",
                message="⏹️ Cancellazione richiesta prima dell'avvio.",
                extra={"cancelled": True, "completed_batches": 0},
            )
            raise PipelineCancelledError(completed_batches=0)

        # Metadata: usa l'override fornito al costruttore oppure il
        # default basato sul PDF. Non mutiamo ``_initial_metadata`` per
        # consentire riuso dell'istanza su più file (BUG #5).
        if self._initial_metadata is not None:
            self.metadata = self._initial_metadata
        elif self.metadata is None:
            self.metadata = BookMetadata(title=input_pdf.stem)
        # Se l'utente ha passato ``chapter_pages`` a ``Pipeline.__init__``
        # ma non ha fornito un ``BookMetadata`` proprio, applichiamo il
        # valore al metadata di default così l'EPUB finale lo rispetta.
        if self.chapter_pages is not None:
            self.metadata = replace(
            self.metadata, chapter_pages=self.chapter_pages
            )
        start = time.perf_counter()

        # Checkpoint: carica stato precedente se disponibile e valido.
        checkpoint_state: CheckpointState | None = None
        if self.checkpoint_store is not None:
            existing = self.checkpoint_store.load()
            if existing is not None:
                from relictoepub.checkpoint import compute_pdf_sha256
                current_sha = compute_pdf_sha256(input_pdf)
                if existing.source_pdf_sha256 != current_sha:
                    raise CheckpointMismatchError(
                        f"Checkpoint presente ({self.checkpoint_store.path}) "
                        f"appartiene a un PDF diverso (sha256 atteso: "
                        f"{current_sha[:20]}…, trovato: "
                        f"{existing.source_pdf_sha256[:20]}…). "
                        f"Usa --no-resume per forzare la riesecuzione "
                        f"oppure elimina {self.checkpoint_store.directory}."
                    )
                # B32: anche se lo SHA combacia, il checkpoint deve essere
                # stato creato con lo stesso ``pages_per_batch``. Altrimenti
                # il resume riusa markdown cached aggregato per N pagine
                # come se fosse da M pagine → pagine mescolate nell'EPUB.
                if existing.batch_size != self.max_pages_per_batch:
                    raise CheckpointConfigMismatchError(
                        f"Checkpoint presente ({self.checkpoint_store.path}) "
                        f"è stato creato con --pages-per-batch="
                        f"{existing.batch_size}, ma ora stai usando "
                        f"{self.max_pages_per_batch}. Riusare il checkpoint "
                        f"con un batch_size diverso mescola le pagine "
                        f"cached senza nessun warning. Usa lo stesso "
                        f"--pages-per-batch oppure --no-resume per "
                        f"forzare la riesecuzione da zero."
                    )
                checkpoint_state = existing
                logger.info(
                    "Checkpoint caricato: %d/%d batch già completati",
                    len(existing.completed_batches), existing.total_batches,
                )
                yield ProgressEvent(
                    phase="ocr",
                    message=(
                        f"Ripresa da checkpoint: "
                        f"{len(existing.completed_batches)}/"
                        f"{existing.total_batches} batch già OCR-ati"
                    ),
                    current=len(existing.completed_batches),
                    total=existing.total_batches,
                    percent=(
                        len(existing.completed_batches)
                        / max(1, existing.total_batches)
                    ) * 100.0,
                    extra={"resuming": True},
                )

        # 1) Ingest
        yield ProgressEvent(phase="rendering", message="Inizio rendering PDF…")
        ingest_result = render_pdf(
            input_pdf,
            output_dir=self.work_dir,
            dpi=self.dpi,
            target_size=self.target_size,
        )
        total_pages = len(ingest_result.pages)
        yield ProgressEvent(
            phase="rendering",
            message=f"Renderizzate {total_pages} pagine",
            current=total_pages, total=total_pages, percent=100.0,
            extra={"output_dir": str(ingest_result.output_dir)},
        )

        # 2) OCR in batch
        if self._runner is None:
            self._runner = UnlimitedOCRRunner(self.inference_config)
        all_markdown_parts: list[str] = []
        all_pages_processed = 0

        crops_dir = ingest_result.output_dir / "crops"
        crops_dir.mkdir(exist_ok=True)
        saved_crops: list[Path] = []

        # Setup iniziale checkpoint state
        if self.checkpoint_store is not None and checkpoint_state is None:
            total_batches = (
                (total_pages + self.max_pages_per_batch - 1)
                // self.max_pages_per_batch
            )
            checkpoint_state = new_checkpoint_state(
                input_pdf,
                total_batches=total_batches,
                batch_size=self.max_pages_per_batch,
            )

        for batch_start in range(0, total_pages, self.max_pages_per_batch):
            batch_end = min(batch_start + self.max_pages_per_batch, total_pages)
            batch_idx = batch_start // self.max_pages_per_batch

            # Cancel check: prima di iniziare il batch, verifichiamo se
            # l'utente ha richiesto interruzione. Solleva PipelineCancelledError;
            # il batch in corso NON viene abortito (per non lasciare checkpoint
            # in stato inconsistente).
            if self._cancel_event.is_set():
                completed_so_far = (
                    len(checkpoint_state.completed_batches)
                    if checkpoint_state is not None else 0
                )
                yield ProgressEvent(
                    phase="cancelling",
                    message=(
                        f"⏹️ Cancellazione richiesta dopo {completed_so_far} batch. "
                        f"Salvataggio stato e uscita."
                    ),
                    current=batch_start, total=total_pages,
                    percent=(batch_start / total_pages) * 100.0,
                    extra={"cancelled": True,
                           "completed_batches": completed_so_far},
                )
                # L'ultimo save (sotto) viene eseguito subito, poi raise.
                if self.checkpoint_store is not None and checkpoint_state is not None:
                    self.checkpoint_store.save(checkpoint_state)
                raise PipelineCancelledError(completed_batches=completed_so_far)

            # Checkpoint: riusa il markdown cached se il batch è già stato fatto.
            cached_markdown = (
                checkpoint_state.batch_markdown.get(str(batch_idx))
                if checkpoint_state is not None and batch_idx in checkpoint_state.completed_batches
                else None
            )
            if cached_markdown is not None:
                # Split per pagina perché il markdown è già l'intero batch.
                cached_parts = cached_markdown.split("\n\n<!-- pagebreak -->\n\n")
                # Se il cached è un singolo chunk (no pagebreak), inseriscilo come 1 page
                if len(cached_parts) == 1:
                    cached_parts = [cached_parts[0]]
                all_markdown_parts.extend(cached_parts)
                all_pages_processed += batch_end - batch_start
                yield ProgressEvent(
                    phase="ocr",
                    message=(
                        f"Batch {batch_idx+1}/{checkpoint_state.total_batches} "
                        f"recuperato da checkpoint"
                    ),
                    current=batch_end, total=total_pages,
                    percent=(batch_end / total_pages) * 100.0,
                    extra={"batch_size": batch_end - batch_start, "cached": True},
                )
                continue

            batch_pages = ingest_result.pages[batch_start:batch_end]
            yield ProgressEvent(
                phase="ocr",
                message=f"OCR batch pagine {batch_start+1}-{batch_end}/{total_pages}",
                current=batch_start + 1, total=total_pages,
                percent=(batch_end / total_pages) * 100.0,
                extra={"batch_size": len(batch_pages)},
            )
            normalized_paths = [p.normalized_path for p in batch_pages]

            final_raw_text = ""
            try:
                iter_stream = self._runner.run_batch_iter(
                    normalized_paths,
                    cancel_check=self.is_cancelled,
                )
            except OCRCancelledError:
                # Cancel arrivato PRIMA del primo yield (worker ancora
                # bloccato in ``infer()``): viene già alzato dal loop
                # interno del runner. Mappiamo al contratto pubblico
                # ``PipelineCancelledError`` emettendo prima l'evento
                # ``cancelling`` per la UI, esattamente come il path
                # normale post-yield.
                completed_so_far = (
                    len(checkpoint_state.completed_batches)
                    if checkpoint_state is not None else 0
                )
                logger.info(
                    "Cancel ricevuto durante OCR batch %d (catturato "
                    "dal runner prima del primo yield). "
                    "I batch %d..%d saranno saltati.",
                    batch_idx, batch_idx,
                    checkpoint_state.total_batches - 1 if checkpoint_state else 0,
                )
                yield ProgressEvent(
                    phase="cancelling",
                    message=(
                        f"⏹️ Cancellazione ricevuta durante OCR batch "
                        f"{batch_idx + 1}. Stop."
                    ),
                    extra={"cancelled": True, "completed_batches": completed_so_far},
                )
                raise PipelineCancelledError(completed_batches=completed_so_far) from None
            for partial_text, status in iter_stream:
                # Cancel check mid-batch: se l'utente preme Stop mentre
                # l'inferenza sta girando, interrompi al prossimo token
                # yielded. Più reattivo del check solo a inizio-batch.
                if self._cancel_event.is_set():
                    completed_so_far = (
                        len(checkpoint_state.completed_batches)
                        if checkpoint_state is not None else 0
                    )
                    logger.info(
                        "Cancel ricevuto durante OCR batch %d. "
                        "I batch %d..%d saranno saltati.",
                        batch_idx, batch_idx, checkpoint_state.total_batches - 1
                        if checkpoint_state else 0,
                    )
                    # Yield dell'evento cancelling per la UI prima del raise.
                    yield ProgressEvent(
                        phase="cancelling",
                        message=(
                            f"⏹️ Cancellazione ricevuta durante OCR batch "
                            f"{batch_idx + 1}. Stop."
                        ),
                        extra={"cancelled": True, "completed_batches": completed_so_far},
                    )
                    raise PipelineCancelledError(
                        completed_batches=completed_so_far,
                    )
                if status == "running":
                    chunk = partial_text[-500:] if len(partial_text) > 500 else partial_text
                    yield ProgressEvent(
                        phase="ocr",
                        message=f"OCR batch pagine {batch_start+1}-{batch_end}/{total_pages}\n[Testo estratto in tempo reale]:\n{chunk}",
                        current=batch_start + 1, total=total_pages,
                        percent=(batch_end / total_pages) * 100.0,
                        extra={"batch_size": len(batch_pages), "transient": True},
                    )
                else:
                    final_raw_text = partial_text

            raw_text = final_raw_text.strip()

            # Dividi l'output grezzo per pagina
            pages_raw = re.split(r"(?i)<page>", raw_text)
            if pages_raw and not pages_raw[0].strip():
                pages_raw = pages_raw[1:]

            batch_markdown_parts = []
            for idx, page in enumerate(batch_pages):
                page_text = pages_raw[idx] if idx < len(pages_raw) else ""

                def wrap_layout_tags(match):
                    label = match.group(1).strip()
                    text_content = match.group(2).strip()
                    return f'\n\n<div class="{label}">{text_content}</div>\n\n'

                # Avvolge i tag footer/page_number/header con testo in un div XHTML per conservarli ed impaginarli
                # (B50: regex hoisted — compilato una sola volta, non per pagina)
                page_text = _LAYOUT_TAG_RE.sub(wrap_layout_tags, page_text)

                # Rimuoviamo eventuali tag residui vuoti di layout
                # (B51: regex hoisted — compilato una sola volta, non per pagina)
                page_text = _EMPTY_LAYOUT_RE.sub("", page_text)

                img_counter = 0
                page_num = page.page_num  # bind for closure (B023)
                page_path = page.original_path  # bind for closure (B023)
                # Larghezza della pagina originale in pixel (300 DPI):
                # usata per calcolare la width % corretta del bbox denormalizzato.
                # Pre-popolata in ``RenderedPage`` (vedi ``ingest.py``) per non
                # dover riaprire l'immagine ad ogni pagina (issue #10).
                _page_w_px = page.width_px or 0

                def replace_tag(match, _pn=page_num, _pp=page_path, _page_w_px=_page_w_px):
                    nonlocal img_counter
                    label = match.group(1).strip()
                    x1, y1, x2, y2 = (int(g) for g in match.groups()[1:5])
                    bbox = BBox(x_min=x1, y_min=y1, x_max=x2, y_max=y2, label=label)

                    if label in ("image", "figure", "table"):
                        img_label = f"{label}{img_counter}"
                        img_counter += 1
                        ext = ".webp" if self.eink_optimize else ".png"
                        out_filename = f"page{_pn:04d}_{img_label}{ext}"
                        out_path = crops_dir / f"page{_pn:04d}_{img_label}.png"

                        crop_result = crop_image_from_bbox_with_box(
                            _pp, bbox, output_path=out_path, target_size=self.target_size
                        )
                        if crop_result is None:
                            return "\n\n"
                        result_path, _pixel_box = crop_result
                        if not result_path.exists():
                            return "\n\n"
                        saved_crops.append(result_path)
                        # Larghezza % derivata dal bbox denormalizzato (in pixel
                        # della pagina 300 DPI), non dalle coordinate [0,1000]
                        # dell'immagine padded 1024: indipendente dall'aspect-
                        # ratio della pagina.
                        width_pct = bbox.width_pct_against(_page_w_px)
                        # Clamp per evitare icone giganti o layout troppo stretti
                        # (decisione utente: [25, 100]).
                        width_pct = max(25.0, min(100.0, width_pct))
                        img_html = (
                            f'<img src="images/{out_filename}" '
                            f'style="width:{width_pct:.1f}%;max-width:100%;'
                            f'height:auto;display:block;margin:1em auto;" />'
                        )
                        return _build_figure(img_html, caption=None)

                    elif label == "title":
                        return "\n\n# "
                    elif label == "heading":
                        return "\n\n## "
                    elif label == "subtitle":
                        return "\n\n### "

                    return "\n\n"

                # Trova tutti i <|det|>...<|/det|> in ordine di apparizione e
                # processali sequenzialmente. Questo permette di accoppiare
                # ``image_caption`` / ``figure_caption`` / ``table_caption``
                # con l'immagine/figura/tabella che li precede immediatamente
                # (modello Unlimited-OCR emette la caption come token separato
                # subito dopo l'immagine -- issue #10).
                det_matches = list(_DET_PATTERN.finditer(page_text))
                pieces: list[str] = []
                cursor = 0
                # Mappa label immagine -> prefisso caption atteso.
                caption_label_for = {
                    "image": "image_caption",
                    "figure": "figure_caption",
                    "table": "table_caption",
                }
                last_was_image: str | None = None  # prefisso caption atteso
                last_img_html: str | None = None   # <img> pendente da wrappare
                for det_match in det_matches:
                    # 1) Emetti il testo fino al match corrente (eventualmente
                    #    include caption orfane che non avevano immagine prima).
                    pieces.append(page_text[cursor:det_match.start()])
                    cursor = det_match.end()

                    m_label = det_match.group(1).strip()
                    m_groups = det_match.groups()[1:5]

                    if m_label in ("image", "figure", "table"):
                        # C'e un'immagine pendente dalla quale non e arrivata
                        # una caption? Flushiamola senza caption.
                        if last_img_html is not None:
                            pieces.append(_build_figure(last_img_html, caption=None))
                            last_img_html = None
                            last_was_image = None

                        # Costruisci il tag <img> (stessa logica di replace_tag).
                        x1, y1, x2, y2 = (int(g) for g in m_groups)
                        bbox = BBox(
                            x_min=x1, y_min=y1, x_max=x2, y_max=y2, label=m_label,
                        )
                        img_label = f"{m_label}{img_counter}"
                        img_counter += 1
                        ext = ".webp" if self.eink_optimize else ".png"
                        out_filename = f"page{page_num:04d}_{img_label}{ext}"
                        out_path = crops_dir / f"page{page_num:04d}_{img_label}.png"
                        crop_result = crop_image_from_bbox_with_box(
                            page_path,
                            bbox,
                            output_path=out_path,
                            target_size=self.target_size,
                        )
                        if crop_result is None:
                            continue
                        result_path, _pixel_box = crop_result
                        if not result_path.exists():
                            continue
                        saved_crops.append(result_path)
                        width_pct = bbox.width_pct_against(_page_w_px)
                        width_pct = max(25.0, min(100.0, width_pct))
                        last_img_html = (
                            f'<img src="images/{out_filename}" '
                            f'style="width:{width_pct:.1f}%;max-width:100%;'
                            f'height:auto;display:block;margin:1em auto;" />'
                        )
                        last_was_image = caption_label_for[m_label]
                        continue

                    if (
                        m_label in ("image_caption", "figure_caption", "table_caption")
                        and last_was_image == m_label
                        and last_img_html is not None
                    ):
                        # B58: la caption può superare i 400 char (es.
                        # didascalie di tabelle, descrizioni estese di figure).
                        # La cercavamo in una finestra fissa di 400 char,
                        # troncando il testo e riversando la parte eccedente
                        # nel flusso principale come paragrafo orfano dopo
                        # ``</figure>`` (contenuto duplicato nell'EPUB).
                        # Cerchiamo ora il primo ``\n`` o ``<|`` SENZA
                        # limiti di lunghezza sul testo sorgente, e avanziamo
                        # ``cursor`` di pari passo per consumare la caption
                        # estratta (evita la duplicazione).
                        rest = page_text[det_match.end():]
                        cut = re.search(r"[\n]|<\|", rest)
                        cut_pos = cut.start() if cut else len(rest)
                        caption_text = rest[:cut_pos]
                        caption_text = re.sub(
                            r"<\|ref\|>.*?<\|/ref\|>", "", caption_text,
                            flags=re.DOTALL,
                        ).strip()
                        # Flush del <figure> con <figcaption>.
                        pieces.append(
                            _build_figure(last_img_html, caption=caption_text or None),
                        )
                        # Consuma la caption dal flusso principale: senza
                        # questo, il testo verrebbe re-incluso in
                        # ``page_text[cursor:]`` a fine loop, duplicando
                        # la caption nell'EPUB finale.
                        cursor = det_match.end() + cut_pos
                        last_img_html = None
                        last_was_image = None
                        continue

                    # Qualsiasi altro label (text, header, footer, page_number,
                    # caption orfana, ...) -- flush di un eventuale <figure>
                    # pendente e poi delega a ``replace_tag`` per il testo.
                    if last_img_html is not None:
                        pieces.append(_build_figure(last_img_html, caption=None))
                        last_img_html = None
                        last_was_image = None
                    pieces.append(replace_tag(det_match))

                # Flush finale di un <figure> pendente.
                if last_img_html is not None:
                    pieces.append(_build_figure(last_img_html, caption=None))
                pieces.append(page_text[cursor:])

                page_markdown = "".join(pieces)
                page_markdown = self._runner._strip_image_tokens(page_markdown)
                batch_markdown_parts.append(page_markdown)

            # Salva il batch sul checkpoint PRIMA di passare al successivo.
            batch_markdown_str = "\n\n<!-- pagebreak -->\n\n".join(batch_markdown_parts)
            all_markdown_parts.extend(batch_markdown_parts)
            all_pages_processed += len(batch_pages)

            if self.checkpoint_store is not None and checkpoint_state is not None:
                completed = list(checkpoint_state.completed_batches)
                if batch_idx not in completed:
                    completed.append(batch_idx)
                completed.sort()
                state_dict = {k: v for k, v in checkpoint_state.batch_markdown.items()}
                state_dict[str(batch_idx)] = batch_markdown_str
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                checkpoint_state = CheckpointState(
                    source_pdf_sha256=checkpoint_state.source_pdf_sha256,
                    source_pdf_size_bytes=checkpoint_state.source_pdf_size_bytes,
                    total_batches=checkpoint_state.total_batches,
                    batch_size=checkpoint_state.batch_size,
                    completed_batches=completed,
                    batch_markdown=state_dict,
                    created_at=checkpoint_state.created_at,
                    updated_at=ts,
                )
                self.checkpoint_store.save(checkpoint_state)

        full_markdown = "\n\n<!-- pagebreak -->\n\n".join(all_markdown_parts)

        # 3) Clean
        yield ProgressEvent(phase="cleaning", message="Pulizia testo OCR…")
        cleaned = clean_text(full_markdown)

        # 4) Crop immagini
        yield ProgressEvent(
            phase="cropping",
            message=f"Ritagliate {len(saved_crops)} immagini",
            current=len(saved_crops), total=len(saved_crops), percent=100.0,
        )

        # 5) WebP optimization
        if self.eink_optimize and saved_crops:
            yield ProgressEvent(phase="optimizing", message="Ottimizzazione WebP…")
            webp_dir = ingest_result.output_dir / "webp"
            webp_paths = optimize_batch(saved_crops, webp_dir)
            final_images = webp_paths
        else:
            final_images = saved_crops

        # 6) Compile EPUB
        yield ProgressEvent(phase="compiling", message="Compilazione EPUB3…")
        # BUG HUNT: la pipeline in passato sovrascriveva sempre la cover
        # con la prima pagina del PDF, ignorando ``metadata.cover_image``
        # impostato dall'utente (es. da UI Gradio). Centralizzato in
        # :meth:`resolve_cover_image` per consentire override espliciti
        # e garantire copertura dei test di regressione.
        cover_image = self.resolve_cover_image(self.metadata, ingest_result)
        result_path = build_epub(
            markdown=cleaned,
            images=final_images,
            metadata=self.metadata,
            output_path=output_epub,
            cover_image=cover_image,
        )

        elapsed = time.perf_counter() - start
        result = PipelineResult(
            output_path=result_path,
            pages_processed=all_pages_processed,
            images_extracted=len(saved_crops),
            total_seconds=elapsed,
            markdown_chars=len(cleaned),
            extra={
                "rendered_pages": total_pages,
                "images_used_in_epub": len(final_images),
                "dpi": self.dpi,
                "quantization": self.inference_config.quantization.value,
                            # Espone il markdown pulito per test introspezione
                            # (issue #10: verifica emissione <figure>/<figcaption>).
                            "cleaned_markdown": cleaned,
                        },
        )
        yield ProgressEvent(
            phase="done",
            message=f"Fatto in {elapsed:.1f}s — EPUB: {result_path}",
            current=total_pages, total=total_pages, percent=100.0,
            extra={"result": result.__dict__, "output": str(result_path)},
        )


__all__ = [
    "CheckpointMismatchError",
    "ModelNotFoundError",
    "Pipeline",
    "PipelineCancelledError",
    "PipelineResult",
    "ProgressEvent",
    "check_model_available",
]
