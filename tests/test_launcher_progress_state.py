"""Test unitari per :mod:`build.launchers.progress_state`.

Il modulo e' IPC condiviso fra ``gpu_bootstrap.py`` (writer) e
``gpu_splash.py`` (reader). Testiamo:

* scrittura atomica (nessun .tmp lasciato sul disco)
* stato di default quando il file manca
* tolleranza a JSON corrotto
* percorso custom (parametrizzato nel costruttore)
* ogni writer method aggiorna ``updated_at``
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Aggiunge ``build/launchers`` al sys.path cosi ``import progress_state``
# funziona identico al runtime di gpu_bootstrap.
_LAUNCHERS = Path(__file__).resolve().parents[1] / "build" / "launchers"
if str(_LAUNCHERS) not in sys.path:
    sys.path.insert(0, str(_LAUNCHERS))

import progress_state as ps_mod
from progress_state import ProgressState


@pytest.fixture()
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


def test_default_state_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    s = ProgressState(p)
    data = s.get()
    assert data["phase"] == "starting"
    assert data["message"] == ""
    assert data["downloaded_bytes"] == 0
    assert data["total_bytes"] == 0
    assert data["speed_bps"] == 0.0
    assert data["eta_seconds"] == 0.0
    assert isinstance(data["updated_at"], float)


def test_reset_initializes_phase(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.reset()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["phase"] == "starting"
    assert data["message"] == "Avvio in corso…"


def test_set_phase_updates_phase_and_message(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.set_phase("select_wheel", message="GPU rilevata")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["phase"] == "select_wheel"
    assert data["message"] == "GPU rilevata"


def test_set_phase_preserves_counters(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.update_download(500, 1000)
    s.set_phase("verify", message="Verifica")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    # I counter del download devono essere preservati (utile per la UI).
    assert data["downloaded_bytes"] == 500
    assert data["total_bytes"] == 1000
    assert data["phase"] == "verify"


def test_update_download_sets_phase_and_counters(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.update_download(1024, 4096, speed_bps=512.0, eta_seconds=6.0)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["phase"] == "download_wheel"
    assert data["downloaded_bytes"] == 1024
    assert data["total_bytes"] == 4096
    assert data["speed_bps"] == 512.0
    assert data["eta_seconds"] == 6.0


def test_error_sets_phase_error(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.error("Download fallito")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["phase"] == "error"
    assert data["message"] == "Download fallito"


def test_done_sets_phase_done(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.done("Installato")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["phase"] == "done"
    assert data["message"] == "Installato"


def test_writer_methods_update_timestamp(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.reset()
    initial = json.loads(state_path.read_text(encoding="utf-8"))["updated_at"]
    time.sleep(0.02)
    s.set_phase("detect_gpu", message="x")
    after_set = json.loads(state_path.read_text(encoding="utf-8"))["updated_at"]
    assert after_set > initial
    time.sleep(0.02)
    s.update_download(1, 2)
    after_dl = json.loads(state_path.read_text(encoding="utf-8"))["updated_at"]
    assert after_dl > after_set
    time.sleep(0.02)
    s.error("boom")
    after_err = json.loads(state_path.read_text(encoding="utf-8"))["updated_at"]
    assert after_err > after_dl
    time.sleep(0.02)
    s.done("ok")
    after_done = json.loads(state_path.read_text(encoding="utf-8"))["updated_at"]
    assert after_done > after_err


def test_atomic_write_leaves_no_tmp(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.reset()
    s.set_phase("download_wheel", message="x")
    s.update_download(1, 2)
    s.error("boom")
    s.done("ok")
    # Nessun .tmp deve essere rimasto accanto al file.
    leftover = list(state_path.parent.glob("*.tmp"))
    assert leftover == [], f"tmp residui: {leftover}"


def test_corrupt_json_returns_defaults(state_path: Path) -> None:
    state_path.write_text("{ this is not valid json", encoding="utf-8")
    s = ProgressState(state_path)
    data = s.get()
    assert data["phase"] == "starting"
    # Aggiornare dopo aver letto JSON corrotto deve riscrivere correttamente.
    s.reset()
    fixed = json.loads(state_path.read_text(encoding="utf-8"))
    assert fixed["phase"] == "starting"


def test_seconds_since_update(state_path: Path) -> None:
    s = ProgressState(state_path)
    s.reset()
    elapsed = s.seconds_since_update()
    assert 0.0 <= elapsed < 1.0
    time.sleep(0.05)
    elapsed = s.seconds_since_update()
    assert elapsed >= 0.05


def test_non_dict_json_returns_defaults(state_path: Path) -> None:
    state_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    s = ProgressState(state_path)
    assert s.get()["phase"] == "starting"


def test_creates_parent_dir(tmp_path: Path) -> None:
    # Il file va in una sottocartella che ancora non esiste.
    deep = tmp_path / "nested" / "more" / "state.json"
    s = ProgressState(deep)
    s.reset()
    assert deep.exists()


def test_module_default_path_constant() -> None:
    # Default punta in %TEMP%/RelicToEpubBoot/state.json. Non verifichiamo
    # l'esatto path perche' dipende dall'OS, ma deve essere un Path con
    # suffisso state.json dentro una cartella RelicToEpubBoot.
    assert isinstance(ps_mod.DEFAULT_PATH, Path)
    assert ps_mod.DEFAULT_PATH.name == "state.json"
    assert ps_mod.DEFAULT_PATH.parent.name == "RelicToEpubBoot"