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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image

logger = logging.getLogger(__name__)


DEFAULT_NORMALIZE_RANGE = 1000  # dal paper §4.1


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

    @classmethod
    def from_string(cls, raw: str) -> "BBox":
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
) -> tuple[int, int, int, int]:
    """Converte una :class:`BBox` normalizzata in coordinate pixel.

    Args:
        bbox: BBox in scala ``[0, normalize_range]``.
        image_size: ``(width, height)`` dell'immagine target in pixel.
        normalize_range: Valore massimo della scala normalizzata.

    Returns:
        Tupla ``(left, upper, right, lower)`` valida per
        :py:meth:`PIL.Image.Image.crop`.
    """
    img_w, img_h = image_size
    scale_x = img_w / normalize_range
    scale_y = img_h / normalize_range

    left = int(round(bbox.x_min * scale_x))
    upper = int(round(bbox.y_min * scale_y))
    right = int(round(bbox.x_max * scale_x))
    lower = int(round(bbox.y_max * scale_y))

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

    with Image.open(image_path) as img:
        w, h = img.size
        pixel_box = denormalize_bbox(bbox, (w, h), normalize_range=normalize_range)
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
        return output_path


def extract_bbox_tokens(ocr_text: str) -> list[BBox]:
    """Estrae tutti i tag BBox/Det dal testo OCR.

    Args:
        ocr_text: Testo emesso da Unlimited-OCR con ``skip_special_tokens=False``.

    Returns:
        Lista di :class:`BBox` trovati. Silenziosamente scarta i tag
        malformati (li logga a livello DEBUG).
    """
    results: list[BBox] = []
    
    # 1. Cerca tag <|det|>
    det_pattern = re.compile(
        r"<\|det\|>([^\[]+)\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]<\|/det\|>"
    )
    for match in det_pattern.finditer(ocr_text):
        label = match.group(1).strip()
        x1, y1, x2, y2 = (int(g) for g in match.groups()[1:5])
        results.append(BBox(x_min=x1, y_min=y1, x_max=x2, y_max=y2, label=label))
        
    # 2. Cerca tag <|bbox|> (fallback)
    bbox_pattern = re.compile(
        r"<\|bbox\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*(?:\|\s*([^|>\s]+)\s*)?\|?>"
    )
    for match in bbox_pattern.finditer(ocr_text):
        x1, y1, x2, y2 = (int(g) for g in match.groups()[:4])
        label = (match.group(5) or "").strip()
        results.append(BBox(x_min=x1, y_min=y1, x_max=x2, y_max=y2, label=label))
        
    return results


def crop_batch_from_pages(
    page_image_paths: Sequence[str | Path],
    bboxes_per_page: Sequence[Iterable[BBox]],
    output_dir: str | Path,
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
            result = crop_image_from_bbox(page_path, bbox, output_path=output_path)
            if result is not None:
                saved.append(result)
    return saved


__all__ = [
    "BBox",
    "DEFAULT_NORMALIZE_RANGE",
    "crop_image_from_bbox",
    "crop_batch_from_pages",
    "denormalize_bbox",
    "extract_bbox_tokens",
]
