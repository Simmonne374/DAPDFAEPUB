"""Wrapper CLI per l'eseguibile RelicToEpubCLI.

Stesse responsabilità di ``launch_ui_launcher.py`` ma delega l'esecuzione
allo script ``scripts/convert_one.py`` con il forwarding degli argomenti e
propagazione dell'exit code. Redirige anche stdout/stderr su file di log
in ``AppData\\Local\\RelicToEpub\\logs``.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Versione letta dinamicamente dal pacchetto (single source of truth:
# pyproject.toml). In caso di bundle PyInstaller che non include i metadata
# importlib.metadata cade sul fallback "0.0.0+unknown".
try:
    from relictoepub import __version__ as _APP_VERSION
except Exception:
    _APP_VERSION = "0.0.0+unknown"

# Lettere di unita storicamente non scrivibili su Windows moderni (floppy).
# Vengono usate in _self_check per intercettare shortcut orfani che puntano
# a unita non piu presenti (es. bug "A:\\RelicToEpub" -> CreateProcess: 5).
_LETTERE_FLOPPY = {"A", "B"}


def _project_paths() -> tuple[Path, Path]:
    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)),
        Path(__file__).resolve().parent,
        Path.cwd(),
    ]
    for c in candidates:
        src = c / "src"
        if src.is_dir():
            return c, src
    return Path.cwd(), Path.cwd() / "src"


def _setup_logging() -> Path:
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
    log_dir = Path(local) / "RelicToEpub" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"cli_{datetime.now():%Y%m%d_%H%M%S}.log"

    try:
        log_fp = log_path.open("a", encoding="utf-8")
        sys.stdout = log_fp
        sys.stderr = log_fp
        return log_path
    except OSError:
        return Path()


def _log_diagnostic(message: str) -> None:
    """Scrive un messaggio diagnostico nel log dedicato ``launcher_selfcheck.log``."""
    try:
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
        log_dir = Path(local) / "RelicToEpub" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "launcher_selfcheck.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except OSError:
        pass


def _self_check() -> bool:
    """Verifica che il percorso di esecuzione sia realistico (versione CLI).

    Ritorna True se va tutto bene, False altrimenti. Logga sempre un
    messaggio diagnostico per aiutare la diagnosi in caso di failure.
    Niente finestra tkinter qui (la CLI non ha GUI): il messaggio va
    solo su stderr + log file.
    """
    try:
        exe = Path(sys.executable)
    except (OSError, ValueError) as exc:
        _log_diagnostic(f"[cli] sys.executable non recuperabile: {exc}")
        return False

    # Test 1: il file esiste davvero?
    if not exe.exists():
        _log_diagnostic(f"[cli] sys.executable inesistente: {exe}")
        return False

    # Test 2: drive letter sospetto?
    drive = exe.drive or ""
    if drive.rstrip(":").upper() in _LETTERE_FLOPPY:
        _log_diagnostic(
            f"[cli] sys.executable punta a unita floppy {drive!r}: {exe}"
        )
        return False

    # Test 3: marker di installazione coerente con la nostra app
    parent = exe.parent
    expected_markers = [
        parent / "RelicToEpubBoot.exe",
        parent / "_internal",
        parent / "RelicToEpubUI.exe",
        parent / "RelicToEpubCLI.exe",
    ]
    if not any(m.exists() for m in expected_markers):
        _log_diagnostic(
            f"[cli] Nessun marker RelicToEpub in {parent}; exe={exe}"
        )
        return False

    _log_diagnostic(f"[cli] Self-check OK: {exe}")
    return True


def main(argv: list[str]) -> int:
    _setup_logging()

    if os.environ.get("RELICTOEPUB_BOOT_OK") not in (None, "1"):
        # In dev mode BOOT_OK puo essere assente; non blocchiamo
        pass

    # Self-check (solo in modalita bundled; in dev prosegue silenziosamente)
    is_bundled = getattr(sys, "frozen", False) or (
        "RelicToEpub" in sys.executable
        and Path(sys.executable).suffix.lower() == ".exe"
    )
    if is_bundled and not _self_check():
        # CLI non ha GUI: emette un messaggio chiaro su stderr.
        sys.stderr.write(
            f"ERRORE: il file di avvio punta a un percorso non valido "
            f"({sys.executable}).\n"
            f"Causa probabile: l'installer e stato eseguito da una unita "
            f"rimovibile (USB, floppy) ora scollegata, oppure l'installazione "
            f"e stata spostata/cancellata.\n"
            f"Risoluzione:\n"
            f"  1. Apri Impostazioni di Windows -> App -> "
            f"RelicToEpub -> Disinstalla.\n"
            f"  2. Rilancia l'installer RelicToEpub-Setup scaricato dal "
            f"sito ufficiale.\n"
            f"  3. Per dettagli: %LOCALAPPDATA%\\RelicToEpub\\logs\\"
            f"launcher_selfcheck.log\n"
            f"\nVersione: RelicToEpub {_APP_VERSION}\n"
        )
        return 4

    project_root, src_dir = _project_paths()
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        import relictoepub.cli as cli_module  # type: ignore
    except ImportError:
        try:
            # Modalita dev: usa lo script convert_one.py
            from scripts import convert_one  # type: ignore
            cli_module = convert_one
        except ImportError as exc:
            sys.stderr.write(f"[launch_cli_launcher] Import failed: {exc}\n")
            return 2

    # Lo script convert_one.main() si aspetta argv[1:] (la sys.argv originale
    # viene passata quando si esegue come __main__). Noi passiamo solo
    # gli argomenti passati dall'utente (saltiamo il path dell'eseguibile).
    if hasattr(cli_module, "main"):
        return cli_module.main(argv[1:])
    sys.stderr.write("[launch_cli_launcher] Modulo CLI privo di main()\n")
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
