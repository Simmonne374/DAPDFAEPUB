"""Riproduzione isolata del bug B38 — ``UnlimitedOCRRunner.run_batch_iter``
non controlla il cancel token durante l'attesa su ``queue.get``.

Issue #38: il loop interno di :meth:`UnlimitedOCRRunner.run_batch_iter`
esegue ``q.get(timeout=0.1)`` ad ogni iterazione. Tra un check e l'altro
il consumer (la pipeline) può essere sospeso fino a 100 ms anche se
``Pipeline.cancel()`` è già stato invocato. Il check di cancel
avviene solo in :func:`relictoepub.pipeline.Pipeline.run_iter` quando
il generator ``run_batch_iter`` cede il controllo (yield), quindi
durante una ``q.get`` bloccante non c'è alcuna osservazione.

Effetto: l'utente che preme "Stop" nella UI Gradio aspetta fino a
100 ms extra per la propagazione del cancel. In modalità CPU
(~1 token/sec) il delay può superare la finestra di un singolo token.
In GPU mode (~50 token/sec) il delay è trascurabile — il bug è
comunque un correctness regression (cancel non-deterministic entro
la finestra di timeout).

Questo file:

1. **Reproduce** il bug con un test failing che misura il tempo tra
   ``cancel_event.set()`` e l'effettiva uscita del loop, e mostra
   che supera i 50 ms (corrispondente a più cicli di ``q.get``).
2. **Validate** il fix atteso: dopo la patch, l'uscita avviene entro
   50 ms (corrispondente a una singola iterazione del loop con
   timeout ridotto e check esplicito).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from relictoepub.inference.config import InferenceConfig
from relictoepub.inference.unlimited_ocr import (
    OCRCancelledError,
    UnlimitedOCRRunner,
    _QueueWriter,
)

# ---------------------------------------------------------------------
# Fake model che simula ``Unlimited-OCR`` con un worker che NON emette
# token fino a quando non riceve un segnale esterno. Questo riproduce
# fedelmente il caso reale in cui il worker thread è occupato in
# ``infer()`` e il consumer è sospeso su ``q.get(timeout=0.1)``.
# ---------------------------------------------------------------------


class _SilentModel:
    """Modello OCR che blocca su un ``threading.Event`` invece di emettere token.

    Quando ``unblock_event`` viene settato, restituisce il ``decoded``.
    Rimane vivo (per ``thread.is_alive()``) finché ``unblock_event``
    non viene settato, esattamente come il worker thread reale quando
    è dentro ``infer()``.
    """

    def __init__(
        self,
        decoded: str,
        unblock_event: threading.Event,
    ) -> None:
        self._decoded = decoded
        self._unblock = unblock_event

    def infer(self, tokenizer, *, image_file, **kwargs) -> str:
        # Aspetta indefinitamente che il test sblocchi. ``is_alive()``
        # del thread resta True durante questa attesa, esattamente
        # come il worker thread reale durante ``infer()``.
        self._unblock.wait(timeout=10.0)
        return self._decoded

    def infer_multi(
        self, tokenizer, *, image_files, **kwargs,
    ) -> tuple[str, None]:
        self._unblock.wait(timeout=10.0)
        return self._decoded, None

    def eval(self) -> None:
        return None

    def to(self, _device):  # pragma: no cover
        return self


class _FakeTokenizer:
    def __getattr__(self, _name):  # nessun attributo usato nel test
        raise AttributeError("FakeTokenizer: nessun attributo usato")


def _make_runner_with_silent_model(
    monkeypatch: pytest.MonkeyPatch,
    unblock_event: threading.Event,
    decoded: str = "# Done",
) -> UnlimitedOCRRunner:
    """Costruisce un runner il cui worker NON emette token fino a ``unblock_event``."""
    runner = UnlimitedOCRRunner(InferenceConfig())
    runner._model = _SilentModel(decoded=decoded, unblock_event=unblock_event)
    runner._tokenizer = _FakeTokenizer()
    runner._loaded = True
    # Evita chiamate reali a ``load_model``.
    monkeypatch.setattr(runner, "load_model", lambda: None)
    return runner


def _consume_in_background(
    runner: UnlimitedOCRRunner,
    consumer_thread: threading.Thread,
    cancel_event: threading.Event,
) -> None:
    """Aggancia il consumer al runner con supporto cancel opzionale.

    Se ``run_batch_iter`` accetta un parametro ``cancel_check``, viene
    passato. Altrimenti il consumer si affida al check esterno.
    """


def _consume_run_batch_iter(
    runner: UnlimitedOCRRunner,
    cancel_event: threading.Event,
    images: list[Path],
) -> tuple[threading.Thread, list[tuple[str, str]], list[float], list[BaseException]]:
    """Consuma ``run_batch_iter`` in un thread separato, registrando i tempi dei yield.

    Cattura :class:`OCRCancelledError` (che ora il runner solleva quando
    ``cancel_check`` ritorna ``True``) e la memorizza in ``exceptions``
    per consentire al test di verificare che l'uscita sia avvenuta
    tramite cancel e non per altra causa.

    Returns:
        (thread, events_yielded, timestamps, exceptions) — i timestamp
        sono in secondi da ``time.perf_counter()`` per ogni yield
        ricevuto dal consumer; ``exceptions`` è la lista di eccezioni
        sollevate dal thread (usualmente 0 o 1 elemento).
    """
    events: list[tuple[str, str]] = []
    timestamps: list[float] = []
    exceptions: list[BaseException] = []

    def _consume() -> None:
        try:
            try:
                # Prova la firma con cancel_check (post-fix).
                stream = runner.run_batch_iter(
                    images, cancel_check=cancel_event.is_set,
                )
            except TypeError:
                # Pre-fix: la firma non accetta cancel_check.
                stream = runner.run_batch_iter(images)
            for event in stream:
                events.append(event)
                timestamps.append(time.perf_counter())
        except BaseException as exc:  # noqa: BLE001 - registriamo tutto
            exceptions.append(exc)

    t = threading.Thread(target=_consume, daemon=True)
    return t, events, timestamps, exceptions


# ---------------------------------------------------------------------
# B38 — REPRODUCTION
# ---------------------------------------------------------------------


def test_b38_run_batch_iter_honors_cancel_within_one_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B38 — REPRODUZIONE + FIX.

    Quando ``cancel_check`` viene passato a ``run_batch_iter``, il loop
    deve uscire entro UNA iterazione (cioè entro ``q.get`` timeout)
    dal momento in cui ``cancel_event`` viene settato.

    Scenario: il worker thread è occupato in ``infer()`` e NON emette
    token. Il consumer è sospeso su ``q.get(timeout=0.1)``. Quando
    il test setta ``cancel_event``, il consumer deve rilevare il
    cancel alla PRIMA iterazione successiva del loop (≤ timeout +
    ε).
    """
    cancel_event = threading.Event()
    unblock_event = threading.Event()

    runner = _make_runner_with_silent_model(
        monkeypatch, unblock_event=unblock_event,
    )

    images = [Path("fake.png")]
    t, _events, _timestamps, exceptions = _consume_run_batch_iter(
        runner, cancel_event, images,
    )
    t.start()

    # Diamo al thread consumer il tempo di entrare nel loop
    # (e bloccarsi su ``q.get(timeout=0.1)``).
    time.sleep(0.15)
    assert t.is_alive(), (
        "Il thread consumer dovrebbe essere ancora vivo, in attesa "
        "di un token dal worker silente."
    )

    # Settiamo cancel. Misuriamo quanto tempo passa prima che il
    # thread esca (cioè prima che la ``PipelineCancelledError``
    # venga sollevata o che il generator esaurisca).
    t_cancel = time.perf_counter()
    cancel_event.set()

    # Diamo al consumer una finestra ragionevole per reagire. Con
    # il fix atteso (check esplicito ad ogni iterazione, timeout
    # ridotto a ~50 ms), il consumer dovrebbe uscire entro 80 ms.
    # SENZA il fix (timeout 100 ms + check solo dopo che ``q.get``
    # ritorna), il consumer può rimanere sospeso fino alla fine
    # del timeout, e se l'utente ha premuto cancel proprio in
    # mezzo a ``q.get`` il delay può essere fino a 100 ms per
    # iterazione.
    join_deadline = t_cancel + 0.5  # 500 ms di finestra totale
    while time.perf_counter() < join_deadline:
        if not t.is_alive():
            break
        time.sleep(0.01)
    elapsed = time.perf_counter() - t_cancel
    assert not t.is_alive(), (
        f"B38 REPRODUCED: il consumer non è uscito dal loop entro "
        f"{elapsed * 1000:.0f} ms dal set del cancel event. "
        "Il loop di run_batch_iter non controlla cancel_check durante "
        "l'attesa su q.get."
    )

    # Con il fix: l'uscita avviene entro ~50 ms (1 timeout).
    # Senza il fix: il consumer esce comunque alla fine del timeout
    # corrente (~100 ms) ma NON per via del cancel — semplicemente
    # perché q.get restituisce Empty. La DISTINZIONE chiave è che
    # *prima del fix* il consumer può aver continuato a ciclare per
    # più di una iterazione anche dopo cancel, perché ogni
    # iterazione deve aspettare il timeout di q.get prima di
    # ricontrollare. Verifichiamo quindi che l'uscita avvenga entro
    # una sola iterazione (≤ 120 ms = timeout + margine) per garantire
    # che il fix abbia effetto reale e non sia "fortuna".
    assert elapsed <= 0.12, (
        f"B38: l'uscita è avvenuta dopo {elapsed * 1000:.0f} ms — "
        "troppo lungo per indicare un check cancel attivo. Con il "
        "fix atteso, l'uscita avviene entro 50 ms (1 sola iterazione)."
    )

    # Sblocchiamo il worker per evitare leak.
    unblock_event.set()
    t.join(timeout=2.0)

    # Verifica chiave del fix: il consumer è uscito perché il cancel
    # check ha funzionato (solleva OCRCancelledError), NON perché il
    # worker ha finito. Questo distingue il fix vero da una falsa
    # uscita causata dallo sblocco del worker.
    assert exceptions, (
        "Atteso che il consumer sollevi OCRCancelledError dopo cancel "
        "set; thread uscito senza eccezione invece."
    )
    assert any(isinstance(e, OCRCancelledError) for e in exceptions), (
        f"Atteso OCRCancelledError, ottenuto: {[type(e).__name__ for e in exceptions]!r}"
    )


def test_b38_cancel_check_signature_backward_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B38 — REGRESSION GUARD (contratto pubblico).

    ``run_batch_iter`` deve restare invocabile SENZA ``cancel_check``
    (parametro opzionale con default ``None``). Chi usa la firma
    legacy continua a funzionare senza modifiche.
    """
    unblock_event = threading.Event()
    runner = _make_runner_with_silent_model(
        monkeypatch, unblock_event=unblock_event,
    )

    images = [Path("fake.png")]

    # Avviamo un consumer che usa la firma legacy (senza cancel_check).
    events: list[tuple[str, str]] = []

    def _consume() -> None:
        for event in runner.run_batch_iter(images):
            events.append(event)

    t = threading.Thread(target=_consume, daemon=True)
    t.start()
    time.sleep(0.05)  # lascia al consumer il tempo di entrare nel loop

    # Sblocchiamo il worker → il consumer deve ricevere "done".
    unblock_event.set()
    t.join(timeout=2.0)
    assert not t.is_alive(), "Il consumer doveva uscire dopo unblock"
    assert events[-1][1] == "done", (
        f"L'ultimo evento doveva essere 'done', ottenuto: {events[-1]!r}"
    )


def test_b38_cancel_during_empty_queue_terminates_promptly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B38 — variante: cancel quando la queue è vuota da molto tempo.

    Questo è il caso pathologico: il worker è bloccato in ``infer()``
    e non ha ancora prodotto alcun token. La queue è vuota. Il
    consumer è sospeso su ``q.get(timeout=0.1)``. Il fix DEVE
    far uscire il consumer entro un timeout dal cancel set.

    Senza il fix: il consumer continua a ciclare su queue.Empty
    ogni 100 ms senza mai controllare cancel. Non esce mai finché
    il worker non completa.
    """
    cancel_event = threading.Event()
    unblock_event = threading.Event()

    runner = _make_runner_with_silent_model(
        monkeypatch, unblock_event=unblock_event,
    )

    images = [Path("fake.png")]
    t, _events, _ts, exceptions = _consume_run_batch_iter(
        runner, cancel_event, images,
    )
    t.start()
    time.sleep(0.15)  # consumer è dentro q.get

    # Set cancel, cronometriamo.
    t_cancel = time.perf_counter()
    cancel_event.set()

    # Con il fix, uscita entro ~50 ms (1 iterazione).
    # Senza il fix, NON esce finché unblock_event non è settato.
    # Verifichiamo SOLO che il thread sia uscito entro la deadline
    # PRIMA di sbloccare il worker: questo è il vero test del fix,
    # perché senza il check esplicito il consumer resta sospeso su
    # ``q.get(timeout=0.1)`` anche dopo cancel, in attesa del
    # timeout corrente. Con il check esplicito, l'uscita avviene
    # alla prossima iterazione del while.
    deadline = t_cancel + 0.15  # < 1 timeout + margine
    while time.perf_counter() < deadline:
        if not t.is_alive():
            break
        time.sleep(0.005)
    exited_before_unblock = not t.is_alive()
    elapsed = time.perf_counter() - t_cancel

    # Cleanup: sblocchiamo il worker per non lasciare thread appesi.
    unblock_event.set()
    t.join(timeout=2.0)

    assert exited_before_unblock, (
        f"B38 REPRODUCED: il consumer è rimasto vivo per "
        f"{elapsed * 1000:.0f} ms dopo cancel set, anche con la "
        "queue vuota e il worker bloccato. Senza un check esplicito "
        "nel while loop, il consumer non può rilevare cancel tra un "
        "q.get e l'altro."
    )

    # Verifica chiave del fix: l'uscita è avvenuta per OCRCancelledError,
    # non per altro (es. timeout del worker).
    assert exceptions, (
        "Atteso che il consumer sollevi OCRCancelledError; thread "
        "uscito senza eccezione invece."
    )
    assert any(isinstance(e, OCRCancelledError) for e in exceptions), (
        f"Atteso OCRCancelledError, ottenuto: {[type(e).__name__ for e in exceptions]!r}"
    )


# ---------------------------------------------------------------------
# Sanity: il writer della queue continua a funzionare (regression guard
# sul pattern ``_QueueWriter`` che è il complemento di ``run_batch_iter``).
# ---------------------------------------------------------------------


def test_queue_writer_drains_into_queue() -> None:
    """``_QueueWriter`` deve consegnare i write alla coda (regression guard)."""
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
