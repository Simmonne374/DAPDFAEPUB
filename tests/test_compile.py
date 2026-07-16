"""Test per ``relictoepub.compile`` (Modulo 5).

La compilazione EPUB richiede ``pypandoc`` + ``pandoc`` installati.
Se non disponibili, i test vengono skippati esplicitamente.
"""

from __future__ import annotations

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


# ----------------------------------------------------------------------
# Adaptive chapter splitting (Item 3)
# ----------------------------------------------------------------------

from relictoepub.compile.build_epub import _split_into_chapters  # noqa: E402


def _make_md(n_h1: int = 0, n_h2: int = 0, n_pages: int = 0) -> str:
    """Costruisce un markdown sintetico con N H1 + N H2 + N pagebreaks."""
    parts: list[str] = []
    for i in range(n_h1):
        parts.append(f"# Capitolo {i + 1}\n\nTesto del capitolo {i + 1}.\n\n")
    for i in range(n_h2):
        parts.append(f"## Sezione {i + 1}\n\nTesto della sezione {i + 1}.\n\n")
    for i in range(n_pages):
        parts.append(f"Contenuto pagina {i + 1}.\n\n<!-- pagebreak -->\n\n")
    return "".join(parts)


def test_chapter_split_adaptive_uses_h1() -> None:
    """Con ≥3 H1 lo splitter deve usare gli H1 come confini di capitolo."""
    md = _make_md(n_h1=4)
    chapters = _split_into_chapters(md)
    titles = [c["title"] for c in chapters]
    assert titles[:4] == ["Capitolo 1", "Capitolo 2", "Capitolo 3", "Capitolo 4"]


def test_chapter_split_adaptive_falls_back_to_h2() -> None:
    """Senza H1 ma con ≥3 H2 → usa gli H2."""
    md = _make_md(n_h1=0, n_h2=8)
    chapters = _split_into_chapters(md)
    titles = [c["title"] for c in chapters]
    assert titles == [f"Sezione {i + 1}" for i in range(8)]


def test_chapter_split_single_chapter_when_no_headings() -> None:
    """Senza heading → singolo capitolo con tutto il testo."""
    md = "Solo testo piano senza nessun heading.\n\nAncora testo."
    chapters = _split_into_chapters(md)
    assert len(chapters) == 1
    assert chapters[0]["title"] == ""
    assert "Solo testo piano" in chapters[0]["body"]


def test_chapter_split_page_grouping() -> None:
    """Con ``chapter_pages=3`` e 9 pagebreaks → 3 capitoli."""
    md = _make_md(n_pages=9)
    chapters = _split_into_chapters(md, chapter_pages=3)
    assert len(chapters) == 3
    assert [c["title"] for c in chapters] == [
        "Pagine 1-3",
        "Pagine 4-6",
        "Pagine 7-9",
    ]


def test_chapter_split_prefers_h1_over_page_grouping() -> None:
    """H1 batte page-grouping anche se chapter_pages è settato."""
    md = _make_md(n_h1=5, n_pages=10)
    chapters = _split_into_chapters(md, chapter_pages=2)
    titles = [c["title"] for c in chapters]
    assert titles[:5] == [f"Capitolo {i + 1}" for i in range(5)]


def test_chapter_split_legacy_pagebreaks_when_no_headings() -> None:
    """Senza heading né ``chapter_pages`` → fallback page-per-chapter."""
    md = _make_md(n_pages=5)
    chapters = _split_into_chapters(md)
    assert len(chapters) == 5


def test_chapter_split_empty_input() -> None:
    """Input vuoto → lista vuota (nessun capitolo)."""
    assert _split_into_chapters("") == []
    assert _split_into_chapters("   \n\n  ") == []


def test_book_metadata_chapter_pages_field() -> None:
    """``BookMetadata`` deve esporre ``chapter_pages`` come campo opzionale."""
    m = BookMetadata(title="T", chapter_pages=10)
    assert m.chapter_pages == 10
    m_default = BookMetadata(title="T")
    assert m_default.chapter_pages is None


def test_build_epub_uses_h1_chapter_titles_in_toc(tmp_path: Path) -> None:
    """Gli H1 del markdown devono comparire come titoli nel TOC del EPUB."""
    md = (
        "# Introduzione\n\nTesto introduttivo.\n\n"
        "# Capitolo Alpha\n\nTesto alpha.\n\n"
        "# Capitolo Beta\n\nTesto beta.\n\n"
        "# Epilogo\n\nFine."
    )
    out = tmp_path / "book.epub"
    build_epub(
        markdown=md,
        images=[],
        metadata=BookMetadata(title="Test"),
        output_path=out,
    )
    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Introduzione" in nav
    assert "Capitolo Alpha" in nav
    assert "Capitolo Beta" in nav
    assert "Epilogo" in nav