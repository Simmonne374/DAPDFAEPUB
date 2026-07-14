"""Modulo 1 — PDF Ingest & Normalization.

Usa PyMuPDF (``fitz``) per renderizzare ogni pagina di un PDF in due versioni:

* **300 DPI** (qualità archiviatica) → usata dal modulo di post-processing
  per ritagliare le immagini nelle coordinate reali dei pixel originali.
* **1024×1024 normalizzata** → risoluzione nativa del DeepEncoder di Baidu
  Unlimited-OCR (corrisponde a 256 token visivi per pagina, paper §3.3).

Vengono mantenuti solo i path su disco: le PNG a 300 DPI possono pesare
1–5 MB l'una, tenere tutto in RAM centuplica i consumi.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pymupdf as fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class RenderedPage:
    """Una singola pagina PDF renderizzata in due risoluzioni.

    Attributes:
        page_num: Numero 1-based della pagina nel PDF.
        width_pt: Larghezza della pagina in punti tipografici (per debug).
        height_pt: Altezza della pagina in punti tipografici.
        original_path: PNG a 300 DPI (o comunque il dpi specificato).
        normalized_path: PNG 1024×1024 normalizzata per l'inferenza.
    """

    page_num: int
    width_pt: float
    height_pt: float
    original_path: Path
    normalized_path: Path
    extra_paths: list[Path] = field(default_factory=list)


@dataclass
class IngestResult:
    """Risultato del rendering di un intero PDF.

    Attributes:
        source_pdf: Path del file PDF sorgente.
        output_dir: Cartella di lavoro creata (contiene le PNG).
        pages: Lista ordinata di :class:`RenderedPage`.
    """

    source_pdf: Path
    output_dir: Path
    pages: list[RenderedPage]

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.pages)

    def __iter__(self) -> Iterator[RenderedPage]:  # pragma: no cover
        return iter(self.pages)


def _normalize_to_square(pil_image: Image.Image, target_size: int) -> Image.Image:
    """Ridimensiona una pagina a un quadrato ``target_size x target_size``.

    Mantiene l'aspect ratio aggiungendo padding bianco (i PDF sono rari
    che siano quadrati; il padding bianco è la scelta usata dal paper
    di DeepSeek-OCR per la fase di pre-training).
    """
    pil_image = pil_image.convert("RGB")
    w, h = pil_image.size

    # Scala l'immagine per riempire il lato lungo mantenendo aspect ratio
    scale = target_size / max(w, h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Canvas quadrato bianco
    canvas = Image.new("RGB", (target_size, target_size), (255, 255, 255))
    paste_x = (target_size - new_w) // 2
    paste_y = (target_size - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y))
    return canvas


def render_pdf(
    input_pdf: str | os.PathLike,
    output_dir: str | os.PathLike | None = None,
    *,
    dpi: int = 300,
    target_size: int = 1024,
    keep_rendered: bool = True,
) -> IngestResult:
    """Renderizza un PDF in pagine PNG a due risoluzioni.

    Args:
        input_pdf: Path al PDF sorgente.
        output_dir: Cartella di destinazione per le PNG. Se ``None``, ne
            viene creata una temporanea che il chiamante può ripulire con
            :meth:`shutil.rmtree` quando ha finito.
        dpi: Risoluzione di rendering "alta qualità" (default 300, come da
            piano). Usata per il crop finale.
        target_size: Lato del quadrato in pixel per la versione normalizzata
            che verrà passata a Unlimited-OCR (default 1024, nativo del
            DeepEncoder).
        keep_rendered: Se ``False`` e ``output_dir`` era ``None``, indica
            che la cartella temporanea verrà cancellata dal chiamante.

    Returns:
        :class:`IngestResult` con tutti i path delle immagini prodotte.

    Raises:
        FileNotFoundError: Se il PDF non esiste.
        RuntimeError: Se PyMuPDF non riesce ad aprire il PDF.
    """
    pdf_path = Path(input_pdf).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF non trovato: {pdf_path}")

    if output_dir is None:
        # Directory temporanea auto-pulente se non la si vuole tenere
        output_dir = Path(tempfile.mkdtemp(prefix="relictoepub_"))
    else:
        output_dir = Path(output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

    # Cartelle separate per 300 DPI e 1024px, ordinate come il PDF
    hires_dir = output_dir / "hires"
    model_dir = output_dir / "model_input"
    hires_dir.mkdir(exist_ok=True)
    model_dir.mkdir(exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # fitz alza eccezioni eterogenee
        raise RuntimeError(f"Impossibile aprire il PDF {pdf_path}: {exc}") from exc

    pages: list[RenderedPage] = []
    try:
        total = doc.page_count
        logger.info("PDF %s: %d pagine", pdf_path.name, total)

        zoom = dpi / 72.0  # PyMuPDF usa 72 DPI come unità di base
        matrix = fitz.Matrix(zoom, zoom)

        for page_idx in range(total):
            page = doc.load_page(page_idx)
            page_num = page_idx + 1
            width_pt, height_pt = page.rect.width, page.rect.height

            # PNG 300 DPI
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            hires_path = hires_dir / f"page_{page_num:04d}.png"
            pix.save(str(hires_path))

            # 1024×1024 normalizzata
            pil_hires = Image.open(hires_path)
            pil_norm = _normalize_to_square(pil_hires, target_size)
            norm_path = model_dir / f"page_{page_num:04d}.png"
            pil_norm.save(norm_path, optimize=True)

            pages.append(
                RenderedPage(
                    page_num=page_num,
                    width_pt=width_pt,
                    height_pt=height_pt,
                    original_path=hires_path,
                    normalized_path=norm_path,
                )
            )
            logger.debug(
                "Renderizzata pagina %d/%d: %dx%d pt → %s",
                page_num, total, int(width_pt), int(height_pt), hires_path.name,
            )
    finally:
        doc.close()

    logger.info(
        "Render completato: %d pagine → %s (dpi=%d, model=%dpx)",
        len(pages), output_dir, dpi, target_size,
    )
    return IngestResult(source_pdf=pdf_path, output_dir=output_dir, pages=pages)
