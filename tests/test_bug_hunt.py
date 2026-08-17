"""Sanity / exploratory checks for the bug-hunt session.

Questi test *positivi* documentano comportamenti verificati durante
l'analisi; per il fix vero e proprio vedi ``test_clean_text_B_52_...``
in ``test_postprocess.py``.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_chapter_split_with_h1_and_no_chapter_pages() -> None:
    """Con ``chapter_pages=None`` e H1 presenti, ritorna H1 chapters."""
    from relictoepub.compile.build_epub import _split_into_chapters
    md = "# A\n\nT1.\n\n# B\n\nT2.\n\n# C\n\nT3."
    chapters = _split_into_chapters(md, chapter_pages=None)
    titles = [c["title"] for c in chapters]
    assert titles == ["A", "B", "C"]


def test_unicode_title_in_chapter_xml_escape() -> None:
    """``_xml_escape`` deve convertire & < > in entities per XHTML."""
    from relictoepub.compile.build_epub import _xml_escape
    assert _xml_escape("A & B") == "A &amp; B"
    assert _xml_escape("A < B") == "A &lt; B"
    assert _xml_escape("A > B") == "A &gt; B"
    assert _xml_escape("A & B < C > D") == "A &amp; B &lt; C &gt; D"


def test_unique_manifest_ids_with_colliding_stems(tmp_path: Path) -> None:
    """Due immagini con stem collidente non devono produrre id duplicati."""
    from PIL import Image
    a = tmp_path / "fig.png"
    b = tmp_path / "fig.jpg"
    Image.new("RGB", (50, 50), (0, 0, 0)).save(a)
    Image.new("RGB", (50, 50), (255, 255, 255)).save(b, format="JPEG")

    import zipfile

    from relictoepub.compile.build_epub import BookMetadata, build_epub
    out = tmp_path / "book.epub"
    build_epub(
        markdown="# T\n\nX.",
        images=[a, b],
        metadata=BookMetadata(title="T"),
        output_path=out,
    )
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    ids = re.findall(r'<item\s+id="([^"]+)"', opf)
    duplicates = [i for i in set(ids) if ids.count(i) > 1]
    assert not duplicates, f"ID duplicati: {duplicates}"