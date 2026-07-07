"""Test per ``relictoepub.ingest`` (Modulo 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from relictoepub.ingest import RenderedPage, render_pdf


def test_render_pdf_creates_hires_and_normalized(sample_pdf: Path, tmp_path: Path) -> None:
    """``render_pdf`` deve produrre sia le PNG 300 DPI sia le 1024px normalizzate."""
    work = tmp_path / "work"
    result = render_pdf(sample_pdf, output_dir=work, dpi=300, target_size=1024)

    assert len(result) == 3
    assert result.output_dir == work
    assert (work / "hires").is_dir()
    assert (work / "model_input").is_dir()
    for page in result:
        assert isinstance(page, RenderedPage)
        assert page.original_path.is_file()
        assert page.normalized_path.is_file()
        # La versione normalizzata deve essere effettivamente 1024x1024
        from PIL import Image
        with Image.open(page.normalized_path) as img:
            assert img.size == (1024, 1024)


def test_render_pdf_nonexistent_raises(tmp_path: Path) -> None:
    """PDF mancante → ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        render_pdf(tmp_path / "ghost.pdf")


def test_render_pdf_uses_tmp_when_no_output_dir(sample_pdf: Path) -> None:
    """Se ``output_dir`` è ``None`` viene creata una cartella temporanea."""
    result = render_pdf(sample_pdf, output_dir=None, dpi=150, target_size=512)
    assert result.output_dir.exists()
    assert len(result.pages) == 3
    # Cleanup manuale (lo farebbe il chiamante)
    import shutil
    shutil.rmtree(result.output_dir)


def test_rendered_page_dimensions_consistent(sample_pdf: Path, tmp_path: Path) -> None:
    """Le dimensioni in pt devono essere positive e coerenti con un A4."""
    result = render_pdf(sample_pdf, output_dir=tmp_path / "w")
    for page in result:
        assert page.width_pt > 0
        assert page.height_pt > 0
        # A4 portrait
        assert page.width_pt < page.height_pt