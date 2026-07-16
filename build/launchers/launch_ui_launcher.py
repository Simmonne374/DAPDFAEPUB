"""Wrapper UI per l'eseguibile RelicToEpubUI.

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
import socket
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
        sys.stdout = log_fp  # type: ignore[assignment]
        sys.stderr = log_fp  # type: ignore[assignment]
        return log_path
    except OSError:
        return Path()


def _check_boot() -> None:
    """Verifica che il bootstrap GPU abbia completato l'installazione."""
    if os.environ.get("RELICTOEPUB_BOOT_OK") == "1":
        return
    # In dev mode (venv) il bootstrap non viene eseguito; non blocchiamo
    # ma emettiamo un avviso.
    sys.stdout.write(
        "[launch_ui_launcher] Avvio in modalità dev "
        "(RELICTOEPUB_BOOT_OK non settato).\n"
    )
    sys.stdout.flush()


def _resolve_demo_port(host: str) -> tuple[int, str]:
    """Trova una porta libera per Gradio.

    Gradio's ``launch(server_port=...)`` fallisce con ``OSError`` se la porta
    è occupata, perché di default cerca solo quella. Noi invece vogliamo
    fallback automatico su una porta vicina.

    Ritorna (port, message) dove ``message`` descrive cosa è successo (utile
    per il log).
    """
    port_str = os.environ.get("RELICTOEPUB_PORT", "7860")
    try:
        preferred_port = int(port_str)
    except ValueError:
        preferred_port = 7860
    try:
        port_scan = int(os.environ.get("RELICTOEPUB_PORT_SCAN", "20"))
    except ValueError:
        port_scan = 20

    for offset in range(port_scan):
        candidate = preferred_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, candidate))
            except OSError:
                continue
            if offset == 0:
                return candidate, ""
            return candidate, (
                f"Porta {preferred_port} occupata, uso {candidate}."
            )
    raise RuntimeError(
        f"Nessuna porta libera nell'intervallo "
        f"{preferred_port}-{preferred_port + port_scan - 1} su {host}. "
        f"Chiudi l'istanza precedente di RelicToEpub o imposta "
        f"RELICTOEPUB_PORT per usarne un'altra."
    )


def main(argv: list[str] | None = None) -> int:
    log_path = _setup_logging()
    if log_path:
        sys.stdout.write(f"[launch_ui_launcher] Log: {log_path}\n")
        sys.stdout.flush()

    _check_boot()

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

    try:
        port, port_msg = _resolve_demo_port(host)
    except RuntimeError as exc:
        sys.stderr.write(f"[launch_ui_launcher] ERRORE: {exc}\n")
        return 3
    if port_msg:
        sys.stdout.write(f"[launch_ui_launcher] {port_msg}\n")
        sys.stdout.flush()

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