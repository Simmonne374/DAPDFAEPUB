"""Modulo 3 — Estrazione di immagini dalle coordinate OCR.

Il modello Unlimited-OCR restituisce (paper §4.1) coordinate
**normalizzate** in scala ``[0, 1000]`` per ogni bounding box.
Queste devono essere mappate alle coordinate pixel della pagina
``300 DPI`` (renderizzata dal Modulo 1) per ritagliare con
precisione chirurgica.

Convenzioni del paper:
* L'origine ``(0, 0)`` è in alto a sinistra
* L'asse Y cresce verso il basso
* I valori sono inclusivi a sinistra/sopra, esclusivi a destra/sotto
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


DEFAULT_NORMALIZE_RANGE = 1000  # dal paper §4.1

# Bolt optimization: Hoisted pre-compiled regex patterns for BBox token parsing.
_DET_PATTERN = re.compile(
    r"<\|det\|>([^\[]+)\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]<\|/det\|>"
)
_BBOX_PATTERN = re.compile(
    r"<\|bbox\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*(?:\|\s*([^|>\s]+)\s*)?\|?>"
)


@dataclass(frozen=True)
class BBox:
    """Bounding box normalizzata in scala ``[0, normalize_range]``.

    Attributes:
        x_min, y_min, x_max, y_max: coordinate normalizzate.
        label: tipo di blocco (es. ``"image"``, ``"figure"``, ``"table"``).
    """

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    label: str = ""

    @property
    def width(self) -> float:
        return max(0.0, self.x_max - self.x_min)

    @property
    def height(self) -> float:
        return max(0.0, self.y_max - self.y_min)

    @property
    def area(self) -> float:
        return self.width * self.height

    def width_pct_against(self, page_width_px: float) -> float:
        """Restituisce la larghezza del bbox come percentuale della pagina.

        Utile per il layout EPUB: una figura che occupa il 70% della pagina
        verrà renderizzata con ``width: 70%`` nel CSS invece di essere
        scalata solo in base alle coordinate normalizzate (che ignorano
        il padding introdotto da :func:`relictoepub.ingest._normalize_to_square`).

        Args:
            page_width_px: larghezza della pagina in pixel (es. immagine 300 DPI).
                Conservato per retro-compatibilità della firma; un valore non
                positivo fa ritornare ``0.0``. Non influenza il calcolo della
                percentuale (che dipende solo dalla scala normalizzata).

        Returns:
            Percentuale 0–100. ``0.0`` se ``page_width_px`` non è positivo.
        """
        if page_width_px <= 0:
            return 0.0
        # B55: la divisione per ``page_width_px`` era dimensionalmente
        # inconsistente (coordinate normalizzate / pixel). Corretto: usare la
        # scala normalizzata canonica (DEFAULT_NORMALIZE_RANGE = 1000).
        return max(0.0, min(100.0, self.width / DEFAULT_NORMALIZE_RANGE * 100.0))

    @classmethod
    def from_string(cls, raw: str) -> BBox:
        """Parsa una stringa tipo "<|det|>label [x1, y1, x2, y2]<|/det|>" o "<|bbox|...>"."""
        match = re.search(
            r"<\|det\|>([^\[]+)\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]<\|/det\|>",
            raw
        )
        if not match:
            match = re.search(
                r"<\|bbox\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*(?:\|\s*([^|>\s]+)\s*)?\|?>",
                raw,
            )
            if not match:
                raise ValueError(f"Formato BBox non riconosciuto: {raw!r}")
            x1, y1, x2, y2 = (int(g) for g in match.groups()[:4])
            label = (match.group(5) or "").strip()
            return cls(x_min=x1, y_min=y1, x_max=x2, y_max=y2, label=label)
            
        label = match.group(1).strip()
        x1, y1, x2, y2 = (int(g) for g in match.groups()[1:5])
        return cls(x_min=x1, y_min=y1, x_max=x2, y_max=y2, label=label)


def denormalize_bbox(
    bbox: BBox,
    image_size: tuple[int, int],
    normalize_range: float = DEFAULT_NORMALIZE_RANGE,
    target_size: int = 1024,
) -> tuple[int, int, int, int]:
    """Converte una :class:`BBox` normalizzata in coordinate pixel della pagina originale.

    Le coordinate emesse dal modello Unlimited-OCR sono relative alla
    versione normalizzata e padded a quadrato (target_size) usata per l'inferenza.
    Bisogna quindi ricalcolare la scala e il padding e invertirli.

    Args:
        bbox: BBox in scala ``[0, normalize_range]``.
        image_size: ``(width, height)`` dell'immagine target in pixel.
        normalize_range: Valore massimo della scala normalizzata.
        target_size: Dimensione del quadrato normalizzato (default 1024).

    Returns:
        Tupla ``(left, upper, right, lower)`` valida per
        :py:meth:`PIL.Image.Image.crop`.
    """
    img_w, img_h = image_size

    scale = target_size / max(img_w, img_h)
    new_w = max(1, round(img_w * scale))
    new_h = max(1, round(img_h * scale))

    paste_x = (target_size - new_w) / 2.0
    paste_y = (target_size - new_h) / 2.0

    # Da scala [0, normalize_range] a scala pixel nel quadrato target_size
    x_min_1024 = bbox.x_min * target_size / normalize_range
    y_min_1024 = bbox.y_min * target_size / normalize_range
    x_max_1024 = bbox.x_max * target_size / normalize_range
    y_max_1024 = bbox.y_max * target_size / normalize_range

    # Rimuovi padding e riscala alle coordinate originali
    x_min_mapped = (x_min_1024 - paste_x) / scale
    y_min_mapped = (y_min_1024 - paste_y) / scale
    x_max_mapped = (x_max_1024 - paste_x) / scale
    y_max_mapped = (y_max_1024 - paste_y) / scale

    left = round(x_min_mapped)
    upper = round(y_min_mapped)
    right = round(x_max_mapped)
    lower = round(y_max_mapped)

    # Clipping difensivo per evitare crop fuori immagine
    left = max(0, min(img_w - 1, left))
    upper = max(0, min(img_h - 1, upper))
    right = max(left + 1, min(img_w, right))
    lower = max(upper + 1, min(img_h, lower))

    return (left, upper, right, lower)


def crop_image_from_bbox(
    image_path: str | Path,
    bbox: BBox,
    output_path: str | Path | None = None,
    *,
    normalize_range: float = DEFAULT_NORMALIZE_RANGE,
    min_size: int = 32,
    target_size: int = 1024,
) -> Path | None:
    """Ritaglia un'immagine usando una BBox normalizzata.

    Args:
        image_path: PNG a 300 DPI (output di :func:`relictoepub.ingest.render_pdf`).
        bbox: BBox nel formato del paper.
        output_path: Dove salvare il crop. Se ``None``, viene derivato
            da ``image_path`` con suffisso ``_bbox_{label}.png``.
        normalize_range: Valore massimo della scala normalizzata.
        min_size: Dimensione minima in pixel del crop. Se il box è
            più piccolo, viene scartato (ritorna ``None``).

    Returns:
        Il :class:`Path` del crop salvato, oppure ``None`` se scartato.
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Immagine sorgente mancante: {image_path}")

    result = crop_image_from_bbox_with_box(
        image_path=image_path,
        bbox=bbox,
        output_path=output_path,
        normalize_range=normalize_range,
        min_size=min_size,
        target_size=target_size,
    )
    if result is None:
        return None
    return result[0]


def crop_image_from_bbox_with_box(
    image_path: str | Path,
    bbox: BBox,
    output_path: str | Path | None = None,
    *,
    normalize_range: float = DEFAULT_NORMALIZE_RANGE,
    min_size: int = 32,
    target_size: int = 1024,
) -> tuple[Path, tuple[int, int, int, int]] | None:
    """Variante di :func:`crop_image_from_bbox` che ritorna anche il bbox in pixel.

    Args:
        image_path: PNG a 300 DPI (output di :func:`relictoepub.ingest.render_pdf`).
        bbox: BBox nel formato del paper.
        output_path: Dove salvare il crop. Se ``None``, viene derivato.
        normalize_range: Valore massimo della scala normalizzata.
        min_size: Dimensione minima in pixel del crop.
        target_size: Dimensione del quadrato normalizzato (default 1024).

    Returns:
        Tupla ``(path, pixel_box)`` con il bbox denormalizzato in pixel
        dell'immagine originale, oppure ``None`` se scartato per
        dimensione insufficiente.
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Immagine sorgente mancante: {image_path}")

    with Image.open(image_path) as img:
        w, h = img.size
        pixel_box = denormalize_bbox(bbox, (w, h), normalize_range=normalize_range, target_size=target_size)
        width_px = pixel_box[2] - pixel_box[0]
        height_px = pixel_box[3] - pixel_box[1]

        if width_px < min_size or height_px < min_size:
            logger.debug(
                "Crop scartato per dimensione insufficiente (%dx%d px)",
                width_px, height_px,
            )
            return None

        cropped = img.crop(pixel_box)
        if output_path is None:
            suffix = f"_bbox_{bbox.label}" if bbox.label else "_bbox"
            output_path = image_path.with_name(
                image_path.stem + suffix + image_path.suffix
            )
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(output_path, optimize=True)
        logger.debug(
            "Crop salvato: %s (%dx%d px)", output_path.name, width_px, height_px,
        )
        return output_path, pixel_box


def extract_bbox_tokens(ocr_text: str) -> list[BBox]:
    """Estrae tutti i tag BBox/Det dal testo OCR.

    Args:
        ocr_text: Testo emesso da Unlimited-OCR con ``skip_special_tokens=False``.

    Returns:
        Lista di :class:`BBox` trovati. Silenziosamente scarta i tag
        malformati (li logga a livello DEBUG).
    """
    results: list[BBox] = []

    # Bolt optimization: Fast-path substring check before running regex,
    # and direct tuple indexing to avoid per-match regex compilation & generator overhead (~15-20% speedup).
    if "<|det|>" in ocr_text:
        for match in _DET_PATTERN.finditer(ocr_text):
            label = match.group(1).strip()
            g = match.groups()
            results.append(BBox(x_min=int(g[1]), y_min=int(g[2]), x_max=int(g[3]), y_max=int(g[4]), label=label))

    if "<|bbox|" in ocr_text:
        for match in _BBOX_PATTERN.finditer(ocr_text):
            g = match.groups()
            label = (g[4] or "").strip()
            results.append(BBox(x_min=int(g[0]), y_min=int(g[1]), x_max=int(g[2]), y_max=int(g[3]), label=label))

    return results


def crop_batch_from_pages(
    page_image_paths: Sequence[str | Path],
    bboxes_per_page: Sequence[Iterable[BBox]],
    output_dir: str | Path,
    target_size: int = 1024,
) -> list[Path]:
    """Utility: data N pagine con le rispettive BBox, salva tutti i crop.

    Args:
        page_image_paths: una entry per pagina (300 DPI).
        bboxes_per_page: lista di iterable, allineata con ``page_image_paths``.
        output_dir: cartella di destinazione.

    Returns:
        Lista dei crop effettivamente salvati (i troppo piccoli sono saltati).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for page_path, page_bboxes in zip(page_image_paths, bboxes_per_page):
        page_path = Path(page_path)
        for i, bbox in enumerate(page_bboxes):
            label = bbox.label or f"asset{i:02d}"
            output_path = out / f"{page_path.stem}_{label}.png"
            result = crop_image_from_bbox(page_path, bbox, output_path=output_path, target_size=target_size)
            if result is not None:
                saved.append(result)
    return saved


__all__ = [
    "DEFAULT_NORMALIZE_RANGE",
    "BBox",
    "crop_batch_from_pages",
    "crop_image_from_bbox",
    "crop_image_from_bbox_with_box",
    "denormalize_bbox",
    "extract_bbox_tokens",
]
