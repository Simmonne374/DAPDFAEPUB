"""Modulo 4 — Ottimizzazione immagini per display E-ink.

I display E-ink (Kindle, Kobo, reMarkable) lavorano al meglio con:

* Immagini **8-bit grayscale** (16 livelli sono il vero sweet-spot
  per il prezzo, ma il formato WebP accetta 8 bit senza penalizzazioni).
* Contrasto spinto (gli E-ink hanno un rapporto di contrasto modesto
  sulla scala di grigi, quindi boost del contrasto aiuta la leggibilità).
* Compressione **WebP** lossless o quasi-lossless: file ~3-5× più
  piccoli del PNG equivalente su foto/illustrazioni.

L'ottimizzazione è opzionale ma raccomandata per EPUB destinati a E-ink.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

logger = logging.getLogger(__name__)


def optimize_for_eink(
    image_path: str | Path,
    output_path: str | Path | None = None,
    *,
    grayscale: bool = True,
    contrast_factor: float = 1.25,
    auto_contrast: bool = True,
    webp_quality: int = 80,
    webp_lossless: bool = False,
) -> Path:
    """Ottimizza un'immagine per un display E-ink.

    Args:
        image_path: Sorgente (PNG a 300 DPI o altro).
        output_path: Dove salvare. Se ``None`` e l'input è ``.png``,
            viene salvato come ``.webp`` accanto al file sorgente.
        grayscale: Se ``True`` (default), converte in scala di grigi.
        contrast_factor: Moltiplicatore di contrasto (1.0 = invariato).
        auto_contrast: Se ``True``, applica :func:`ImageOps.autocontrast`
            prima del boost per normalizzare l'istogramma.
        webp_quality: Qualità WebP (0-100). Ignorato se ``lossless=True``.
        webp_lossless: Se ``True``, usa WebP lossless.

    Returns:
        Il :class:`Path` del file ottimizzato.
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Immagine mancante: {image_path}")

    if output_path is None:
        output_path = image_path.with_suffix(".webp")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(image_path) as img:
        # Lavora su una copia per non toccare l'originale aperto
        work = img.copy()

        if grayscale and work.mode != "L":
            # "L" = 8-bit grayscale
            work = work.convert("L")

        if auto_contrast:
            work = ImageOps.autocontrast(work, cutoff=1)

        if contrast_factor != 1.0:
            enhancer = ImageEnhance.Contrast(work)
            work = enhancer.enhance(contrast_factor)

        save_kwargs = {
            "format": "WEBP",
            "lossless": webp_lossless,
        }
        if not webp_lossless:
            save_kwargs["quality"] = webp_quality
            save_kwargs["method"] = 6  # 0=fast, 6=slowest but best compression

        work.save(output_path, **save_kwargs)

    in_size = image_path.stat().st_size
    out_size = output_path.stat().st_size
    ratio = out_size / in_size if in_size else 0
    logger.info(
        "Ottimizzata %s → %s (%.1f KB → %.1f KB, ratio %.2f)",
        image_path.name, output_path.name,
        in_size / 1024, out_size / 1024, ratio,
    )
    return output_path


def optimize_batch(
    image_paths: Iterable[str | Path],
    output_dir: str | Path,
    *,
    max_workers: int | None = None,
    **kwargs,
) -> list[Path]:
    """Ottimizza una serie di immagini in parallelo, salvandole in ``output_dir``.

    Args:
        image_paths: Iterabile di path sorgente.
        output_dir: Cartella di destinazione (verrà creata).
        max_workers: Numero massimo di thread worker per l'ottimizzazione
            in parallelo. Se ``None``, usa la configurazione di default
            di :class:`ThreadPoolExecutor`.
        **kwargs: Parametri passati a :func:`optimize_for_eink`.

    Returns:
        Lista dei file WebP generati nell'ordine originale di ``image_paths``.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths_list = [Path(p) for p in image_paths]
    if not paths_list:
        return []

    # Helper function executed per image in thread pool
    def _worker(path: Path) -> Path:
        target = out / (path.stem + ".webp")
        return optimize_for_eink(path, target, **kwargs)

    # ThreadPoolExecutor is highly effective here because libwebp and Pillow C extensions
    # release the GIL during heavy image filtering and WEBP compression.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_worker, paths_list))

    return results


__all__ = ["optimize_batch", "optimize_for_eink"]
