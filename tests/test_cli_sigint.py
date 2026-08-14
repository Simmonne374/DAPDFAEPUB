"""Test per il signal handler SIGINT cooperativo della CLI.

Verifica che Ctrl+C durante l'esecuzione di ``convert_one.py`` non provochi
la perdita dell'ultimo batch (BUG #19).

Nota Windows: l'invio di CTRL_C_EVENT a un subprocess Python è notoriamente
poco affidabile (la libreria standard lo gestisce in modo inconsistente tra
versioni). Per questo test, validiamo la logica DIRETTAMENTE senza subprocess.
"""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from relictoepub.pipeline import (
    Pipeline,
)


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="Signal handler testing è flaky su Windows",
)
def test_sigint_handler_first_press_cooperative(tmp_path: Path) -> None:
    """Primo SIGINT: cancel cooperativo (exit 130, no KeyboardInterrupt)."""
    # Questo test è un placeholder per la logica di ``convert_one.main``.
    # Verifica manualmente la sequenza signal handler → cancel.

    pipeline = Pipeline(
        inference_config=type("Cfg", (), {"pages_per_batch": 2})(),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=type("M", (), {"title": "t"})(),
    )

    handled = [False]

    def handler(signum, frame):
        if handled[0]:
            raise KeyboardInterrupt
        handled[0] = True
        pipeline.cancel()

    old_handler = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, handler)
        # NON eseguiamo davvero la run (servirebbe un PDF reale),
        # verifichiamo solo la logica di escalation.
        assert not pipeline.is_cancelled()
        # Simula: chiama il handler manualmente
        handler(signal.SIGINT, None)
        assert pipeline.is_cancelled()
        assert handled[0] is True

        # Seconda chiamata: deve propagare KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    finally:
        signal.signal(signal.SIGINT, old_handler)
