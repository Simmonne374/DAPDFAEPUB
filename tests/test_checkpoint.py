"""Test per :mod:`relictoepub.checkpoint`.

Copre:
1. ``compute_pdf_sha256`` produce hash deterministico e identico per byte uguali.
2. ``CheckpointState.to_dict``/``from_dict`` round-trip.
3. ``CheckpointStore.save``/``load`` round-trip (inclusi edge case).
4. ``CheckpointStore.save`` è atomico: simuliamo ``os.kill`` mid-write e
   verifichiamo che lo stato precedente sia ancora intatto.
5. ``CheckpointMismatchError`` sollevata da ``Pipeline`` quando SHA combacia ≠.
6. ``Pipeline.run_iter`` con mock OCR: resume salta i batch già cached.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from relictoepub.checkpoint import (
    CHECKPOINT_FILENAME,
    CHECKPOINT_VERSION,
    CheckpointMismatchError,
    CheckpointState,
    CheckpointStore,
    compute_pdf_sha256,
    new_checkpoint_state,
    resolve_checkpoint_dir,
)
from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig
from relictoepub.ingest import IngestResult, RenderedPage
from relictoepub.pipeline import Pipeline

# -------------------------------------------------------------------
# 1) SHA256 deterministico
# -------------------------------------------------------------------


def test_compute_pdf_sha256_is_deterministic(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfake content for hashing\n%%EOF\n")

    a = compute_pdf_sha256(pdf)
    b = compute_pdf_sha256(pdf)
    assert a == b
    assert a.startswith("sha256:")
    # 64 char hex digest + "sha256:" prefix
    assert len(a) == len("sha256:") + 64


def test_compute_pdf_sha256_differs_per_content(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert compute_pdf_sha256(a) != compute_pdf_sha256(b)


def test_compute_pdf_sha256_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        compute_pdf_sha256(tmp_path / "ghost.pdf")


# -------------------------------------------------------------------
# 2) CheckpointState round-trip
# -------------------------------------------------------------------


def test_checkpoint_state_roundtrip(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\ndata\n%%EOF\n")
    st = new_checkpoint_state(pdf, total_batches=10, batch_size=5)
    d = st.to_dict()
    assert d["version"] == CHECKPOINT_VERSION
    assert d["total_batches"] == 10
    assert d["batch_markdown"] == {}
    assert d["completed_batches"] == []
    st2 = CheckpointState.from_dict(d)
    assert st2 == st


def test_checkpoint_state_rejects_unknown_version(tmp_path: Path) -> None:
    d = {
        "version": 999,
        "source_pdf_sha256": "sha256:abc",
        "source_pdf_size_bytes": 1,
        "total_batches": 1,
        "batch_size": 1,
        "completed_batches": [],
        "batch_markdown": {},
        "created_at": "",
        "updated_at": "",
    }
    with pytest.raises(ValueError, match="Checkpoint version"):
        CheckpointState.from_dict(d)


# -------------------------------------------------------------------
# 3) CheckpointStore save/load
# -------------------------------------------------------------------


def test_store_save_load_roundtrip(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ckpt")
    assert not store.exists()
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")
    st = new_checkpoint_state(pdf, total_batches=3, batch_size=2)
    st = replace(
        st,
        completed_batches=[0, 1],
        batch_markdown={"0": "# Cap 1\nTesto\n", "1": "# Cap 2\n"},
    )
    store.save(st)
    assert store.exists()
    assert store.path == tmp_path / "ckpt" / CHECKPOINT_FILENAME
    loaded = store.load()
    assert loaded == st


def test_store_load_missing_returns_none(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "nope")
    assert store.load() is None


def test_store_load_corrupt_returns_none(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ckpt")
    store.directory.mkdir(parents=True)
    store.path.write_text("{not valid json", encoding="utf-8")
    assert store.load() is None


def test_store_clear_idempotent(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ckpt")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")
    store.save(new_checkpoint_state(pdf, total_batches=1, batch_size=1))
    assert store.exists()
    store.clear()
    assert not store.exists()
    # Idempotente: chiamare due volte non esplode.
    store.clear()


def test_resolve_checkpoint_dir(tmp_path: Path) -> None:
    pdf = tmp_path / "mybook.pdf"
    d = resolve_checkpoint_dir(pdf)
    assert d == tmp_path / ".relictoepub_checkpoints"


# -------------------------------------------------------------------
# 4) Atomic write sotto crash simulato
# -------------------------------------------------------------------


def test_atomic_write_preserves_prior_state_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se ``os.replace`` fallisce (es. SIGKILL mid-write), il vecchio
    checkpoint deve restare leggibile.
    """
    store = CheckpointStore(tmp_path / "ckpt")
    store.directory.mkdir(parents=True)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    # Primo save buono
    st1 = new_checkpoint_state(pdf, total_batches=2, batch_size=2)
    store.save(st1)
    assert store.load() == st1

    # Patch os.replace per simulare crash prima del rename
    boom_count = {"n": 0}

    def fake_replace(src, dst):
        boom_count["n"] += 1
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr("os.replace", fake_replace)
    # Tentativo di save durante crash
    st2 = replace(st1, completed_batches=[0], total_batches=2)
    with pytest.raises(OSError):
        store.save(st2)

    assert boom_count["n"] == 1

    # Lo stato precedente deve essere ancora intatto
    monkeypatch.undo()  # ripristina os.replace
    assert store.load() == st1


def test_concurrent_save_load_is_safe(tmp_path: Path) -> None:
    """Verifica thread-safety di base: save in thread multipli non corrompe."""
    import threading

    store = CheckpointStore(tmp_path / "ckpt")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")
    base = new_checkpoint_state(pdf, total_batches=10, batch_size=2)

    errors: list[Exception] = []

    def writer(i: int) -> None:
        try:
            s = replace(base, completed_batches=list(range(i + 1)))
            for _ in range(5):
                store.save(s)
        except OSError as exc:  # pragma: no cover - error path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # L'ultimo save deve essere uno stato valido (non un JSON troncato)
    loaded = store.load()
    assert loaded is not None
    assert loaded.source_pdf_sha256 == base.source_pdf_sha256


# -------------------------------------------------------------------
# 5) Pipeline.run_iter + checkpoint: mismatch detection
# -------------------------------------------------------------------


def _fake_ingest_result(tmp_path: Path, n_pages: int) -> IngestResult:
    """Crea un ``IngestResult`` fittizio con N pagine."""
    out_dir = tmp_path / "render"
    out_dir.mkdir(exist_ok=True)
    pages = []
    for i in range(1, n_pages + 1):
        png = out_dir / f"page_{i:04d}.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        norm = out_dir / f"model_{i:04d}.png"
        norm.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        pages.append(
            RenderedPage(
                page_num=i,
                width_pt=100.0, height_pt=200.0,
                original_path=png, normalized_path=norm,
            )
        )
    return IngestResult(
        source_pdf=tmp_path / "fake.pdf",
        output_dir=out_dir,
        pages=pages,
    )


def test_pipeline_checkpoint_mismatch_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se il checkpoint esiste ma appartiene a un PDF diverso, pipeline solleva
    CheckpointMismatchError."""
    pdf_a = tmp_path / "a.pdf"
    pdf_a.write_bytes(b"%PDF-1.4\nprimo pdf\n%%EOF\n")
    pdf_b = tmp_path / "b.pdf"
    pdf_b.write_bytes(b"%PDF-1.4\nsecondo pdf\n%%EOF\n")

    # Checkpoint scritto per pdf_a
    store = CheckpointStore(resolve_checkpoint_dir(pdf_a))
    state = new_checkpoint_state(
        pdf_a, total_batches=2, batch_size=3,
    )
    store.save(state)
    assert store.exists()

    # Patch ingest.render_pdf per non dipendere dal PDF vero
    def fake_render(input_pdf, **kwargs):
        return _fake_ingest_result(tmp_path, n_pages=3)

    monkeypatch.setattr("relictoepub.pipeline.render_pdf", fake_render)

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=3),
        max_pages_per_batch=3,
        eink_optimize=False,
        metadata=BookMetadata(title="T"),
        checkpoint_store=store,
    )
    # La pipeline deve controllare SHA del PDF passato, non quello originale
    with pytest.raises(CheckpointMismatchError, match="appartiene a un PDF diverso"):
        list(pipeline.run_iter(pdf_b, tmp_path / "out.epub"))


def test_pipeline_skip_cached_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il resume deve saltare i batch cached e processare solo quelli mancanti."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfake\n%%EOF\n")
    n_pages = 4
    n_calls = {"ocr": 0}

    # Mock OCR runner che conta le invocazioni
    class CountingOCR:
        def __init__(self, cfg: InferenceConfig) -> None:
            pass

        def run_batch_iter(self, paths):
            n_calls["ocr"] += 1
            yield "# Md\nTest\n", "running"
            yield "# Md\nTest\n", "done"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", CountingOCR,
    )
    monkeypatch.setattr(
        "relictoepub.pipeline.render_pdf",
        lambda *a, **kw: _fake_ingest_result(tmp_path, n_pages=n_pages),
    )

    # Crea checkpoint con 1 batch (su 2 totali) già completato
    store = CheckpointStore(resolve_checkpoint_dir(pdf))
    state = new_checkpoint_state(
        pdf, total_batches=2, batch_size=2,
    )
    state = replace(
        state,
        completed_batches=[0],
        batch_markdown={"0": "# Md cached\n"},
    )
    store.save(state)

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="X"),
        checkpoint_store=store,
    )

    list(pipeline.run_iter(pdf, tmp_path / "out.epub"))

    # Solo il batch non cached (idx 1) deve aver chiamato OCR
    assert n_calls["ocr"] == 1


def test_pipeline_no_checkpoint_no_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Senza checkpoint_store, la pipeline gira normalmente (regression test)."""
    pdf = tmp_path / "y.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")
    n_calls = {"ocr": 0}

    class CountingOCR:
        def __init__(self, cfg: InferenceConfig) -> None:
            pass

        def run_batch_iter(self, paths):
            n_calls["ocr"] += 1
            yield "# Md\n", "running"
            yield "# Md\n", "done"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", CountingOCR,
    )
    monkeypatch.setattr(
        "relictoepub.pipeline.render_pdf",
        lambda *a, **kw: _fake_ingest_result(tmp_path, n_pages=4),
    )

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="Y"),
        # checkpoint_store=None di default
    )
    list(pipeline.run_iter(pdf, tmp_path / "out.epub"))
    # 4 pages / batch_size 2 = 2 invocazioni OCR
    assert n_calls["ocr"] == 2
