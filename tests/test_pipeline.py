"""Test per ``relictoepub.pipeline`` (Orchestratore).

Questi test non caricano il modello OCR reale (per evitare download di GB
e dipendenza da GPU). Viene usato un :class:`UnlimitedOCRRunner` mockato
che restituisce direttamente del markdown finto.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
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

    DEFAULT_MARKDOWN = (
        "# Capitolo Fake\n\n"
        "Testo OCR simulato per la pagina corrente.\n\n"
        "<|det|>figure[100, 100, 500, 500]<|/det|>\n"
    )

    def __init__(self, config: InferenceConfig, markdown: str | None = None) -> None:
        self.config = config
        self._markdown = markdown if markdown is not None else self.DEFAULT_MARKDOWN

    def run_batch_iter(self, image_paths):
        """Replica la firma del runner reale: yield di (testo, status)."""
        yield self._markdown, "running"
        yield self._markdown, "done"

    def run_batch(self, image_paths: list[Path]) -> MagicMock:
        result = MagicMock()
        result.markdown = self._markdown
        result.raw_text = self._markdown
        result.page_separators = len(image_paths)
        return result

    @staticmethod
    def _strip_image_tokens(text: str) -> str:
        """Stub del private method chiamato da ``pipeline.py``."""
        return text


def _patch_runner(monkeypatch: pytest.MonkeyPatch, *, markdown: str | None = None) -> None:
    """Sostituisce ``UnlimitedOCRRunner`` con :class:`FakeOCRRunner`.

    Args:
        markdown: Markdown finto custom (per test che emulano ``image`` +
            ``image_caption``, ecc.). Se ``None`` viene usato il default.
    """

    def _factory(config: InferenceConfig) -> FakeOCRRunner:
        return FakeOCRRunner(config, markdown=markdown)

    monkeypatch.setattr("relictoepub.pipeline.UnlimitedOCRRunner", _factory)


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


def test_pipeline_emits_figure_with_caption(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il pipeline deve accoppiare ``image`` + ``image_caption`` in un ``<figure>``.

    Bug storico (issue #10): la caption veniva strippata dalla regex difensiva
    e l'immagine era un ``<img>`` nudo. Con il refactor atteso:
    * il tag immagine è wrappato in ``<figure>``
    * la caption diventa ``<figcaption>`` dentro il ``<figure>``
    * la width è derivata dal bbox denormalizzato (non più ``(x2-x1)/10``)
    """
    markdown = (
        "# Capitolo con didascalia\n\n"
        "Testo OCR.\n\n"
        "<|det|>image[100, 100, 800, 700]<|/det|>\n"
        "<|det|>image_caption[120, 720, 780, 770]<|/det|>Figura 1: Diagramma.\n"
    )
    _patch_runner(monkeypatch, markdown=markdown)
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=1),
        dpi=150,
        target_size=512,
        max_pages_per_batch=1,
        eink_optimize=False,
        metadata=BookMetadata(title="T"),
    )
    out = tmp_path / "fig.epub"
    result = pipeline.run(sample_pdf, out, progress_callback=lambda e: None)
    cleaned = result.extra["cleaned_markdown"]

    # <figure> wrapper presente
    assert "<figure" in cleaned and "</figure>" in cleaned
    # <figcaption> con il testo della caption
    assert "<figcaption" in cleaned and "</figcaption>" in cleaned
    assert "Figura 1: Diagramma." in cleaned
    # <img> dentro <figure>, non più nudo
    assert "<img " in cleaned
    # Il tag caption residuo non deve essere rimasto letteralmente
    assert "<|det|>" not in cleaned
    # Width % coerente: il bbox denormalizzato su una pagina A4 150 DPI
    # (1240×1754 px) copre ~700 pixel reali → ~56%, ma clamped a [25, 100].
    assert "width:" in cleaned


def test_pipeline_orphan_caption_preserved(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una caption senza immagine precedente deve restare nel testo.

    Il refactor deve poter distinguere tra:
    * ``image`` (o ``figure``/``table``) seguita da caption → ``<figcaption>``
    * ``image_caption`` orfana (es. testo puro) → mantenuta come testo
    """
    markdown = (
        "# Capitolo senza immagine\n\n"
        "Una caption solitaria: <|det|>image_caption[100, 100, 500, 200]<|/det|>appare qui.\n"
    )
    _patch_runner(monkeypatch, markdown=markdown)
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=1),
        dpi=150,
        target_size=512,
        max_pages_per_batch=1,
        eink_optimize=False,
        metadata=BookMetadata(title="T"),
    )
    out = tmp_path / "orphan.epub"
    result = pipeline.run(sample_pdf, out, progress_callback=lambda e: None)
    cleaned = result.extra["cleaned_markdown"]

    # Non c'è <figure> perché non c'è immagine
    assert "<figure>" not in cleaned
    assert "<figcaption>" not in cleaned
    # La caption orfana è preservata come testo (il tag det viene strippato)
    assert "appare qui" in cleaned
    assert "<|det|>" not in cleaned


def test_pipeline_width_pct_not_constant_div10(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il ``width:N%`` deve variare in base al bbox, non essere sempre ``(x2-x1)/10``.

    Bug storico: la formula era ``(x2-x1)/10`` sulle coordinate [0,1000],
    che per un bbox full-page dava sempre ~73% (su A4 portrait) indipendentemente
    dal vero rapporto d'aspetto del bbox. Ora il valore deve dipendere
    dalla dimensione **denormalizzata** del bbox.
    """
    # Due bbox con dimensioni normalizzate diverse (la prima piccola, la
    # seconda full-page). Con il bug, i width_pct sarebbero (300/10=30) e
    # (1000/10=100). Con il fix, sono molto diversi (denormalizzati).
    markdown = (
        "# Capitolo\n\n"
        "<|det|>image[100, 100, 400, 400]<|/det|>\n"
        "<|det|>image[0, 0, 1000, 1000]<|/det|>\n"
    )
    _patch_runner(monkeypatch, markdown=markdown)
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=1),
        dpi=150,
        target_size=512,
        max_pages_per_batch=1,
        eink_optimize=False,
        metadata=BookMetadata(title="T"),
    )
    out = tmp_path / "w.epub"
    result = pipeline.run(sample_pdf, out, progress_callback=lambda e: None)
    cleaned = result.extra["cleaned_markdown"]

    # Estrai tutti i "width:N.N%" presenti nel markdown
    import re as _re
    widths = [
        float(m.group(1))
        for m in _re.finditer(r"width:([\d.]+)%", cleaned)
    ]
    # Almeno due immagini → almeno due width dichiarate
    assert len(widths) >= 2
    # Le due width devono essere diverse (la prima è più piccola della seconda)
    assert widths[0] < widths[1], (
        f"Le width devono differire: prima={widths[0]}, seconda={widths[1]}"
    )
    # E la prima NON deve essere 30 (vecchio bug).
    assert widths[0] != 30.0