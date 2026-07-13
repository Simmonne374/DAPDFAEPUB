"""Splash Tkinter per il bootstrap GPU-aware di RelicToEpub.

Visualizza una finestra modale durante l'esecuzione di ``gpu_bootstrap.py``:

* Title bar: "RelicToEpub — primo avvio in corso"
* Label di stato corrente (aggiornato ogni 500ms leggendo lo ``ProgressState``)
* Progress bar determinate (con %) per la fase di download
* Sotto-label con byte scaricati / totali / velocità / ETA
* Watchdog: se stesso aggiornamento >10 s → "Attendere, operazione in corso"

Lo splash NON blocca la UI principale: alla terminazione del bootstrap (state
``done`` o ``error``) la finestra si chiude automaticamente.
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))
from progress_state import ProgressState  # type: ignore  # noqa: E402


def _human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(n)
    for unit in units:
        if val < 1024.0:
            return f"{val:,.1f} {unit}"
        val /= 1024.0
    return f"{val:,.1f} PB"


def _human_speed(bps: float) -> str:
    return f"{_human_bytes(bps)}/s"


def _format_eta(secs: float) -> str:
    if secs <= 0 or not _is_finite(secs):
        return "—"
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _is_finite(x: float) -> bool:
    try:
        return float("-inf") < float(x) < float("inf")
    except (ValueError, TypeError):
        return False


# Messaggi human-readable per fase
PHASE_LABELS = {
    "starting": "Avvio in corso…",
    "detect_gpu": "Rilevamento hardware GPU in corso…",
    "select_wheel": "Selezione build CUDA appropriata…",
    "download_wheel": "Download del wheel PyTorch per la tua GPU",
    "install_wheel": "Installazione PyTorch (estrazione + setup)…",
    "verify": "Verifica installazione…",
    "done": "Pronto, avvio applicazione…",
    "error": "Si è verificato un errore.",
}


class SplashApp:
    """Finestra modale Tkinter che osserva ``ProgressState``."""

    POLL_MS = 500
    STALL_AFTER_S = 10.0  # watchdog: forza label "attendere prego" dopo 10s
    AUTO_CLOSE_AFTER_DONE_MS = 1500

    def __init__(self, state: ProgressState) -> None:
        self.state = state
        self.root = tk.Tk()
        self.root.title("RelicToEpub — primo avvio in corso")
        self.root.minsize(560, 220)
        self.root.resizable(False, False)
        # Centra sullo schermo
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = 560, 220
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg="#1e1e1e")

        self._build_ui()
        self.last_message = ""
        self.last_seen_update_ts = time.time()

    def _build_ui(self) -> None:
        padding = {"padx": 18, "pady": 6}

        header = tk.Label(
            self.root,
            text="RelicToEpub",
            font=("Segoe UI", 16, "bold"),
            fg="#ffffff",
            bg="#1e1e1e",
        )
        header.grid(row=0, column=0, columnspan=2, sticky="w", **padding)

        subtitle = tk.Label(
            self.root,
            text="Primo avvio in corso — potrebbe richiedere alcuni minuti.",
            font=("Segoe UI", 9),
            fg="#aaaaaa",
            bg="#1e1e1e",
        )
        subtitle.grid(row=1, column=0, columnspan=2, sticky="w", **padding)

        self.phase_label = tk.Label(
            self.root,
            text="Avvio in corso…",
            font=("Segoe UI", 10),
            fg="#ffffff",
            bg="#1e1e1e",
            anchor="w",
            wraplength=520,
            justify="left",
        )
        self.phase_label.grid(row=2, column=0, columnspan=2, sticky="we", **padding)

        self.progress = ttk.Progressbar(
            self.root, orient="horizontal", mode="determinate", maximum=100
        )
        self.progress.grid(
            row=3, column=0, columnspan=2, sticky="we", padx=18, pady=(2, 6)
        )

        self.detail_label = tk.Label(
            self.root,
            text="",
            font=("Consolas", 9),
            fg="#cccccc",
            bg="#1e1e1e",
            anchor="w",
        )
        self.detail_label.grid(row=4, column=0, columnspan=2, sticky="we", padx=18, pady=(0, 6))

        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

    # ---- Loop ----

    def run(self) -> None:
        self.root.after(self.POLL_MS, self._poll)
        self.root.mainloop()

    def _poll(self) -> None:
        s = self.state.get()
        phase = s.get("phase", "starting")
        msg = s.get("message", "") or PHASE_LABELS.get(phase, phase)
        updated_at = float(s.get("updated_at", time.time()))

        # Watchdog: se stesso aggiornamento >STALL_AFTER_S → label "attendere"
        now = time.time()
        if now - updated_at > self.STALL_AFTER_S and phase not in ("done", "error"):
            stall_msg = msg + "\nAttendere prego — operazione in corso…"
            self._set_message(stall_msg)
        else:
            self._set_message(msg)

        # Update progress bar se in download
        if phase == "download_wheel":
            total = float(s.get("total_bytes", 0))
            downloaded = float(s.get("downloaded_bytes", 0))
            if total > 0:
                pct = max(0.0, min(100.0, (downloaded / total) * 100.0))
                self.progress["value"] = pct
                speed = float(s.get("speed_bps", 0))
                eta = float(s.get("eta_seconds", 0))
                detail = (
                    f"{_human_bytes(downloaded)} / {_human_bytes(total)} "
                    f"— {_human_speed(speed)} — ETA {_format_eta(eta)}"
                )
                self.detail_label.config(text=detail)
            else:
                self.progress["mode"] = "indeterminate"
                self.progress.start(50)
                self.detail_label.config(text=f"{_human_bytes(s.get('downloaded_bytes', 0))} scaricati…")
        else:
            self.progress.stop()
            self.progress["mode"] = "determinate"
            self.progress["value"] = 100 if phase == "done" else (
                50 if phase == "verify" else 0
            )
            self.detail_label.config(text="")

        # Done → auto-close
        if phase == "done":
            self.root.after(self.AUTO_CLOSE_AFTER_DONE_MS, self.root.destroy)
            return
        if phase == "error":
            # Resta visibile finché l'utente non chiude manualmente
            self.detail_label.config(text=s.get("message", "Errore"))
            self.root.after(15000, self.root.destroy)  # chiudi dopo 15s
            return

        self.root.after(self.POLL_MS, self._poll)

    def _set_message(self, msg: str) -> None:
        if msg != self.last_message:
            self.phase_label.config(text=msg)
            self.last_message = msg


def main() -> int:
    state = ProgressState()
    # Aspetta che il primo stato sia scritto
    for _ in range(40):  # max 2 s
        if state.get().get("updated_at", 0) > 0 and state.get().get("phase") != "starting":
            break
        time.sleep(0.05)
    SplashApp(state).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
