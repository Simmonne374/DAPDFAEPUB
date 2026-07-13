"""Wrapper CLI per l'exeguibile RelicToEpubCLI.

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


def main(argv: list[str]) -> int:
    log_path = _setup_logging()

    if os.environ.get("RELICTOEPUB_BOOT_OK") not in (None, "1"):
        # In dev mode BOOT_OK può essere assente; non blocchiamo
        pass

    project_root, src_dir = _project_paths()
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        import relictoepub.cli as cli_module  # type: ignore
    except ImportError:
        try:
            # Modalità dev: usa lo script convert_one.py
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
