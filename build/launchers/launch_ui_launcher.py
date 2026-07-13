"""Wrapper UI per l'exeguibile RelicToEpubUI.

Responsabilità:
1. Reindirizza stdout/stderr su un file log in ``AppData\\Local\\RelicToEpub\\logs``
   (utile perché l'app è avviata con subsystem "windows" per il doppio-click
   senza finestra console).
2. Verifica l'env var ``RELICTOEPUB_BOOT_OK=1`` impostata dal bootstrap GPU.
3. Aggiunge ``src`` al ``sys.path`` (per compatibilità con il bundle PyInstaller).
4. Lancia l'app Gradio principale (``launch_ui.py``).

Il bootstrap (``gpu_bootstrap.py``) si occupa di scaricare/installare il wheel
PyTorch corretto prima di invocare questo launcher.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

APP_VERSION = "0.1.0"


def _project_paths() -> tuple[Path, Path]:
    """Ritorna (src_dir, scripts_dir) per importare i moduli giusti."""
    # Quando bundlato da PyInstaller, _MEIPASS contiene i moduli; aggiungiamo
    # sempre la working directory e la _internal/src per compatibilità.
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
    """Reindirizza stdout/stderr su file di log persistenti. Ritorna il path."""
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
    log_dir = Path(local) / "RelicToEpub" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"ui_{datetime.now():%Y%m%d_%H%M%S}.log"

    # Apre i file in append; se fallisce, fallback a None (output a console)
    try:
        log_fp = log_path.open("a", encoding="utf-8")

        # Sostituisce stdout/stderr con una classe custom che logga + propaga.
        # Importante: ``isatty()`` deve restituire False perché stiamo scrivendo
        # su file. Uvicorn/Gradio chiamano ``isatty()`` per configurare il
        # formatter del log; se ritorniamo True (ereditato dal real stdout che
        # in subsystem "windows" potrebbe mentire) otteniamo crash con
        # ``ValueError: Unable to configure formatter 'default'``.
        class _StreamLogger:
            def __init__(self, real, log_f):
                self.real = real
                self.log_f = log_f

            def write(self, msg):
                try:
                    self.log_f.write(msg)
                    self.log_f.flush()
                except Exception:
                    pass
                try:
                    return self.real.write(msg)
                except Exception:
                    return 0

            def flush(self):
                try:
                    self.log_f.flush()
                except Exception:
                    pass
                try:
                    return self.real.flush()
                except Exception:
                    return 0

            def isatty(self):
                # Siamo su file: mai una TTY. Questo evita che uvicorn cerchi
                # di usare formattatori ANSI/Terminal.
                return False

            def fileno(self):
                # Alcune librerie (logging, ipython) chiamano fileno() per
                # identificare lo stream. Rimandiamo al real stdout se possibile.
                try:
                    return self.real.fileno()
                except (AttributeError, OSError, ValueError):
                    raise OSError("Stream non collegato a un file descriptor")

        sys.stdout = _StreamLogger(sys.__stdout__, log_fp)
        sys.stderr = _StreamLogger(sys.__stderr__, log_fp)
        return log_path
    except OSError:
        return Path()


def main() -> int:
    log_path = _setup_logging()

    if os.environ.get("RELICTOEPUB_BOOT_OK") != "1":
        sys.stderr.write(
            "[launch_ui_launcher] ERRORE: l'app deve essere avviata tramite "
            "RelicToEpubBoot.exe (gpu_bootstrap.py). RELICTOEPUB_BOOT_OK mancante.\n"
        )
        # Non blocchiamo — può essere avviato in dev mode con venv
        print(
            "[launch_ui_launcher] Avvio in modalità dev (RELICTOEPUB_BOOT_OK non settato).",
            flush=True,
        )

    project_root, src_dir = _project_paths()
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from relictoepub.ui.gradio_app import build_demo
    except ImportError as exc:
        sys.stderr.write(f"[launch_ui_launcher] Import failed: {exc}\n")
        return 2

    host = os.environ.get("RELICTOEPUB_HOST", "127.0.0.1")
    port_str = os.environ.get("RELICTOEPUB_PORT", "7860")
    try:
        port = int(port_str)
    except ValueError:
        port = 7860

    if log_path:
        sys.stdout.write(f"[launch_ui_launcher] Log: {log_path}\n")

    demo = build_demo()
    demo.queue()
    sys.stdout.write(f"\n  RelicToEpub UI pronta su http://{host}:{port}\n\n")
    sys.stdout.flush()

    demo.launch(
        server_name=host,
        server_port=port,
        share=False,
        inbrowser=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
