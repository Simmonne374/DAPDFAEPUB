"""Resume/checkpoint della pipeline OCR.

Salva lo stato di avanzamento dell'OCR su disco in formato JSON,
permettendo di riprendere conversioni interrotte (Ctrl+C, crash, OOM)
senza dover ri-eseguire la fase lenta di inferenza.

Flusso:
    1. CLI calcola SHA256 del PDF sorgente → ``source_pdf_sha256``.
    2. Cerca ``{output_dir}/.relictoepub_checkpoints/state.json``.
    3. Se esiste e SHA combacia → carica ``completed_batches`` e
       riusa :class:`Pipeline` saltando i batch già fatti.
    4. Se SHA NON combacia → errore esplicito (l'utente deve cancellare
       il checkpoint oppure usare ``--no-resume``).
    5. Ogni batch completato scrive atomicamente lo stato aggiornato
       (tmp file + rename) → se il processo è killato a metà write,
       il vecchio checkpoint rimane intatto.

Lo schema su disco::

    {
      "version": 1,
      "source_pdf_sha256": "sha256:...",
      "source_pdf_size_bytes": 1234567,
      "total_batches": 42,
      "batch_size": 5,
      "completed_batches": [0, 1, 2],
      "batch_markdown": {
        "0": "<p>markdown...</p>",
        "1": "..."
      },
      "created_at": "2026-08-05T14:00:00Z",
      "updated_at": "2026-08-05T14:03:11Z"
    }

Note di design:

* Lo stato contiene **solo** il testo OCR normalizzato per batch
  (markdown finale, non raw text). Lo SHA256 cambia se cambiamo
  la logica di post-processing → in tal caso ``build_epub`` ricomputerebbe
  ma noi vogliamo coerenza con cosa è stato emesso al primo run.
* Scriviamo in ``.relictoepub_checkpoints/`` di fianco al PDF
  (non ``/tmp``) così sopravvive a reboot della macchina.
* Thread-safe via :class:`threading.Lock` perché Gradio può
  triggersave da thread multipli se l'utente clicca cancel + resume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CHECKPOINT_DIRNAME = ".relictoepub_checkpoints"
CHECKPOINT_FILENAME = "state.json"
CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class CheckpointState:
    """Stato serializzabile di una conversione in corso.

    Attributes:
        source_pdf_sha256: SHA256 esadecimale del PDF sorgente, prefixed ``"sha256:"``.
            Usato per validare che il checkpoint appartiene a quel PDF.
        source_pdf_size_bytes: dimensione in byte del PDF (per diagnostic).
        total_batches: numero totale di batch previsto dalla pipeline.
        batch_size: pagine per batch (coerente con ``max_pages_per_batch``).
        completed_batches: indici 0-based dei batch la cui OCR è stata
            completata e il cui testo finale è in ``batch_markdown``.
        batch_markdown: dict ``batch_idx → markdown normalizzato``
            (la stessa stringa che andrebbe passata a ``build_epub``).
        created_at: ISO 8601 UTC.
        updated_at: ISO 8601 UTC.
    """

    source_pdf_sha256: str
    source_pdf_size_bytes: int
    total_batches: int
    batch_size: int
    completed_batches: list[int] = field(default_factory=list)
    batch_markdown: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["version"] = CHECKPOINT_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckpointState:
        """Deserializza da dict, accettando versioni future compatibili."""
        version = d.get("version", 1)
        if version != CHECKPOINT_VERSION:
            raise ValueError(
                f"Checkpoint version {version} non supportata "
                f"(attesa: {CHECKPOINT_VERSION})"
            )
        # Strip keys non riconosciute per forward-compat
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


class CheckpointMismatchError(RuntimeError):
    """Il checkpoint esiste ma appartiene a un PDF diverso."""


class CheckpointConfigMismatchError(RuntimeError):
    """Il checkpoint esiste ma è stato creato con un ``batch_size``
    (``pages_per_batch``) diverso da quello attuale.

    Cambiare ``--pages-per-batch`` tra due run sulla stessa PDF mescola
    silenziosamente le pagine nei batch cached: la pipeline riusa il
    markdown di un batch da N pagine come se fosse da M pagine. Il
    resume deve quindi rifiutare esplicitamente la situazione per evitare
    un EPUB finale con pagine in ordine sbagliato.
    """


class CheckpointStore:
    """Persistenza thread-safe di uno stato checkpoint.

    Scrive atomicamente (tmp + ``os.replace``) per garantire che un
    crash a metà write non corrompa il file di stato.

    Example:
        >>> store = CheckpointStore(Path("book.relictoepub_checkpoints"))
        >>> state = CheckpointState(
        ...     source_pdf_sha256=compute_pdf_sha256(Path("book.pdf")),
        ...     source_pdf_size_bytes=os.path.getsize("book.pdf"),
        ...     total_batches=10, batch_size=5,
        ... )
        >>> store.save(state)
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._path = self.directory / CHECKPOINT_FILENAME
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> CheckpointState | None:
        """Carica lo stato da disco. ``None`` se non esiste."""
        with self._lock:
            if not self._path.is_file():
                return None
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                return CheckpointState.from_dict(data)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                logger.warning(
                    "Checkpoint corrotto o illeggibile (%s): %s. "
                    "Verrà ignorato e si riparte da zero.",
                    self._path, exc,
                )
                return None

    def save(self, state: CheckpointState) -> None:
        """Salva atomicamente. Crea la directory se non esiste.

        Uso:

            >>> ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            >>> updated = dataclasses.replace(state, updated_at=ts)
            >>> store.save(updated)
        """
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = state.to_dict()
            # Scrittura atomica: tmp + fsync + replace. Garantisce
            # che ``self._path`` punti sempre a una versione completa
            # o alla vecchia (mai a un file troncato).
            fd, tmp_path = tempfile.mkstemp(
                prefix=".state-", suffix=".tmp", dir=str(self.directory)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._path)
            except Exception:
                # Cleanup tmp in caso di errore
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def clear(self) -> None:
        """Rimuove il checkpoint. Idempotente."""
        with self._lock:
            if self._path.is_file():
                self._path.unlink()


def compute_pdf_sha256(pdf_path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Calcola ``"sha256:<hexdigest>"`` del PDF a chunk di 1 MB.

    Args:
        pdf_path: Path al PDF.
        chunk_size: dimensione chunk lettura in byte (default 1 MiB).

    Returns:
        ``"sha256:" + 64 char hex digest``.

    Raises:
        FileNotFoundError: se il PDF non esiste.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF non trovato: {pdf_path}")
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return f"sha256:{h.hexdigest()}"


def resolve_checkpoint_dir(pdf_path: Path) -> Path:
    """Default checkpoint dir: ``{pdf_dir}/.relictoepub_checkpoints``."""
    return Path(pdf_path).parent / CHECKPOINT_DIRNAME


def _iso_utc_now() -> str:
    """ISO 8601 UTC, secondo precision (es. ``2026-08-05T14:03:11Z``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_checkpoint_state(
    pdf_path: Path,
    *,
    total_batches: int,
    batch_size: int,
) -> CheckpointState:
    """Costruisce un CheckpointState iniziale (vuoto).

    Helper che centralizza il calcolo dello SHA256 + timestamp.
    """
    return CheckpointState(
        source_pdf_sha256=compute_pdf_sha256(pdf_path),
        source_pdf_size_bytes=os.path.getsize(pdf_path),
        total_batches=total_batches,
        batch_size=batch_size,
        created_at=_iso_utc_now(),
        updated_at=_iso_utc_now(),
    )


__all__ = [
    "CHECKPOINT_DIRNAME",
    "CHECKPOINT_FILENAME",
    "CHECKPOINT_VERSION",
    "CheckpointConfigMismatchError",
    "CheckpointMismatchError",
    "CheckpointState",
    "CheckpointStore",
    "compute_pdf_sha256",
    "new_checkpoint_state",
    "resolve_checkpoint_dir",
]
