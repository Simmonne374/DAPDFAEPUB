"""Helper IPC condiviso tra ``gpu_bootstrap.py`` (writer) e ``gpu_splash.py`` (reader).

Lo stato del bootstrap viene serializzato in un file JSON in ``%TEMP%`` con
scrittura atomica (tempfile + ``os.replace``). Lo splash Tkinter lo rilegge
ogni 500 ms.

Schema JSON::

    {
        "phase": str,            # "starting" | "detect_gpu" | "select_wheel"
                                 # | "download_wheel" | "install_wheel"
                                 # | "verify" | "done" | "error"
        "message": str,          # messaggio human-readable mostrato all'utente
        "downloaded_bytes": int, # byte scaricati (solo fase download_wheel)
        "total_bytes": int,      # byte totali attesi (idem)
        "speed_bps": float,      # velocità in byte/s
        "eta_seconds": float,    # stima secondi rimanenti
        "updated_at": float      # timestamp Unix (per rilevare stall)
    }
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_PATH = (
    Path(os.environ.get("TEMP", tempfile.gettempdir()))
    / "RelicToEpubBoot"
    / "state.json"
)


class ProgressState:
    """Wrapper thread-safe-leggero per leggere/scrivere lo stato di bootstrap."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------ writer API ------

    def reset(self) -> None:
        """Inizializza lo stato con ``phase=starting`` e timestamp corrente."""
        self.set_phase("starting", message="Avvio in corso…")

    def set_phase(self, phase: str, message: str = "") -> None:
        """Aggiorna solo phase + message, preservando eventuali counters."""
        current = self._read_safe()
        current["phase"] = phase
        if message:
            current["message"] = message
        current["updated_at"] = time.time()
        self._write(current)

    def update_download(
        self,
        downloaded_bytes: int,
        total_bytes: int,
        *,
        speed_bps: float = 0.0,
        eta_seconds: float = 0.0,
    ) -> None:
        """Aggiorna i campi relativi al download (counters + fase)."""
        current = self._read_safe()
        current["phase"] = "download_wheel"
        current["downloaded_bytes"] = int(downloaded_bytes)
        current["total_bytes"] = int(total_bytes)
        current["speed_bps"] = float(speed_bps)
        current["eta_seconds"] = float(eta_seconds)
        current["updated_at"] = time.time()
        self._write(current)

    def error(self, message: str) -> None:
        current = self._read_safe()
        current["phase"] = "error"
        current["message"] = message
        current["updated_at"] = time.time()
        self._write(current)

    def done(self, message: str = "Pronto.") -> None:
        current = self._read_safe()
        current["phase"] = "done"
        current["message"] = message
        current["updated_at"] = time.time()
        self._write(current)

    # ------ reader API ------

    def get(self) -> dict[str, Any]:
        """Ritorna lo stato corrente; default vuoto se file mancante."""
        return self._read_safe()

    def seconds_since_update(self) -> float:
        return time.time() - self.get().get("updated_at", time.time())

    # ------ internals ------

    def _read_safe(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _empty_state()
            # Garantisce tutti i campi attesi
            for key, default in _empty_state().items():
                data.setdefault(key, default)
            return data
        except (json.JSONDecodeError, OSError):
            return _empty_state()

    def _write(self, data: dict[str, Any]) -> None:
        # Scrittura atomica via tempfile + os.replace
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)


def _empty_state() -> dict[str, Any]:
    return {
        "phase": "starting",
        "message": "",
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0.0,
        "eta_seconds": 0.0,
        "updated_at": time.time(),
    }


__all__ = ["ProgressState", "DEFAULT_PATH"]
