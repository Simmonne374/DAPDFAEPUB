"""Test per i moduli di post-processing (3, 4, text-clean)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from relictoepub.postprocess.bbox_crop import (
    BBox,
    crop_image_from_bbox,
    denormalize_bbox,
    extract_bbox_tokens,
)
from relictoepub.postprocess.text_clean import clean_text, count_words
from relictoepub.postprocess.webp_optim import optimize_batch, optimize_for_eink


# ============================================================
# bbox_crop
# ============================================================

def test_bbox_from_string_valid() -> None:
    bbox = BBox.from_string("<|bbox|100|200|300|400|image|>")
    assert bbox.x_min == 100
    assert bbox.y_min == 200
    assert bbox.x_max == 300
    assert bbox.y_max == 400
    assert bbox.label == "image"


def test_bbox_from_string_no_label() -> None:
    bbox = BBox.from_string("<|bbox|0|0|500|500|>")
    assert bbox.label == ""


def test_bbox_from_string_malformed() -> None:
    with pytest.raises(ValueError):
        BBox.from_string("not a bbox")


def test_denormalize_bbox_basic() -> None:
    """1000-based normalizzata su immagine 1024x1024 -> pixel senza padding."""
    # L'immagine è 600x800.
    # Viene scalata a 768x1024 (scale=1.28).
    # Viene aggiunto padding X di (1024-768)/2 = 128
    # bbox su immagine intera: (125, 0, 875, 1000) in scala [0, 1000]
    bbox = BBox(125, 0, 875, 1000)
    pixel_box = denormalize_bbox(bbox, (600, 800))
    # Il clipping difensivo arrotonda in (0, 0, 600, 800)
    assert pixel_box == (0, 0, 600, 800)


def test_denormalize_bbox_clipped() -> None:
    """Le coordinate oltre l'immagine vengono clippate."""
    bbox = BBox(-500, -500, 1500, 1500)
    left, upper, right, lower = denormalize_bbox(bbox, (1000, 800))
    assert left == 0
    assert upper == 0
    assert right == 1000
    assert lower == 800


def test_crop_image_from_bbox_saves_file(sample_image: Path, tmp_path: Path) -> None:
    """Il crop salva un file valido e ritorna il path."""
    bbox = BBox(125, 0, 875, 1000)  # tutta l'immagine per 600x800
    out = tmp_path / "crop.png"
    result = crop_image_from_bbox(sample_image, bbox, output_path=out)
    assert result is not None
    assert result.is_file()
    with Image.open(result) as img:
        assert img.size == (600, 800)


def test_crop_image_too_small_returns_none(sample_image: Path, tmp_path: Path) -> None:
    """BBox microscopica → ``None`` per via di ``min_size``."""
    tiny = BBox(500, 500, 501, 501)
    result = crop_image_from_bbox(sample_image, tiny, output_path=tmp_path / "x.png")
    assert result is None


def test_extract_bbox_tokens_multiple() -> None:
    text = (
        "Capitolo primo\n"
        "<|bbox|10|20|300|400|figure|>\n"
        "Altro testo\n"
        "<|bbox|0|0|100|100|title|>\n"
    )
    bboxes = extract_bbox_tokens(text)
    assert len(bboxes) == 2
    assert bboxes[0].label == "figure"
    assert bboxes[1].label == "title"


def test_extract_bbox_tokens_invalid_skipped() -> None:
    text = "<|bbox|1|2|3|4|ok|> <|bbox|broken|stuff|here|>"
    bboxes = extract_bbox_tokens(text)
    # Solo il bbox ben formato sopravvive (l'altro è malformato)
    assert len(bboxes) == 1


# ============================================================
# webp_optim
# ============================================================

def test_optimize_for_eink_creates_webp(sample_image: Path, tmp_path: Path) -> None:
    out = tmp_path / "opt.webp"
    result = optimize_for_eink(sample_image, output_path=out)
    assert result == out
    assert out.is_file()
    # WebP lossy viene riletto da Pillow come RGB anche se i pixel sono
    # grayscale (l'encoder WebP salva internamente in YUV→RGB). Verifichiamo
    # che la conversione grayscale sia davvero avvenuta controllando i pixel.
    with Image.open(out) as img:
        px = img.getpixel((0, 0))
        if isinstance(px, tuple):
            r, g, b = px[:3]
            assert r == g == b, f"primo pixel non grayscale: {px}"
        else:
            # modalità "L" → singolo valore
            assert 0 <= px <= 255


def test_optimize_batch_returns_all_paths(sample_image: Path, tmp_path: Path) -> None:
    # Crea 3 immagini per il batch
    paths = []
    for i in range(3):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", (100, 100), (255, 255, 255)).save(p)
        paths.append(p)

    results = optimize_batch(paths, tmp_path / "out")
    assert len(results) == 3
    for r in results:
        assert r.suffix == ".webp"
        assert r.is_file()


# ============================================================
# text_clean
# ============================================================

def test_clean_text_hyphenation() -> None:
    """Il trattino di fine riga viene rimosso."""
    raw = "para-\ngraphia finale"
    cleaned = clean_text(raw)
    assert "paragrato" not in cleaned
    # la sillabazione è unita
    assert "paragraphia" in cleaned


def test_clean_text_quotes_normalization() -> None:
    raw = "‘ciao’ “mondo” «ciao»"
    cleaned = clean_text(raw, fix_hyphenation=False)
    assert "‘" not in cleaned
    assert "“" not in cleaned
    assert "«" not in cleaned


def test_clean_text_collapses_multiple_newlines() -> None:
    raw = "a\n\n\n\n\nb"
    cleaned = clean_text(raw, fix_hyphenation=False, normalize_quotes=False)
    assert "\n\n\n" not in cleaned
    assert cleaned == "a\n\nb"


def test_clean_text_empty() -> None:
    assert clean_text("") == ""


def test_count_words() -> None:
    assert count_words("uno due tre") == 3
    assert count_words("") == 0