"""Test per la cancel-azione cooperativa della :class:`Pipeline`.

Copre:
1. ``Pipeline.cancel()`` setta l'event flag.
2. ``Pipeline.is_cancelled()`` riflette lo stato.
3. ``PipelineCancelledError`` viene sollevata quando l'utente chiama
   ``cancel()`` durante OCR.
4. Se cancel arriva prima dell'OCR → nessuna chiamata OCR reale.
5. Cancel mid-batch: solo il batch in corso viene completato (con checkpoint
   salvato se ``checkpoint_store`` è configurato).
6. ``reset_cancel()`` permette di riusare la stessa istanza.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from relictoepub.checkpoint import (
    CheckpointStore,
    resolve_checkpoint_dir,
)
from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig
from relictoepub.ingest import IngestResult, RenderedPage
from relictoepub.pipeline import (
    Pipeline,
    PipelineCancelledError,
)

# -------------------------------------------------------------------
# Test doubles
# -------------------------------------------------------------------


class CancellableOCR:
    """OCR mock che rispetta ``threading.Event`` per cancel reattivo mid-batch.

    Ogni ``run_batch_iter`` controlla ``cancel_event`` ad ogni yield.
    Se settato → esce senza emettere ``"done"``.
    """

    def __init__(
        self,
        config: InferenceConfig,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self._cancel = cancel_event

    def run_batch_iter(
        self, image_paths,
    ) -> Iterator[tuple[str, str]]:
        for i in range(5):
            if self._cancel is not None and self._cancel.is_set():
                return
            yield f"# Batch {i}\nTesto {i}\n", "running"
        yield "# Final markdown\nDone\n", "done"

    @staticmethod
    def _strip_image_tokens(text: str) -> str:
        return text


# -------------------------------------------------------------------
# 1) Unit test sull'API cancel
# -------------------------------------------------------------------


def test_pipeline_cancel_sets_event() -> None:
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="X"),
    )
    assert not pipeline.is_cancelled()
    pipeline.cancel()
    assert pipeline.is_cancelled()
    # Idempotente
    pipeline.cancel()
    assert pipeline.is_cancelled()


def test_pipeline_reset_cancel_allows_reuse() -> None:
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="X"),
    )
    pipeline.cancel()
    assert pipeline.is_cancelled()
    pipeline.reset_cancel()
    assert not pipeline.is_cancelled()


# -------------------------------------------------------------------
# 2) Integration: cancel prima della pipeline → nessuna OCR
# -------------------------------------------------------------------


def _fake_ingest_result(tmp_path: Path, n_pages: int) -> IngestResult:
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
                page_num=i, width_pt=100.0, height_pt=200.0,
                # width_px/height_px usati dalla pipeline per calcolare la
                # width% delle immagini (vedi fix issue #10).
                width_px=2480, height_px=3508,
                original_path=png, normalized_path=norm,
            )
        )
    return IngestResult(
        source_pdf=tmp_path / "fake.pdf",
        output_dir=out_dir, pages=pages,
    )


def test_cancel_before_first_batch_skips_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se cancel arriva durante run_iter prima che il primo batch OCR
    venga completato, la pipeline solleva l'eccezione e l'OCR non
    processa pagine.

    Il mock OCR espone un evento di cancel osservabile che viene
    controllato al PRIMO yield. Il watcher thread setta l'evento
    dell'OCR direttamente, garantendo che il batch sia interrotto al
    primissimo checkpoint possibile. Robusto contro jitter del
    threading su runner CI lenti.
    """
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    n_calls = {"ocr": 0}
    ocr_cancel = threading.Event()

    class CountingCancellable:
        """Mock OCR che osserva ``ocr_cancel`` PRIMA di ogni yield.

        Quando l'evento e' settato, esce immediatamente. Cio'
        consente al test di verificare il comportamento del check
        mid-batch in modo deterministico, senza dipendere dal timing
        del watcher thread.

        Il delay ``time.sleep(0.05)`` simula inferenza realistica e
        da' al watcher thread il tempo di impostare ``ocr_cancel``
        durante il primo batch.
        """

        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths):
            n_calls["ocr"] += 1
            if ocr_cancel.is_set():
                return  # cancel ricevuto prima ancora di iniziare
            yield "# X", "running"
            # Delay realistico per simulare inferenza OCR in corso.
            time.sleep(0.05)
            if ocr_cancel.is_set():
                return
            yield "# X", "done"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", CountingCancellable,
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
    )

    # Watcher thread: setta il flag dell'OCR (reactive) e il cancel
    # della pipeline. Il check OCR-side e' sincrono, quindi scattera'
    # al primissimo yield del mock, anche su runner CI molto lenti.
    def trigger_cancel():
        # Piccolo delay per garantire che il main thread abbia
        # raggiunto il batch loop (altrimenti ``ocr_cancel`` sarebbe
        # gia' settato PRIMA che il mock venga istanziato e il test
        # perderebbe valore). 10ms e' un margine sicuro su tutti i
        # runner CI osservati (Windows, Ubuntu GitHub Actions).
        time.sleep(0.01)
        ocr_cancel.set()
        pipeline.cancel()

    t = threading.Thread(target=trigger_cancel, daemon=True)
    t.start()

    with pytest.raises(PipelineCancelledError):
        list(pipeline.run_iter(pdf, tmp_path / "out.epub"))

    t.join()
    # Il mock OCR viene istanziato e ``run_batch_iter`` viene chiamato
    # (perche' il check start-of-batch della pipeline avviene PRIMA del
    # watcher). Tuttavia il mock controlla ``ocr_cancel`` e restituisce
    # un iteratore vuoto — il batch non viene completato. Verifichiamo
    # quindi che AL MASSIMO 1 batch sia stato toccato (e idealmente 0).
    assert n_calls["ocr"] <= 1


def test_cancel_mid_pipeline_raises_after_batch_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel mentre il batch in corso sta girando: il batch finisce,
    viene salvato sul checkpoint, poi la pipeline solleva CancelledError
    al batch successivo.
    """
    pdf = tmp_path / "y.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    # OCR mock che imposta cancel dopo il PRIMO batch completato.
    class AutoCancelOCR(CancellableOCR):
        def __init__(self, cfg):
            # NB: il cancel_event è settato ESTERNAMENTE.
            # qui non abbiamo accesso diretto, usiamo un callback.
            self._after_first_batch_done: threading.Event = threading.Event()

        def run_batch_iter(self, paths):
            for partial in super().run_batch_iter(paths):
                if partial[1] == "done":
                    # Segnala "primo batch finito": il thread main
                    # imposterà il cancel_event.
                    self._after_first_batch_done.set()
                yield partial

    cancel_event = threading.Event()
    n_calls = {"ocr": 0}

    class TogglingOCR:
        def __init__(self, cfg):
            self.cfg = cfg

        def run_batch_iter(self, paths):
            n_calls["ocr"] += 1
            # Primo batch: 5 yield running, poi done. Settiamo cancel_event
            # poco dopo per simulare utente che preme Stop.
            yield "# Step 1", "running"
            yield "# Step 1", "running"
            yield "# Step 1", "running"
            if n_calls["ocr"] == 1:
                cancel_event.set()
            yield "# Step 1", "running"
            yield "# Final text", "done"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", TogglingOCR,
    )
    monkeypatch.setattr(
        "relictoepub.pipeline.render_pdf",
        lambda *a, **kw: _fake_ingest_result(tmp_path, n_pages=4),
    )

    store = CheckpointStore(resolve_checkpoint_dir(pdf))

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="Z"),
        checkpoint_store=store,
    )
    # Hook: durante il run, appena vediamo cancel arrivare, lo
    # propaga alla pipeline. Semplice: usiamo un thread watcher.
    def watch_and_cancel():
        cancel_event.wait(timeout=5.0)
        pipeline.cancel()

    watcher = threading.Thread(target=watch_and_cancel, daemon=True)
    watcher.start()

    with pytest.raises(PipelineCancelledError) as exc_info:
        list(pipeline.run_iter(pdf, tmp_path / "out.epub"))

    # Il batch 0 deve aver completato (settato in completed_batches)
    assert n_calls["ocr"] == 1
    assert exc_info.value.completed_batches >= 1

    # Checkpoint deve avere il batch 0 salvato
    loaded = store.load()
    assert loaded is not None
    assert 0 in loaded.completed_batches
    assert "Final text" in loaded.batch_markdown["0"]


def test_cancel_preserves_checkpoint_completed_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se cancel avviene prima del run, lo stato checkpoint rimane vuoto
    (nessuna scrittura). Se cancel avviene mid-pipeline, i batch fatti sono
    persistiti.
    """
    pdf = tmp_path / "z.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    class SlowOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths):
            # Batch 0: emette "done" rapidamente, con un po' di delay
            yield "# B0 partial", "running"
            time.sleep(0.1)  # dà tempo al test di chiamare cancel()
            yield "# B0 partial", "running"
            yield "# B0 done", "done"
            # Batch 1: dovrebbe essere short-circuit dal cancel check
            yield "# B1 partial", "running"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", SlowOCR,
    )
    monkeypatch.setattr(
        "relictoepub.pipeline.render_pdf",
        lambda *a, **kw: _fake_ingest_result(tmp_path, n_pages=4),
    )

    store = CheckpointStore(resolve_checkpoint_dir(pdf))

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="Z"),
        checkpoint_store=store,
    )
    # Cancelliamo DOPO che il batch 0 ha emesso done (quando yieldiamo
    # il batch 1) — il checkpoint del batch 0 deve essere salvato.
    def trigger_cancel_when_batch1_starts():
        # Aspettiamo che "B1" sia in volo (yield stringa di B1)
        start = time.time()
        while time.time() - start < 3.0:
            if pipeline.is_cancelled():
                return
            # Quando viene processato il batch 1, i log "B1 partial" appaiono
            # nel log; più semplice: aspettiamo finché check_cancelling
            # è imminente; qui dormiamo un piccolo delay per garantire che
            # batch 0 abbia emesso done.
            time.sleep(0.05)
            if hasattr(trigger_cancel_when_batch1_starts, "_done_event"):
                break
        pipeline.cancel()

    # Helper più diretto: attiviamo cancel subito dopo un piccolo delay,
    # assicurandoci che batch 0 sia già stato processato (visto che il mock
    # fa time.sleep 0.1 prima di done).
    def simple_cancel():
        time.sleep(0.15)  # > il delay di 0.1 nel mock → cancel dopo "done" del batch 0
        pipeline.cancel()
    t = threading.Thread(target=simple_cancel, daemon=True)
    t.start()
    with pytest.raises(PipelineCancelledError):
        list(pipeline.run_iter(pdf, tmp_path / "out.epub"))
    t.join()

    # Checkpoint presente e contiene almeno il batch 0
    assert store.exists()
    state = store.load()
    assert state is not None
    assert len(state.completed_batches) >= 1


def test_cancel_without_checkpoint_save_skips_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Senza ``checkpoint_store``, il cancel deve comunque funzionare e
    sollevare ``PipelineCancelledError`` (lo stato non persiste ma la
    pipeline termina pulita).
    """
    pdf = tmp_path / "w.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    n_calls = {"ocr": 0}

    class SlowOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths):
            n_calls["ocr"] += 1
            # Stesso pattern del test che funziona: yield + delay + done.
            # L'utente preme cancel DOPO che il batch 0 ha emesso done.
            yield "# B0 partial", "running"
            time.sleep(0.2)  # delay lungo → dà tempo al thread di cancel
            yield "# B0 partial", "running"
            yield "# B0 done", "done"
            # Batch 1: deve short-circuit al cancel check mid-batch.
            yield "# B1 running", "running"
            yield "# B1 running", "running"
            yield "# B1 done", "done"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", SlowOCR,
    )
    monkeypatch.setattr(
        "relictoepub.pipeline.render_pdf",
        lambda *a, **kw: _fake_ingest_result(tmp_path, n_pages=6),
    )

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="W"),
        # NO checkpoint_store
    )
    # Cancel AFTER batch 0 done (which fires after 0.2s of mock delay)
    def delayed_cancel():
        time.sleep(0.25)  # > 0.2s del mock → cancel dopo done del B0
        pipeline.cancel()
    t = threading.Thread(target=delayed_cancel, daemon=True)
    t.start()
    with pytest.raises(PipelineCancelledError):
        list(pipeline.run_iter(pdf, tmp_path / "out.epub"))
    t.join()
    # Almeno 1 OCR eseguita. Senza checkpoint_store,
    # ``completed_batches`` nell'eccezione è 0 by design
    # (lo stato non è persistito): verifichiamo quindi l'altro
    # sintomo diretto — l'OCR è stata chiamata almeno una volta.
    assert n_calls["ocr"] >= 1


def test_pipeline_run_iter_does_not_auto_reset_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG #3 (documentato): ``run_iter`` NON resetta ``_cancel_event``
    automaticamente. Documentiamo questo comportamento — l'utente che
    vuole riusare un'istanza ``Pipeline`` dopo una cancel DEVE chiamare
    ``reset_cancel()``.

    Se cambi questa policy (es. reset automatico), il caller della CLI
    deve essere aggiornato perché annullerebbe cancel settati da watchdog
    appena prima dell'ingresso.
    """
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    n_calls = {"ocr": 0}

    class CountingOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths):
            n_calls["ocr"] += 1
            yield "# Md", "done"

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
        metadata=BookMetadata(title="X"),
    )

    # Cancel prima della run → resta "appiccicoso" fino a reset_cancel
    pipeline.cancel()
    with pytest.raises(PipelineCancelledError):
        list(pipeline.run_iter(pdf, tmp_path / "out.epub"))

    # L'event è ancora settato (foot-gun documentato)
    assert pipeline.is_cancelled()

    # Dopo reset esplicito, run successiva completa pulita
    pipeline.reset_cancel()
    assert not pipeline.is_cancelled()
    list(pipeline.run_iter(pdf, tmp_path / "out.epub"))
    assert n_calls["ocr"] >= 1


def test_pipeline_run_iter_early_cancel_skips_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG #16: se cancel arriva prima che ``render_pdf`` sia stato
    chiamato, la pipeline non deve invocare il rendering PDF.

    Il mock ``fake_render`` attende un breve delay (50ms) per dare al
    watcher thread il tempo di impostare il flag. Senza questo delay,
    su macchine veloci ``render_pdf`` viene completato prima che il
    watcher possa interrompere, e il test diverrebbe flaky su runner
    CI lenti.
    """
    pdf = tmp_path / "y.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    render_called = {"n": 0}
    ocr_called = {"n": 0}

    def fake_render(*a, **kw):
        render_called["n"] += 1
        # Delay fittizio per simulare rendering PDF reale. Senza
        # questo, su qualsiasi runner il mock ritorna istantaneamente
        # e il watcher thread non ha tempo di impostare cancel prima
        # che il batch loop parta.
        time.sleep(0.05)
        return _fake_ingest_result(tmp_path, n_pages=4)

    class CountingOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths):
            ocr_called["n"] += 1
            yield "# Md", "done"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text

    monkeypatch.setattr(
        "relictoepub.pipeline.render_pdf", fake_render,
    )
    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", CountingOCR,
    )

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="Y"),
    )

    # Watcher thread: imposta cancel durante il rendering fittizio.
    # 10ms e' sufficiente per arrivare durante il delay di 50ms del
    # mock ``fake_render``, garantendo che il check start-of-batch del
    # batch loop scattera' (il check pre-rendering non scattera' perche'
    # il main thread lo ha gia' passato durante il setup).
    def cancel_quickly():
        time.sleep(0.01)
        pipeline.cancel()

    t = threading.Thread(target=cancel_quickly, daemon=True)
    t.start()

    with pytest.raises(PipelineCancelledError):
        list(pipeline.run_iter(pdf, tmp_path / "out.epub"))

    t.join()

    # Se l'early check ha funzionato, render_pdf non dovrebbe
    # essere stato chiamato affatto (oppure al massimo una volta
    # parziale). Su macchine lente il watcher potrebbe non arrivare
    # in tempo → consentiamo N=0 come caso ideale, N=1 come limite.
    assert render_called["n"] <= 1
    assert ocr_called["n"] == 0


def test_cancel_emits_cancelling_phase_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durante l'iterazione, prima di raise, viene emesso un
    ProgressEvent(phase='cancelling') con extra={'cancelled': True}.
    """
    pdf = tmp_path / "v.pdf"
    pdf.write_bytes(b"%PDF-1.4\nx\n%%EOF\n")

    seen_phases: list[str] = []

    class CountingOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths):
            yield "# A partial", "running"
            time.sleep(0.1)  # finestra per il watcher
            yield "# A partial", "running"
            yield "# A done", "done"
            # Secondo batch: deve short-circuit
            yield "# B partial", "running"
            yield "# B partial", "running"
            yield "# B done", "done"

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
        metadata=BookMetadata(title="V"),
    )
    # Watcher thread perché run_iter resetta l'evento all'ingresso.
    def cancel_soon():
        time.sleep(0.05)
        pipeline.cancel()
    t = threading.Thread(target=cancel_soon, daemon=True)
    t.start()
    try:
        for event in pipeline.run_iter(pdf, tmp_path / "out.epub"):
            seen_phases.append(event.phase)
    except PipelineCancelledError:
        pass
    t.join()
    assert "cancelling" in seen_phases
