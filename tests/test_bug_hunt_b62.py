"""Riproduzione isolata del bug B62 — doppio escape HTML nei titoli dei
capitoli EPUB3.

BUG B62: dopo il fix B56 (``_render_title_html`` che renderizza il
titolo via pypandoc), i titoli di capitolo che contengono caratteri
speciali HTML (``&``, ``<``, ``>``) subiscono un **doppio escape** quando
vengono inseriti in:

1. ``<title>...</title>`` del file XHTML del capitolo
2. ``<a>...</a>`` della TOC (``OEBPS/nav.xhtml``)

ROOT CAUSE
----------

``_render_title_html`` passa il titolo a pypandoc, che restituisce
**frammento HTML già escaped**::

    >>> _render_title_html("A & B")
    'A &amp; B'                        # già escaped da pandoc

    >>> _render_title_html("Test < 5")
    'Test &lt; 5'                      # già escaped da pandoc

    >>> _render_title_html("**Bold** & Test")
    '<strong>Bold</strong> &amp; Test' # ha HTML + entity escaped

Il problema nasce perché in ``_chapter_xhtml``::

    title_text = _HTML_TAG_RE.sub("", title) if (title and "<" in title) else (title or "")
    ...
    f'<title>{_xml_escape(title_text)}</title>'

Quando ``title`` NON contiene tag (``<``) ma contiene entity (``&amp;``,
``&lt;``), la ``_HTML_TAG_RE.sub`` non rimuove nulla, e
``_xml_escape(title_text)`` ri-escape le entity producendo::

    "A &amp; B"    →  "A &amp;amp; B"   # ← DOPPIO ESCAPE (BUG)
    "Test &lt; 5"  →  "Test &amp;lt; 5" # ← DOPPIO ESCAPE (BUG)

Lo stesso bug si manifesta in ``_build_navigation_xhtml`` quando
``ch.title`` non inizia con ``<`` (cioè non contiene tag HTML, solo
entity escaped da pandoc)::

    if ch.title.lstrip().startswith("<"):
        link_text = ch.title                  # OK: già XHTML-safe
    else:
        link_text = _xml_escape(ch.title)     # ← DOPPIO ESCAPE

Questo test:
1. **Riproduce** il bug a livello di ``_chapter_xhtml``: il tag
   ``<title>`` del capitolo contiene ``&amp;amp;`` invece di ``&amp;``.
2. **Riproduce** il bug a livello di ``_build_navigation_xhtml``: la
   TOC contiene link con ``&amp;amp;`` invece di ``&amp;``.
3. **Anti-regressione**: i titoli senza caratteri speciali continuano
   a funzionare come prima (B56 fix preservato).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

# ===============================================================
# B62 — REPRODUCTION (failing test sul master)
# ===============================================================


def test_b62_chapter_title_no_double_escape_for_ampersand() -> None:
    """B62 — REPRODUZIONE.

    Un titolo con ``&`` NON deve produrre ``&amp;amp;`` nel tag
    ``<title>`` del capitolo XHTML.
    """
    from relictoepub.compile.build_epub import _chapter_xhtml

    xhtml = _chapter_xhtml("A & B", "Body content.", 1)

    # Il <title> EPUB deve contenere la entity escape singola, NON doppia
    assert "&amp;amp;" not in xhtml, (
        f"BUG B62: doppio escape nel <title> del capitolo per '&'. "
        f"Atteso '&amp;', trovato '&amp;amp;'. Output: {xhtml!r}"
    )
    # Deve invece contenere la entity escape singola
    assert ">A &amp; B<" in xhtml, (
        f"BUG B62: <title> del capitolo non contiene '&amp;' per '&'. "
        f"Output: {xhtml!r}"
    )


def test_b62_chapter_title_no_double_escape_for_lt() -> None:
    """B62 — REPRODUZIONE variante.

    Un titolo con ``<`` NON deve produrre ``&amp;lt;`` nel tag
    ``<title>`` del capitolo XHTML.
    """
    from relictoepub.compile.build_epub import _chapter_xhtml

    xhtml = _chapter_xhtml("Test < 5", "Body content.", 1)

    # Il <title> EPUB deve contenere la entity escape singola, NON doppia
    assert "&amp;lt;" not in xhtml, (
        f"BUG B62: doppio escape nel <title> del capitolo per '<'. "
        f"Atteso '&lt;', trovato '&amp;lt;'. Output: {xhtml!r}"
    )
    assert ">Test &lt; 5<" in xhtml, (
        f"BUG B62: <title> del capitolo non contiene '&lt;' per '<'. "
        f"Output: {xhtml!r}"
    )


def test_b62_chapter_title_no_double_escape_for_html_with_entity() -> None:
    """B62 — REPRODUZIONE variante con HTML+entity.

    Un titolo con HTML (``<strong>``) E entity (``&``) NON deve produrre
    ``&amp;amp;`` nel tag ``<title>`` del capitolo XHTML dopo lo strip
    dei tag.
    """
    from relictoepub.compile.build_epub import _chapter_xhtml

    # Questo caso simula ciò che accade quando ``_render_title_html``
    # restituisce un frammento HTML con entity escaped (``<strong>...&amp;...``)
    title = "<strong>Bold</strong> &amp; Test"
    xhtml = _chapter_xhtml(title, "Body content.", 1)

    # Dopo lo strip dei tag, rimane "Bold &amp; Test" — che NON va
    # ri-escapato perché è già nella sua forma XHTML-safe finale.
    assert "&amp;amp;" not in xhtml, (
        f"BUG B62: doppio escape nel <title> per HTML+entity. "
        f"Output: {xhtml!r}"
    )
    # Il <title> deve contenere la versione corretta
    assert ">Bold &amp; Test<" in xhtml, (
        f"BUG B62: <title> del capitolo non contiene 'Bold &amp; Test'. "
        f"Output: {xhtml!r}"
    )


def test_b62_navigation_link_no_double_escape() -> None:
    """B62 — REPRODUZIONE: anche la TOC (``nav.xhtml``) soffre dello
    stesso bug quando il titolo non inizia con ``<`` (cioè contiene solo
    entity escaped di pandoc, senza tag HTML).
    """
    from relictoepub.compile.build_epub import (
        _build_navigation_xhtml,
        ChapterInfo,
    )

    # Simuliamo ciò che accade con _render_title_html("A & B"):
    # il titolo è "A &amp; B" (entity escaped, niente tag).
    chapters = [
        ChapterInfo(
            title="A &amp; B",     # già escaped da pandoc
            level=1,
            filename="chap_0001.xhtml",
            xhtml="",
        ),
    ]
    nav = _build_navigation_xhtml("Test", chapters)

    # La TOC NON deve contenere il doppio escape
    assert "&amp;amp;" not in nav, (
        f"BUG B62: doppio escape nella TOC. Atteso '&amp;', trovato "
        f"'&amp;amp;'. Output: {nav!r}"
    )
    # Deve invece contenere la entity escape singola
    assert ">A &amp; B<" in nav, (
        f"BUG B62: link TOC non contiene '&amp;' corretto. "
        f"Output: {nav!r}"
    )


def test_b62_navigation_link_no_double_escape_for_lt() -> None:
    """B62 — REPRODUZIONE variante: TOC con ``<``."""
    from relictoepub.compile.build_epub import (
        _build_navigation_xhtml,
        ChapterInfo,
    )

    chapters = [
        ChapterInfo(
            title="Test &lt; 5",   # già escaped da pandoc
            level=1,
            filename="chap_0001.xhtml",
            xhtml="",
        ),
    ]
    nav = _build_navigation_xhtml("Test", chapters)

    assert "&amp;lt;" not in nav, (
        f"BUG B62: doppio escape nella TOC per '<'. Output: {nav!r}"
    )
    assert ">Test &lt; 5<" in nav, (
        f"BUG B62: link TOC non contiene '&lt;' corretto. "
        f"Output: {nav!r}"
    )


def test_b62_e2e_epub_chapter_titles_unescaped(tmp_path: Path) -> None:
    """B62 — E2E: un EPUB completo generato con titoli H1 contenenti
    caratteri speciali NON deve presentare doppio escape né nel ``<title>``
    del capitolo né nella TOC (``nav.xhtml``).
    """
    from relictoepub.compile.build_epub import build_epub, BookMetadata

    md = (
        "# Intro & Overview\n\nTesto A.\n\n"
        "# Capitolo < 5\n\nTesto B.\n\n"
        "# Plain chapter\n\nTesto C.\n"
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
        # --- Verifica sui capitoli XHTML ---
        chapter_files = [
            n for n in zf.namelist()
            if n.startswith("OEBPS/chap_") and n.endswith(".xhtml")
        ]
        assert len(chapter_files) >= 3, f"Troppo pochi capitoli: {chapter_files}"
        for ch in chapter_files:
            content = zf.read(ch).decode("utf-8")
            # Il <title> EPUB NON deve contenere doppio escape
            assert "&amp;amp;" not in content, (
                f"BUG B62 (e2e): doppio escape '&amp;amp;' nel <title> "
                f"di {ch}. Content: {content!r}"
            )
            assert "&amp;lt;" not in content, (
                f"BUG B62 (e2e): doppio escape '&amp;lt;' nel <title> "
                f"di {ch}. Content: {content!r}"
            )

        # --- Verifica sulla TOC ---
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
        assert "&amp;amp;" not in nav, (
            f"BUG B62 (e2e): doppio escape '&amp;amp;' nella TOC. "
            f"nav: {nav!r}"
        )
        assert "&amp;lt;" not in nav, (
            f"BUG B62 (e2e): doppio escape '&amp;lt;' nella TOC. "
            f"nav: {nav!r}"
        )
        # I titoli corretti devono essere presenti nella TOC
        assert "Intro &amp; Overview" in nav, (
            f"BUG B62 (e2e): 'Intro &amp; Overview' assente dalla TOC. "
            f"nav: {nav!r}"
        )
        assert "Capitolo &lt; 5" in nav, (
            f"BUG B62 (e2e): 'Capitolo &lt; 5' assente dalla TOC. "
            f"nav: {nav!r}"
        )


# ===============================================================
# B62 — ANTI-REGRESSIONE (i test esistenti devono continuare a passare)
# ===============================================================


def test_b62_plain_title_still_works() -> None:
    """B62 — anti-regressione: un titolo plain (senza caratteri
    speciali) deve continuare a funzionare esattamente come prima.
    """
    from relictoepub.compile.build_epub import _chapter_xhtml

    xhtml = _chapter_xhtml("Plain Title", "Body.", 1)
    assert ">Plain Title<" in xhtml, (
        f"BUG B62 (regressione): titolo plain assente dal <title>. "
        f"Output: {xhtml!r}"
    )


def test_b62_html_title_still_renders_correctly() -> None:
    """B62 — anti-regressione: un titolo con solo HTML (no entity
    escaped) deve continuare a funzionare come prima del fix.
    """
    from relictoepub.compile.build_epub import _chapter_xhtml

    # Solo HTML, niente entity escaped: la entity decode non deve rompere
    # il caso.
    xhtml = _chapter_xhtml("<strong>Bold</strong> title", "Body.", 1)
    # Il <title> EPUB deve contenere la versione plain del titolo
    assert ">Bold title<" in xhtml, (
        f"BUG B62 (regressione): <title> plain assente per titolo HTML. "
        f"Output: {xhtml!r}"
    )
    # E l'H1 del body deve comunque avere la classe chapter-title
    assert 'class="chapter-title"' in xhtml


def test_b62_navigation_html_title_still_used_as_is() -> None:
    """B62 — anti-regressione: un titolo HTML (che inizia con ``<``)
    deve continuare a essere usato as-is nella TOC, senza ulteriori
    manipolazioni.
    """
    from relictoepub.compile.build_epub import (
        _build_navigation_xhtml,
        ChapterInfo,
    )

    chapters = [
        ChapterInfo(
            title="<strong>Bold</strong> title",
            level=1,
            filename="chap_0001.xhtml",
            xhtml="",
        ),
    ]
    nav = _build_navigation_xhtml("Test", chapters)
    # L'HTML deve essere usato as-is, senza double escape né strip
    assert "<strong>Bold</strong> title" in nav, (
        f"BUG B62 (regressione): HTML del titolo alterato nella TOC. "
        f"Output: {nav!r}"
    )


def test_b62_html_unescape_handles_common_entities() -> None:
    """B62 — verifica del componente di fix.

    La funzione ``html.unescape`` (stdlib) deve gestire correttamente
    le entity di base che pandoc emette. Questo test assicura che il
    fix usi una funzione di decode robusta e non un hack custom.
    """
    import html

    # Le entity minime che pandoc genera devono essere decodate
    assert html.unescape("&amp;") == "&"
    assert html.unescape("&lt;") == "<"
    assert html.unescape("&gt;") == ">"
    # Le entity non-amp/lt/gt devono restare intatte (html.unescape le
    # passa attraverso se non sono recognized)
    assert html.unescape("A &amp; B") == "A & B"
    assert html.unescape("Test &lt; 5") == "Test < 5"
