"""Test per la diagnostica del bootstrap (install path validation).

Questi test coprono ``_check_install_path`` (validation del percorso di
installazione) e ``_log_selfcheck`` (append al log diagnostico).

``_check_install_path`` usa ``winreg`` quando disponibile; fuori da Windows
il blocco di registry viene saltato silenziosamente. I test che richiedono
winreg sono marcati ``@pytest.mark.skipif(sys.platform != "win32")``.

Per i casi non-Windows ci limitiamo a verificare che la funzione:
- non sollevi eccezioni;
- logghi un problema quando l'exe manca o punta a un floppy drive;
- scriva su stderr;
- NON logghi quando i marker sono presenti (happy path).
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

# Aggiungi build/launchers al path per importare gpu_bootstrap
LAUNCHER_DIR = Path(__file__).resolve().parent.parent / "build" / "launchers"
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import gpu_bootstrap as gb


@pytest.fixture(autouse=True)
def _isolate_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirige LOCALAPPDATA su un tmp per non sporcare il log reale."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


@pytest.fixture(autouse=True)
def _reset_path_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pulisci la env var di warning tra un test e l'altro."""
    monkeypatch.delenv("RELICTOEPUB_PATH_WARNING", raising=False)


def _capture_stderr(callable_, *args, **kwargs):
    """Esegue callable catturando stderr (compatibile con pytest capsys)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        callable_(*args, **kwargs)
    return buf.getvalue()


def _selfcheck_log() -> Path | None:
    """Ritorna il path del log diagnostico se esiste."""
    local = os.environ.get("LOCALAPPDATA", "")
    p = Path(local) / "RelicToEpub" / "logs" / "launcher_selfcheck.log"
    return p if p.exists() else None


def test_log_selfcheck_writes_to_log() -> None:
    gb._log_selfcheck("test message one")
    gb._log_selfcheck("test message two")
    log = _selfcheck_log()
    assert log is not None
    content = log.read_text(encoding="utf-8")
    assert "test message one" in content
    assert "test message two" in content
    # Deve avere timestamp + tag [bootstrap]
    assert "[bootstrap]" in content


def test_log_selfcheck_survives_bad_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se LOCALAPPDATA punta a un path non scrivibile, non deve esplodere.

    Usiamo un percorso che esiste ma su cui l'utente corrente non puo'
    scrivere (es. C:/Windows/System32 sotto account non-amministratore).
    La funzione deve comunque completare senza sollevare eccezioni.
    """
    monkeypatch.setenv("LOCALAPPDATA", "C:/Windows/System32/config")
    # Non deve sollevare eccezioni
    gb._log_selfcheck("this should not crash")


def test_check_install_path_missing_exe(tmp_path: Path) -> None:
    """Exe inesistente -> warning + stderr."""
    fake_exe = tmp_path / "RelicToEpub" / "_internal" / "RelicToEpub.exe"
    err = _capture_stderr(gb._check_install_path, fake_exe)
    assert "ATTENZIONE" in err
    assert "non esistente" in err or "mancante" in err or "marker" in err
    assert os.environ.get("RELICTOEPUB_PATH_WARNING") == "1"


def test_check_install_path_floppy_drive() -> None:
    """Exe che punta a A:\\ o B:\\ -> warning floppy."""
    fake_exe = Path("A:/RelicToEpub/RelicToEpub.exe")
    err = _capture_stderr(gb._check_install_path, fake_exe)
    assert "ATTENZIONE" in err
    assert "floppy" in err.lower() or "A:" in err or "B:" in err
    assert os.environ.get("RELICTOEPUB_PATH_WARNING") == "1"


def test_check_install_path_no_markers(tmp_path: Path) -> None:
    """Exe esistente ma senza marker RelicToEpub -> warning."""
    fake_exe = tmp_path / "RelicToEpub.exe"
    fake_exe.write_bytes(b"MZ")
    err = _capture_stderr(gb._check_install_path, fake_exe)
    assert "ATTENZIONE" in err
    assert "marker" in err.lower() or "Nessun" in err
    assert os.environ.get("RELICTOEPUB_PATH_WARNING") == "1"


def test_check_install_path_happy_path(tmp_path: Path) -> None:
    """Exe + marker presenti -> nessun warning, stderr vuoto."""
    install_dir = tmp_path / "RelicToEpub"
    install_dir.mkdir()
    exe = install_dir / "RelicToEpub.exe"
    exe.write_bytes(b"MZ")
    # Crea un marker riconosciuto
    (install_dir / "RelicToEpubUI.exe").write_bytes(b"MZ")
    err = _capture_stderr(gb._check_install_path, exe)
    assert "ATTENZIONE" not in err
    # Su Windows, se winreg c'e' e la chiave non esiste, non e' un problema
    assert os.environ.get("RELICTOEPUB_PATH_WARNING") is None


def test_check_install_path_does_not_raise(tmp_path: Path) -> None:
    """Garbage input non deve mai sollevare eccezioni (la funzione e' diagnostica)."""
    weird_inputs = [
        Path("Z:/does/not/exist/anything.exe"),
        tmp_path / ("x" * 1000 + ".exe"),  # path molto lungo
    ]
    for p in weird_inputs:
        _capture_stderr(gb._check_install_path, p)


@pytest.mark.skipif(sys.platform != "win32", reason="richiede winreg")
def test_check_install_path_windows_registry_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Su Windows, con registry mock, verifica che il path mismatch logghi.

    Usa un tmp_path come 'app_exe' e mocka winreg per restituire un
    InstallLocation completamente diverso. Il confronto case-insensitive
    deve segnalare la divergenza.
    """
    install_dir = tmp_path / "RelicToEpub"
    install_dir.mkdir()
    exe = install_dir / "RelicToEpub.exe"
    exe.write_bytes(b"MZ")
    # Marker per evitare il warning di "no markers"
    (install_dir / "RelicToEpubUI.exe").write_bytes(b"MZ")

    class _FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open_key(hive, sub_key, *args, **kwargs):
        return _FakeKey()

    def fake_query_value_ex(key, name):
        # InstallLocation fittizio su un'altra unita' (Z:), cosi' il
        # .resolve() non collide con quello del tmp_path.
        return ("Z:/Other/RelicToEpub", 1)

    import winreg

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query_value_ex)

    err = _capture_stderr(gb._check_install_path, exe)
    assert "diverge" in err or "registro" in err.lower()
