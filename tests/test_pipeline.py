"""Test per ``relictoepub.pipeline`` (Orchestratore).

Questi test non caricano il modello OCR reale (per evitare download di GB
e dipendenza da GPU). Viene usato un :class:`UnlimitedOCRRunner` mockato
che restituisce direttamente del markdown finto.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig
from relictoepub.pipeline import Pipeline, ProgressEvent


# Skip se pandoc mancante (build_epub lo richiede)
pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc non installato (richiesto per build_epub)",
)


class FakeOCRRunner:
    """Mock del runner OCR: restituisce markdown finto + bbox di test."""

    def __init__(self, config: InferenceConfig) -> None:
        self.config = config

    def run_batch(self, image_paths: list[Path]) -> MagicMock:
        result = MagicMock()
        # Markdown con un'immagine fittizia via tag bbox
        result.markdown = (
            "# Capitolo Fake\n\n"
            "Testo OCR simulato per la pagina corrente.\n\n"
            "<|bbox|100|100|500|500|figure|>\n"
        )
        result.raw_text = result.markdown
        result.page_separators = len(image_paths)
        return result


def _patch_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sostituisce ``UnlimitedOCRRunner`` con :class:`FakeOCRRunner`."""
    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", FakeOCRRunner
    )


def test_pipeline_yields_all_phases(sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La pipeline deve emettere eventi per ogni fase principale."""
    _patch_runner(monkeypatch)
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        dpi=200,
        target_size=512,
        max_pages_per_batch=2,
        eink_optimize=True,
        metadata=BookMetadata(title="Test", author="T"),
    )
    out = tmp_path / "out.epub"
    events = list(pipeline.run_iter(sample_pdf, out))

    phases = {e.phase for e in events}
    # Deve contenere tutte le fasi previste
    for expected in ("rendering", "ocr", "cleaning", "cropping", "optimizing", "compiling", "done"):
        assert expected in phases, f"Fase mancante: {expected}"

    # L'evento "done" deve trasportare il risultato
    done = next(e for e in events if e.phase == "done")
    assert done.extra.get("result") is not None
    assert "output" in done.extra


def test_pipeline_run_returns_pipeline_result(sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``Pipeline.run`` (sync) deve tornare un ``PipelineResult`` valido."""
    _patch_runner(monkeypatch)
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=3),
        dpi=150,
        target_size=512,
        max_pages_per_batch=3,
        eink_optimize=False,  # disabilitato per velocità
        metadata=BookMetadata(title="T"),
    )
    out = tmp_path / "out.epub"
    result = pipeline.run(sample_pdf, out, progress_callback=lambda e: None)

    assert result.output_path == out
    assert out.is_file()
    assert result.pages_processed >= 1
    assert result.markdown_chars > 0
    # EPUB è uno ZIP valido
    with zipfile.ZipFile(out) as zf:
        assert "mimetype" in zf.namelist()


def test_pipeline_collects_progress_events(sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il progress callback riceve tutti gli eventi."""
    _patch_runner(monkeypatch)
    seen: list[ProgressEvent] = []

    def collect(event: ProgressEvent) -> None:
        seen.append(event)

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        dpi=150,
        target_size=512,
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="X"),
    )
    out = tmp_path / "x.epub"
    pipeline.run(sample_pdf, out, progress_callback=collect)

    assert len(seen) > 0
    assert any(e.phase == "done" for e in seen)