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
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

# Versione letta dinamicamente dal pacchetto (single source of truth:
# pyproject.toml). In caso di bundle PyInstaller che non include i metadata
# importlib.metadata cade sul fallback "0.0.0+unknown".
try:
    from relictoepub import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "0.0.0+unknown"

# Lettere di unita storicamente non scrivibili su Windows moderni (floppy).
# Vengono usate in _self_check per intercettare shortcut orfani che puntano
# a unita non piu presenti (es. bug "A:\\RelicToEpub" -> CreateProcess: 5).
_LETTERE_FLOPPY = {"A", "B"}


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
        pass  # diagnostico, mai bloccante


def _show_repair_dialog(detail: str) -> None:
    """Mostra una finestra tkinter con istruzioni chiare per riparare l'installazione.

    Usata quando _self_check rileva problemi gravi (es. exe inesistente,
    percorso di installazione invalido). L'utente vede cosa e andato storto
    invece del generico "Impossibile eseguire il file: A:\\RelicToEpub".
    """
    try:
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
        log_dir = Path(local) / "RelicToEpub" / "logs"
    except OSError:
        log_dir = Path(".")  # noqa: F841 (solo placeholder)

    root = tk.Tk()
    root.title(f"RelicToEpub {APP_VERSION} - Problema di avvio")
    root.geometry("560x280")
    root.resizable(False, False)
    # Impedisci chiusura con X finestra (l'utente deve cliccare "Chiudi")
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    try:
        # Icona di errore (cross rossa) — nativo tkinter, niente deps
        root.iconbitmap(default="")  # lascia l'icona di default
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    icon_label = ttk.Label(frame, text="\u26A0", font=("Segoe UI", 28))
    icon_label.pack(side="left", anchor="n", padx=(0, 16))

    text_frame = ttk.Frame(frame)
    text_frame.pack(side="left", fill="both", expand=True)

    title_label = ttk.Label(
        text_frame,
        text="L'applicazione non riesce ad avviarsi",
        font=("Segoe UI", 11, "bold"),
    )
    title_label.pack(anchor="w")

    detail_label = ttk.Label(
        text_frame,
        text=detail,
        wraplength=400,
        justify="left",
    )
    detail_label.pack(anchor="w", pady=(8, 8))

    help_label = ttk.Label(
        text_frame,
        text=(
            "Procedura di ripristino:\n"
            "1. Apri Impostazioni di Windows -> App -> RelicToEpub -> Disinstalla.\n"
            "2. Rilancia l'installer RelicToEpub-Setup scaricato dal sito ufficiale.\n"
            "3. Se il problema persiste, apri una segnalazione e allega il file:\n"
            "   %LOCALAPPDATA%\\RelicToEpub\\logs\\launcher_selfcheck.log"
        ),
        wraplength=400,
        justify="left",
        foreground="#404040",
    )
    help_label.pack(anchor="w", pady=(4, 4))

    btn = ttk.Button(text_frame, text="Chiudi", command=root.destroy)
    btn.pack(anchor="e", pady=(12, 0))

    root.mainloop()


def _self_check() -> bool:
    """Verifica che il percorso di esecuzione sia realistico.

    Ritorna True se va tutto bene. False se abbiamo rilevato un problema serio
    (in tal caso _run_with_selfcheck decide se mostrare una UI di riparazione
    o solo loggare e procedere).

    Cosa controlla:
    1. ``sys.executable`` esiste sul disco (non e un fantasma).
    2. Non e su una unita floppy (A:\\, B:\\) che potrebbe essere scollegata.
    3. La cartella di installazione contiene almeno un file .expected.txt
       o l'exe di bootstrap (sanity check minimo).
    """
    try:
        exe = Path(sys.executable)
    except (OSError, ValueError) as exc:
        _log_diagnostic(f"sys.executable non recuperabile: {exc}")
        return False

    # Test 1: il file esiste davvero?
    if not exe.exists():
        _log_diagnostic(f"sys.executable inesistente: {exe}")
        return False

    # Test 2: drive letter sospetto? (A: o B: sono floppy disk storici)
    drive = exe.drive or ""
    if drive.rstrip(":").upper() in _LETTERE_FLOPPY:
        _log_diagnostic(
            f"sys.executable punta a unita floppy {drive!r}: {exe} "
            f"(probabile shortcut orfano a una USB rimossa)"
        )
        return False

    # Test 3: la cartella genitore deve essere coerente con un'installazione
    # di RelicToEpub. Controlliamo che esista almeno il bootstrap (RelicToEpubBoot.exe)
    # o un file _internal/ che ci si aspetta dal bundle PyInstaller.
    parent = exe.parent
    expected_markers = [
        parent / "RelicToEpubBoot.exe",
        parent / "_internal",
        parent / "RelicToEpubUI.exe",  # siamo RelicToEpubUI.exe
        parent / "RelicToEpubCLI.exe",  # oppure CLI
    ]
    if not any(m.exists() for m in expected_markers):
        _log_diagnostic(
            f"Nessun marker di installazione RelicToEpub trovato in {parent}. "
            f"exe={exe}; cartella probabilmente errata."
        )
        return False

    _log_diagnostic(f"Self-check OK: {exe}")
    return True


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

    # Self-check: intercetta installazioni corrotte o shortcut orfani.
    # In modalita dev (sys.executable = python.exe) lo skippiamo per
    # non rompere il workflow degli sviluppatori.
    is_bundled = getattr(sys, "frozen", False) or (
        "RelicToEpub" in sys.executable
        and Path(sys.executable).suffix.lower() == ".exe"
    )
    if is_bundled and not _self_check():
        detail = (
            "Il file di avvio dell'applicazione punta a un percorso non valido "
            f"({sys.executable}).\n\n"
            "Causa probabile: l'installer e stato eseguito da una unita rimovibile "
            "(USB, floppy disk) ora scollegata, oppure l'installazione e stata "
            "spostata/cancellata dopo l'installazione."
        )
        try:
            _show_repair_dialog(detail)
        except tk.TclError:
            # Tkinter non disponibile: fallback al solo log
            _log_diagnostic(
                f"tkinter non disponibile per self-check UI; "
                f"sys.executable={sys.executable}"
            )
        return 4  # codice distinto per "self-check fallito"

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