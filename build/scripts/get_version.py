"""Stampa la versione corrente del pacchetto leggendola da pyproject.toml.

Usato da build_windows.ps1 per inoltrare la versione a ISCC quando
MYAPP_VERSION non e impostata esplicitamente.

Output: una singola riga con la stringa di versione (senza newline finale).

Fail-soft: se non riesce a leggere pyproject.toml, restituisce
"0.0.0+unknown" cosi la build locale continua a produrre un installer
(o salta con un errore comprensibile nella CI).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


def _read_pyproject(start: Path) -> str | None:
    """Cerca pyproject.toml risalendo dal percorso di partenza."""
    for d in [start, *start.parents]:
        candidate = d / "pyproject.toml"
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            m = _VERSION_RE.search(text)
            if m:
                return m.group(1)
    return None


def main() -> int:
    here = Path(__file__).resolve()
    version = _read_pyproject(here)
    if not version:
        print("0.0.0+unknown")
        return 0
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
