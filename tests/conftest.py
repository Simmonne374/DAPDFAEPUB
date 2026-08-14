"""Fixture condivise per pytest.

Genera in modo deterministico:
* Un PDF sintetico multi-pagina per i test di ingest/pipeline.
* Un'immagine PNG di test per i test di bbox/webp.

Aggiunge anche un flag CLI ``--update-golden`` che consente di
rigenerare i golden file EPUB quando il comportamento inteso cambia.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Aggiunge ``src/`` al sys.path così i test possono fare ``import relictoepub.*``
# anche se il progetto non è installato in modalità editable.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw


def pytest_addoption(parser: pytest.Parser) -> None:
    """Aggiunge l'opzione ``--update-golden`` per rigenerare i golden EPUB."""
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help=(
            "Rigenera i golden file .epub in tests/fixtures/golden/ "
            "sovrascrivendoli con l'output corrente. Usare quando una "
            "modifica intenzionale al formato EPUB e stata approvata."
        ),
    )


@pytest.fixture(scope="session")
def update_golden_flag(request: pytest.FixtureRequest) -> bool:
    """Espone il flag ``--update-golden`` ai test che ne hanno bisogno."""
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """Crea un PDF di 3 pagine bianche con un testo stampato."""
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    try:
        for i in range(3):
            page = doc.new_page(width=595, height=842)  # A4 in pt
            page.insert_text(
                (72, 72 + i * 20),
                f"Pagina di test {i + 1}",
                fontsize=14,
            )
        doc.save(str(pdf_path))
    finally:
        doc.close()
    return pdf_path


@pytest.fixture()
def sample_image(tmp_path: Path) -> Path:
    """Crea un'immagine PNG 600x800 con un quadrato nero al centro."""
    img_path = tmp_path / "sample.png"
    img = Image.new("RGB", (600, 800), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((100, 200, 500, 600), fill=(0, 0, 0))
    img.save(img_path)
    return img_path