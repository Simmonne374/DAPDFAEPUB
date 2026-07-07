"""Modulo 2 — Configurazione di inferenza per Unlimited-OCR.

I parametri qui sono il risultato di tre fonti:

1. Paper arXiv:2606.23050 (Baidu, 2026) §3.4 e §4.2
2. HuggingFace model card di ``baidu/Unlimited-OCR``
3. Configurazione di DeepSeek-OCR (la base da cui Unlimited-OCR parte)

Tutto è centralizzato in :class:`InferenceConfig` così che la pipeline
(o la UI) possano modificare solo i parametri che hanno senso per
l'utente (es. ``batch_size``), senza toccare quelli del paper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class QuantizationMode(str, Enum):
    """Modalità di quantizzazione del modello.

    * ``none``: pesi in BF16/FP16 nativi (~6 GB, solo GPU con ≥12 GB)
    * ``int8``: bitsandbytes LLM.int8() (~3 GB, GPU entry-level)
    * ``int4``: bitsandbytes NF4 — consigliato per GTX 1080 Ti 11 GB
    """

    NONE = "none"
    INT8 = "int8"
    INT4 = "int4"


@dataclass
class InferenceConfig:
    """Configurazione di inferenza di Unlimited-OCR.

    Attributes:
        model_id: ID HuggingFace del modello, o path locale a un checkpoint
            scaricato. Default: ``baidu/Unlimited-OCR`` (ufficiale).
        device: ``"cuda"``, ``"cpu"``, o ``"auto"``. ``auto`` seleziona
            CUDA se disponibile.
        dtype: Precisione di calcolo quando la quantizzazione è disattivata.
            Default BF16 (richiede Ampere+, fallback a FP16 altrove).
        quantization: Modalità di quantizzazione bitsandbytes.
        pages_per_batch: Pagine PDF passate in un singolo forward pass
            (un batch contiene ``pages_per_batch`` immagini). Il paper
            raccomanda 20–30 per stare sotto i 32K token.
        max_new_tokens: Limite massimo di token generati. Il paper usa
            32K di context, ma per un singolo libro EPUB raramente servono
            così tanti: 8192 è un buon default.
        prompt_template: Prompt di sistema da passare al modello. Dal paper,
            deve contenere ``<image>document parsing.``.
        image_size: Dimensione lato del quadrato passato al modello
            (paper §3.3 — Base mode = 1024).
        base_size: Finestra del DeepEncoder per la modalità multi-page.
        ngram_no_repeat_size: Vincolo n-gram per evitare ripetizioni
            (dalla HuggingFace model card: 35).
        ngram_window_multi: Finestra per il vincolo n-gram in modalità
            multi-page (dalla HF model card: 1024).
        skip_special_tokens: Se ``False``, conserva i tag di coordinate
            e i delimitatori ``<page>`` (fondamentale per la pipeline
            di post-processing).
        cache_dir: Cartella per la cache dei modelli HuggingFace. Default
            ``~/.cache/huggingface``.
    """

    # Modello
    model_id: str = "baidu/Unlimited-OCR"
    cache_dir: Path | None = None

    # Hardware
    device: str = "auto"
    dtype: str = "bfloat16"
    quantization: QuantizationMode = QuantizationMode.INT4

    # Batching
    pages_per_batch: int = 20

    # Output
    max_new_tokens: int = 8192

    # Parametri del paper / HF model card
    prompt_template: str = "<image>document parsing."
    image_size: int = 1024
    base_size: int = 1024
    ngram_no_repeat_size: int = 35
    ngram_window_multi: int = 1024
    skip_special_tokens: bool = False

    # Soglie per auto-decisione
    min_gpu_memory_gb: float = 8.0  # sotto soglia, CPU fallback

    def resolve_device(self) -> str:
        """Restituisce il device effettivo (``"cuda"`` o ``"cpu"``)."""
        if self.device != "auto":
            return self.device
        try:
            import torch
            if torch.cuda.is_available():
                free_gb = torch.cuda.mem_get_info()[0] / (1024**3)
                if free_gb >= self.min_gpu_memory_gb:
                    return "cuda"
        except ImportError:
            pass
        return "cpu"

    def to_dict(self) -> dict:
        """Serializza la config in un dict (Path → str, Enum → str)."""
        from dataclasses import asdict, is_dataclass
        if not is_dataclass(self):
            return dict(self.__dict__)
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Path):
                d[k] = str(v)
            elif isinstance(v, QuantizationMode):
                d[k] = v.value
        return d


# Parole chiave che Unlimited-OCR include nel vocabulary relativo
# al layout, utili per il post-processing del bbox parsing.
LAYOUT_KEYWORDS = {
    "image", "figure", "table", "caption", "header", "footer",
    "title", "subtitle", "heading", "paragraph", "list",
}


@dataclass
class OCRPageResult:
    """Risultato OCR di una singola pagina o batch.

    Attributes:
        markdown: Testo Markdown puro emesso dal modello.
        page_separators: Numero di tag ``<page>`` incontrati (per sapere
            quante pagine sono state effettivamente processate).
        raw_text: Testo grezzo con tutti i tag speciali (per debug).
    """

    markdown: str
    page_separators: int = 1
    raw_text: str = ""
    extra: dict = field(default_factory=dict)
