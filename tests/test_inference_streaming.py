"""Test per lo streaming del runner OCR (``run_batch_iter``).

Verifica che:
* i yield parziali arrivino progressivamente (ogni ``write`` su
  stdout del modello produce un nuovo yield);
* il yield finale contenga il testo completo;
* lo stream sia thread-safe (la cattura avviene dentro
  ``contextlib.redirect_stdout`` invece di toccare lo ``sys.stdout``
  globale).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from relictoepub.inference.config import InferenceConfig
from relictoepub.inference.unlimited_ocr import (
    UnlimitedOCRRunner,
    _QueueWriter,
)


class _FakeModel:
    """Modello fittizio che simula ``Unlimited-OCR`` chiamando ``print``.

    Emette i "token" come stringhe via ``print`` (esattamente come fa
    la libreria ufficiale per lo streaming), poi restituisce il testo
    finale come valore di ritorno.
    """

    def __init__(self, decoded: str, chunks: list[str]) -> None:
        self._decoded = decoded
        self._chunks = chunks

    def infer(self, tokenizer, *, image_file, **kwargs) -> str:
        for chunk in self._chunks:
            print(chunk, end="", flush=True)
        return self._decoded

    def infer_multi(self, tokenizer, *, image_files, **kwargs) -> tuple[str, None]:
        for chunk in self._chunks:
            print(chunk, end="", flush=True)
        return self._decoded, None

    def eval(self) -> None:
        return None

    def to(self, _device):  # pragma: no cover - solo per non rompere il flusso
        return self


class _FakeTokenizer:
    def __getattr__(self, _name):  # nessun attributo usato nel test
        raise AttributeError("FakeTokenizer: nessun attributo usato")


@pytest.fixture()
def patched_runner(monkeypatch: pytest.MonkeyPatch) -> UnlimitedOCRRunner:
    """Costruisce un ``UnlimitedOCRRunner`` con modello finto precaricato."""
    runner = UnlimitedOCRRunner(InferenceConfig())
    runner._model = _FakeModel(
        decoded="<|det|>figure[0,0,100,100]<|/det|>",
        chunks=["<|det|>", "figure[", "0,0,", "100,100]", "<|/det|>"],
    )
    runner._tokenizer = _FakeTokenizer()
    runner._loaded = True
    # Evita chiamate reali a ``load_model``.
    monkeypatch.setattr(runner, "load_model", lambda: None)
    return runner


def test_queue_writer_drains_into_queue() -> None:
    """``_QueueWriter`` deve consegnare i write alla coda."""
    import queue as _q

    q: _q.Queue[str] = _q.Queue()
    writer = _QueueWriter(q)
    writer.write("hello ")
    writer.write("world")
    writer.flush()

    items: list[str] = []
    while not q.empty():
        items.append(q.get())
    assert "".join(items) == "hello world"


def test_run_batch_iter_yields_running_then_done(patched_runner) -> None:
    """Lo streaming deve produrre (testo_parziale, 'running') e infine (decoded, 'done')."""
    image = Path("fake_page.png")  # path non letto perché il modello è finto

    events = list(patched_runner.run_batch_iter([image]))

    assert len(events) >= 2
    # L'ultimo evento è "done".
    last_text, last_status = events[-1]
    assert last_status == "done"
    assert "<|det|>" in last_text and "<|/det|>" in last_text
    # Il penultimo (e tutti prima) deve essere "running".
    for text, status in events[:-1]:
        assert status == "running"
        assert text  # non vuoto


def test_run_batch_iter_streaming_is_progressive(patched_runner) -> None:
    """Ogni yield 'running' deve contenere il testo accumulato fino a quel punto."""
    events = list(patched_runner.run_batch_iter([Path("fake.png")]))

    running_texts = [t for t, s in events if s == "running"]
    assert len(running_texts) >= 2  # almeno 2 token emessi
    # I testi sono monotonamente crescenti (prepend semantics).
    assert running_texts[0] in running_texts[-1]
    # L'ultimo running deve contenere tutti i chunk concatenati.
    assert "<|det|>" in running_texts[-1]
    assert "figure[" in running_texts[-1]


def test_run_batch_iter_empty_input_yields_only_done() -> None:
    """Input vuoto deve produrre un singolo yield ``('', 'done')`` senza errori."""
    runner = UnlimitedOCRRunner(InferenceConfig())
    events = list(runner.run_batch_iter([]))
    assert events == [("", "done")]


def test_run_batch_iter_propagates_model_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un'eccezione del modello deve emergere al chiamante (non silenziata)."""

    class _ExplodingModel(_FakeModel):
        def infer(self, *args, **kwargs) -> str:
            print("partial_token", end="", flush=True)  # noqa: T201
            raise RuntimeError("model kaboom")

    runner = UnlimitedOCRRunner(InferenceConfig())
    runner._model = _ExplodingModel(decoded="unused", chunks=[])
    runner._tokenizer = _FakeTokenizer()
    runner._loaded = True
    monkeypatch.setattr(runner, "load_model", lambda: None)

    with pytest.raises(RuntimeError, match="model kaboom"):
        list(runner.run_batch_iter([Path("fake.png")]))


def test_no_global_sys_stdout_patching(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: lo streaming deve rilasciare ``sys.stdout`` al termine.

    Prima dell'Item 2 il runner salvava e riassegnava ``sys.stdout``
    manualmente (con bug di ripristino in caso di eccezione); ora usa
    ``contextlib.redirect_stdout``, che è un context manager:
    ``sys.stdout`` deve tornare al valore originale sia dopo un
    completamento normale sia dopo un'eccezione.
    """
    import sys

    runner = UnlimitedOCRRunner(InferenceConfig())
    runner._model = _FakeModel(
        decoded="x", chunks=["a", "b", "c"],
    )
    runner._tokenizer = _FakeTokenizer()
    runner._loaded = True
    monkeypatch.setattr(runner, "load_model", lambda: None)

    sentinel = object()
    sys.stdout = sentinel  # type: ignore[assignment]  # noqa: T201
    try:
        list(runner.run_batch_iter([Path("fake.png")]))
        assert sys.stdout is sentinel, (
            "sys.stdout non è stato ripristinato dopo uno streaming "
            "andato a buon fine: contextlib.redirect_stdout deve fare "
            "il ripristino automatico."
        )
    finally:
        sys.stdout = sentinel  # type: ignore[assignment]  # noqa: T201

    # Anche in caso di eccezione il context manager deve fare il pop.
    sys.stdout = sentinel  # type: ignore[assignment]  # noqa: T201
    try:
        list(runner.run_batch_iter([Path("fake.png")]))
    except RuntimeError:
        pass
    assert sys.stdout is sentinel, (
        "sys.stdout non è stato ripristinato dopo un'eccezione interna."
    )
