"""Regression tests per bug B59 (issue #28).

BUG B59 (issue #28): ``build/launchers/gpu_bootstrap.py`` contiene
``try/except Exception: pass`` silenziosi che **mangiano gli errori senza
loggarli** durante la diagnostica. Conseguenza: se ``pynvml`` o
``requests.head()`` falliscono con un'eccezione non documentata, l'utente
vede ``memory_total_mb: 0`` o un download che parte da zero senza capire
il perche', e lo sviluppatore non ha nessuna traccia nei log.

Questi test verificano che le eccezioni rilevanti vengano **registrate
nel log diagnostico** (``launcher_selfcheck.log`` via ``_log_selfcheck``),
invece di essere silenziosamente scartate.

Casi coperti (cfr. issue #28):

1. **L128_NVML** -- ``pynvml.nvmlDeviceGetMemoryInfo(handle)`` solleva
   un'eccezione generica (es. handle stantio, NVML corrotto); oggi viene
   catturata da ``except Exception: pass``. Deve invece finire nel log
   diagnostico.

2. **L203_CONTENT_LENGTH** -- ``requests.head(url)`` fallisce con una
   ``requests.exceptions.ConnectionError`` (mirror offline, DNS rotto);
   oggi viene catturata da ``except Exception: pass``. Deve invece
   finire nel log diagnostico (almeno come "Content-Length HEAD fallita,
   procedo senza dimensione attesa").

3. **non-regression (happy path)** -- quando tutto va bene, nessuna
   nuova riga diagnostica spuria deve essere scritta.

I test non dipendono da GPU NVIDIA fisica ne' da connessione di rete:
usano ``monkeypatch`` per sostituire ``pynvml`` e ``requests``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
from typing import ClassVar

import pytest

# Aggiungi build/launchers al path (identico agli altri test su gpu_bootstrap)
LAUNCHER_DIR = Path(__file__).resolve().parent.parent / "build" / "launchers"
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import gpu_bootstrap as gb


# ---------------------------------------------------------------
# Fixture: redirige LOCALAPPDATA su un tmp, in modo che
# ``_log_selfcheck`` scriva in un file di test e non sporchi il log
# reale dell'utente.
# ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirige LOCALAPPDATA su un tmp; pulisce la env di warning."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("RELICTOEPUB_PATH_WARNING", raising=False)


def _selfcheck_log() -> Path | None:
    """Ritorna il path del log diagnostico se esiste."""
    local = os.environ.get("LOCALAPPDATA", "")
    p = Path(local) / "RelicToEpub" / "logs" / "launcher_selfcheck.log"
    return p if p.exists() else None


def _read_log() -> str:
    log = _selfcheck_log()
    assert log is not None, "BUG B59: launcher_selfcheck.log non creato"
    return log.read_text(encoding="utf-8")


# ===============================================================
# B59 — REPRODUCTION + FIX
# ===============================================================


def _install_fake_pynvml(monkeypatch: pytest.MonkeyPatch, *, memory_info_exc: Exception) -> None:
    """Installa un fake ``pynvml`` dove ``nvmlDeviceGetMemoryInfo`` esplode.

    Le altre funzioni ritornano valori normali cosi' ``get_gpu_info_via_smi``
    riesce a popolare almeno ``name``/``driver_version``/``compute_cap`` e
    la ``info`` finale contiene ``compute_cap``. L'unica anomalia e' la
    memoria.
    """

    fake = MagicMock()
    fake.nvmlInit.return_value = None
    fake.nvmlDeviceGetHandleByIndex.return_value = "HANDLE0"
    fake.nvmlDeviceGetName.return_value = b"Fake GPU"
    fake.nvmlSystemGetDriverVersion.return_value = b"999.99"
    # Solo nvmlDeviceGetMemoryInfo esplode con l'eccezione passata.
    fake.nvmlDeviceGetMemoryInfo.side_effect = memory_info_exc
    fake.nvmlShutdown.return_value = None
    monkeypatch.setitem(sys.modules, "pynvml", fake)


def _install_fake_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sostituisce ``subprocess.run`` con un mock che simula un nvidia-smi
    funzionante (cosi' ``compute_cap`` viene popolato da quel ramo e la
    funzione prova effettivamente a chiamare pynvml)."""

    fake_completed = MagicMock()
    fake_completed.returncode = 0
    fake_completed.stdout = "Fake GPU, 8.6, 999.99\n"

    real_run = gb.subprocess.run

    def _run_side_effect(*args, **kwargs):
        # Intercetta solo la query nvidia-smi, lascia passare il resto.
        if args and isinstance(args[0], str) and "nvidia-smi" in args[0]:
            return fake_completed
        return real_run(*args, **kwargs)

    monkeypatch.setattr(gb.subprocess, "run", _run_side_effect)


def test_b59_get_gpu_info_logs_pynvml_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B59 - REPRODUZIONE: il fallimento di ``nvmlDeviceGetMemoryInfo``
    deve lasciare traccia diagnostica.

    Scenario: l'utente ha una GPU NVIDIA reale (nvidia-smi ritorna dati),
    ma ``pynvml.nvmlDeviceGetMemoryInfo`` esplode con ``OSError`` su
    handle stantio (es. suspensione GPU driver). Con il bug, la GPU
    viene rilevata ma ``memory_total_mb`` resta assente e nessuna
    riga appare nel log. Con il fix, una riga ``pynvml: ...`` viene
    scritta in ``launcher_selfcheck.log``.
    """
    _install_fake_nvidia_smi(monkeypatch)
    _install_fake_pynvml(monkeypatch, memory_info_exc=OSError("handle stantio"))

    info = gb.get_gpu_info_via_smi()

    # Il detect di base non deve rompersi: name/compute_cap ci sono.
    assert info is not None, "BUG B59: get_gpu_info_via_smi ritorna None nonostante nvidia-smi funzioni"
    assert info.get("compute_cap") == (8, 6)
    assert info.get("name") == "Fake GPU"

    log_content = _read_log()
    assert "pynvml" in log_content.lower(), (
        "BUG B59: il fallimento pynvml è stato ingoiato silenziosamente — "
        "nessuna traccia in launcher_selfcheck.log. Atteso un log "
        "strutturato che spieghi perché memory_total_mb manca."
    )
    # Deve contenere un riferimento all'errore originale (almeno la classe).
    assert "handle stantio" in log_content or "OSError" in log_content, (
        "BUG B59: il log non riporta il messaggio dell'eccezione originale."
    )


def test_b59_download_with_progress_logs_head_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B59 - REPRODUZIONE: il fallimento di ``requests.head(url)`` deve
    lasciare traccia diagnostica.

    Scenario: l'utente tenta di scaricare un wheel ma la HEAD preliminare
    fallisce con una ``requests.exceptions.ConnectionError`` (DNS rotto,
    mirror offline, proxy aziendale). Con il bug, la dimensione attesa
    resta 0 e nessuna riga viene scritta. Con il fix, deve apparire una
    riga che spiega perché la Content-Length non è disponibile.
    """

    # Crea un fake requests in sys.modules con .head() che esplode.
    fake_requests = MagicMock()
    fake_requests.exceptions = __import__("requests").exceptions

    class _FakeResp:
        status_code = 200
            headers: ClassVar[dict] = {}

        def raise_for_status(self) -> None:
            return None

    fake_requests.head.side_effect = fake_requests.exceptions.ConnectionError(
        "DNS failure"
    )
    fake_requests.get.return_value = _FakeResp()  # non ci arriviamo comunque

    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    state = MagicMock()
    state.set_phase = MagicMock()
    state.update_download = MagicMock()
    state.error = MagicMock()

    dest = tmp_path / "wheel.whl"
    ok = gb.download_with_progress(
        "https://example.invalid/wheel.whl",
        dest,
        state,
        timeout=5,
    )

    # Il fix può scegliere di ritornare False oppure continuare: l'importante
    # e' che la diagnosi sia finita nel log. Qui ci aspettiamo che la
    # pipeline abortisca per via dell'errore di rete, quindi False.
    assert ok is False, (
        "BUG B59: download_with_progress ha avuto successo nonostante la "
        "HEAD fallisse completamente (mock di rete mal configurato?)"
    )

    log_content = _read_log()
    assert "head" in log_content.lower() or "content-length" in log_content.lower(), (
        "BUG B59: il fallimento della HEAD preliminare non è stato loggato. "
        "Attesa una riga diagnostica che spieghi perché la Content-Length "
        "non è disponibile."
    )


def test_b59_happy_path_no_spurious_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B59 - non-regression: quando ``get_gpu_info_via_smi`` va a buon fine,
    NON deve scrivere righe spurie di fallimento in ``launcher_selfcheck.log``.

    Garantisce che il fix non introduca rumore diagnostico sul path
    normale. Nota: ``_log_stale_caches`` e altri helper possono scrivere
    righe legittime, quindi filtriamo solo i messaggi relativi al
    detect GPU.
    """

    _install_fake_nvidia_smi(monkeypatch)

    fake = MagicMock()
    fake.nvmlInit.return_value = None
    fake.nvmlDeviceGetHandleByIndex.return_value = "HANDLE0"
    fake.nvmlDeviceGetName.return_value = b"Fake GPU"
    fake.nvmlSystemGetDriverVersion.return_value = b"999.99"
    fake.nvmlDeviceGetMemoryInfo.return_value = MagicMock(total=8 * 1024 * 1024 * 1024)
    fake.nvmlShutdown.return_value = None
    monkeypatch.setitem(sys.modules, "pynvml", fake)

    info = gb.get_gpu_info_via_smi()

    assert info is not None
    assert info.get("compute_cap") == (8, 6)

    log = _selfcheck_log()
    # Happy path: nessuna voce diagnostica deve essere stata generata.
    if log is not None:
        content = log.read_text(encoding="utf-8").lower()
        assert "pynvml" not in content or "fallita" not in content, (
            "BUG B59 happy-path: il fix aggiunge diagnostica spuria in "
            "assenza di errori (rilevato 'pynvml ... fallita')."
        )
