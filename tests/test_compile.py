"""Test per ``relictoepub.compile`` (Modulo 5).

La compilazione EPUB richiede ``pypandoc`` + ``pandoc`` installati.
Se non disponibili, i test vengono skippati esplicitamente.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from relictoepub.compile.build_epub import (
    BookMetadata,
    ChapterInfo,
    _check_pandoc,
    build_epub,
)


# Skip automatico se pandoc non è installato
try:
    _check_pandoc()
    has_pandoc = True
except Exception:
    has_pandoc = False

pytestmark = pytest.mark.skipif(
    not has_pandoc,
    reason="pandoc non installato (richiesto per pypandoc)",
)


def test_check_pandoc_returns_true_when_installed() -> None:
    assert isinstance(_check_pandoc(), str)


def test_build_epub_minimal(tmp_path: Path) -> None:
    """Un EPUB minimale con testo solo markdown deve essere valido."""
    md = "# Capitolo 1\n\nTesto del libro.\n\n# Capitolo 2\n\nAncora testo."
    out = tmp_path / "book.epub"
    result = build_epub(
        markdown=md,
        images=[],
        metadata=BookMetadata(title="Test", author="Tester", language="it"),
        output_path=out,
    )
    assert result == out
    assert out.is_file()
    # È uno ZIP → testiamo la struttura EPUB
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert "META-INF/container.xml" in names
        assert any(n.endswith(".opf") for n in names)
        assert any(n.endswith(".xhtml") for n in names) or any(
            n.endswith(".html") for n in names
        )


def test_build_epub_with_cover(tmp_path: Path, sample_image: Path) -> None:
    """Il path del cover deve essere incluso come immagine di copertina."""
    out = tmp_path / "book.epub"
    build_epub(
        markdown="# T\n\nTesto.",
        images=[],
        metadata=BookMetadata(title="T", author="A"),
        output_path=out,
        cover_image=sample_image,
    )
    assert out.is_file()


def test_book_metadata_defaults() -> None:
    """I default di BookMetadata devono essere sensati."""
    m = BookMetadata(title="T")
    assert m.language == "it"
    assert m.identifier  # non vuoto
    assert m.title == "T"


def test_chapter_info_dataclass() -> None:
    ch = ChapterInfo(title="Cap 1", level=1, filename="chap_0001.xhtml", xhtml="xhtml_content")
    assert ch.title == "Cap 1"
    assert ch.level == 1
    assert ch.filename == "chap_0001.xhtml"
    assert ch.xhtml == "xhtml_content"