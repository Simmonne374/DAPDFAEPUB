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
except RuntimeError:
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

from relictoepub.compile.build_epub import _split_into_chapters

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

def test_build_epub_manifest_has_unique_ids(tmp_path: Path, sample_image: Path) -> None:
    """Regression: il manifest OPF non deve contenere ``id`` duplicati.

    Bug riprodotto: quando si fornisce una ``cover_image``, sia
    ``cover.xhtml`` (capitolo cover) che ``images/cover.webp`` (risorsa
    immagine della cover) usano lo stesso ``id="cover"`` nel manifest.
    Lo standard OPF richiede ID univoci; gli e-reader e gli EpubCheck
    rifiutano il file.

    Secondo caso coperto: due immagini con stesso stem ma estensioni
    diverse (es. ``fig.jpg`` e ``fig.png``) collidono entrambe su
    ``img_fig`` se non deduplicate.
    """
    from PIL import Image
    second = tmp_path / "sample.jpg"
    Image.new("RGB", (50, 50), (0, 255, 0)).save(second, format="JPEG")
    out = tmp_path / "book.epub"
    build_epub(
        markdown="# Cap 1\n\nTesto.\n\n# Cap 2\n\nAltro testo.",
        images=[sample_image, second],
        metadata=BookMetadata(title="Test"),
        output_path=out,
        cover_image=sample_image,
    )
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    # Estrai tutti gli ``id="..."`` dalla sezione manifest
    import re
    ids = re.findall(r'<item\s+id="([^"]+)"', opf)
    duplicates = [i for i in set(ids) if ids.count(i) > 1]
    assert not duplicates, f"ID duplicati nel manifest OPF: {duplicates}"

def test_build_epub_manifest_jpeg_mime_type(tmp_path: Path) -> None:
    """Regression: ``.jpg`` deve essere dichiarato come ``image/jpeg`` nel manifest.

    Bug: ``build_epub`` costruisce il MIME type con ``image/{suffix.lstrip('.')}``,
    che produce ``image/jpg`` per i file ``.jpg``. EPUB validators rifiutano
    MIME non-standard. Il MIME corretto per JPEG è ``image/jpeg``.
    """
    from PIL import Image
    jpg_path = tmp_path / "picture.jpg"
    Image.new("RGB", (50, 50), (255, 0, 0)).save(jpg_path, format="JPEG")

    out = tmp_path / "book.epub"
    build_epub(
        markdown="# Test\n\nFoto.",
        images=[jpg_path],
        metadata=BookMetadata(title="Test"),
        output_path=out,
    )
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert 'media-type="image/jpeg"' in opf, (
        f"MIME type atteso 'image/jpeg', trovato altro:\n{opf}"
    )
    assert 'media-type="image/jpg"' not in opf, (
        "MIME type non-standard 'image/jpg' trovato nel manifest."
    )

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

# ----------------------------------------------------------------------
# EPUB3 structure regression tests (round 2 bug-hunt fixes)
# B37: ogni capitolo NON deve contenere DOCTYPE annidato / <html>/<body>
# B41: dcterms:modified deve essere dinamico (cambia tra due build)
# B40: markdown vuoto non deve produrre EPUB senza capitoli
# ----------------------------------------------------------------------

import re

def test_chapter_xhtml_has_no_nested_doctype(tmp_path: Path) -> None:
    """B37: ogni .xhtml capitolo deve essere un frammento EPUB3 valido,
    non un documento standalone con DOCTYPE annidato."""
    # Lo splitter richiede ≥3 H1 per usare la strategia heading-based
    md = "# A\n\nTesto A.\n\n# B\n\nTesto B.\n\n# C\n\nTesto C.\n\n# D\n\nTesto D."
    out = tmp_path / "book.epub"
    build_epub(markdown=md, images=[], metadata=BookMetadata(title="T"), output_path=out)
    with zipfile.ZipFile(out) as zf:
        chapter_files = [n for n in zf.namelist() if n.startswith("OEBPS/chap_") and n.endswith(".xhtml")]
        assert len(chapter_files) >= 2, f"Troppo pochi capitoli: {chapter_files}"
        for ch in chapter_files:
            content = zf.read(ch).decode("utf-8")
            # Ogni capitolo deve avere ESATTAMENTE un DOCTYPE (non più!)
            doctype_count = content.count("<!DOCTYPE")
            assert doctype_count == 1, (
                f"{ch}: trovati {doctype_count} <!DOCTYPE> (atteso 1). "
                f"BUG B37: pypandoc --standalone sta annidando il wrapper."
            )
            # Ogni capitolo deve avere ESATTAMENTE un <html> (non più!)
            html_count = content.count("<html")
            assert html_count == 1, (
                f"{ch}: trovati {html_count} <html> (atteso 1). "
                f"BUG B37: frammento non riusabile come XHTML EPUB3."
            )

def test_dcterms_modified_is_dynamic(tmp_path: Path) -> None:
    """B41: dcterms:modified non deve essere hardcoded — cambia ad ogni build.

    Verifica che il timestamp sia (a) un valore ISO 8601 valido recente,
    e (b) NON sia la stringa hardcoded ``2026-07-06`` che era il bug originale.
    """
    import re as _re

    md = "# Cap\n\nTesto."
    out = tmp_path / "book.epub"
    build_epub(markdown=md, images=[], metadata=BookMetadata(title="T"), output_path=out)
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")

    # Estrai il valore di dcterms:modified
    match = _re.search(r'<meta property="dcterms:modified">([^<]+)</meta>', opf)
    assert match, "dcterms:modified non trovato in content.opf"
    modified = match.group(1)

    # B41: NON deve essere la stringa hardcoded del vecchio bug
    assert modified != "2026-07-06T00:00:00Z", (
        f"BUG B41: dcterms:modified è hardcoded a {modified!r}"
    )
    # Deve essere un timestamp ISO 8601 valido (YYYY-MM-DDTHH:MM:SSZ)
    assert _re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", modified), (
        f"Formato dcterms:modified inatteso: {modified!r}"
    )
    # Deve essere "recente" (entro 1h dalla generazione) — questo conferma
    # che è stato generato al runtime, non cablato nel codice.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    file_dt = datetime.strptime(modified, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    delta = abs((now - file_dt).total_seconds())
    assert delta < 3600, (
        f"dcterms:modified troppo lontano da ora: {modified!r} vs now={now.isoformat()}"
    )

def test_build_epub_empty_markdown_fallback(tmp_path: Path) -> None:
    """B40: con markdown vuoto/whitespace, build_epub deve produrre
    almeno un capitolo (fallback 'Empty') invece di EPUB vuoto."""
    out = tmp_path / "empty.epub"
    build_epub(
        markdown="",  # o "   \n\n   "
        images=[],
        metadata=BookMetadata(title="Empty book"),
        output_path=out,
    )
    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        chapter_files = [n for n in names if n.startswith("OEBPS/chap_") and n.endswith(".xhtml")]
        assert len(chapter_files) >= 1, (
            f"BUG B40: EPUB senza capitoli (file trovati: {names})"
        )

def test_build_epub_zip_includes_required_opf(tmp_path: Path) -> None:
    """Sanity check struttura EPUB3 minima: mimype + container.xml + content.opf + nav.xhtml."""
    md = "# X\n\nT."
    out = tmp_path / "book.epub"
    build_epub(markdown=md, images=[], metadata=BookMetadata(title="X"), output_path=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        # mimetype deve essere il PRIMO file (EPUB3 requirement, no compression)
        assert names[0] == "mimetype"
        # mimetype content
        assert zf.read("mimetype") == b"application/epub+zip"

# ----------------------------------------------------------------------
# B47: quantization_choices caching
# ----------------------------------------------------------------------

def test_quantization_choices_is_cached() -> None:
    """B47: la funzione deve cachare il risultato dopo la prima chiamata
    (evita di re-importare torch/bitsandbytes ad ogni render UI)."""
    from relictoepub.ui.components import quantization_choices
    # Prima chiamata: side-effect popola la cache globale
    c1, d1 = quantization_choices()
    # La seconda ritorna la STESSA identica lista (object identity),
    # non un nuovo risultato ricalcolato.
    c2, d2 = quantization_choices()
    assert c1 is c2, "BUG B47: quantization_choices() non cacha (ricalcolata)"
    assert d1 == d2
    # Deve essere una sequenza non-vuota con il default tra le choices
    assert isinstance(c1, (list, tuple))
    assert len(c1) >= 1
    assert d2 in [c[-1] if isinstance(c, tuple) else c for c in c1] if isinstance(c1[0], tuple) else (d2 in c1)

# ----------------------------------------------------------------------
# B31: ingest.py chiude correttamente gli handle PIL Image
# ----------------------------------------------------------------------

def test_ingest_render_pdf_closes_pil_handles(tmp_path: Path) -> None:
    """B31: il context manager `with Image.open()` deve essere usato
    per chiudere l'handle di hires_path dopo la normalizzazione.

    Verifica statica: la funzione render_pdf deve contenere il pattern
    `with Image.open(hires_path)` (il vecchio codice era `pil_hires = Image.open(...)`).
    """
    import inspect

    from relictoepub.ingest import render_pdf
    source = inspect.getsource(render_pdf)
    assert "with Image.open(hires_path)" in source, (
        "BUG B31: render_pdf() non usa context manager per Image.open(). "
        "Possibile memory leak su PDF di molte pagine."
    )

# ----------------------------------------------------------------------
# B32/B50/B51: pattern regex hoisted (compilati una sola volta)
# ----------------------------------------------------------------------

def test_pipeline_uses_hoisted_regex_patterns() -> None:
    """B32/B50/B51: i pattern regex non devono essere ricompilati
    ad ogni pagina — devono essere costanti a livello modulo.

    Verifica statica: le costanti _DET_PATTERN, _LAYOUT_TAG_RE,
    _EMPTY_LAYOUT_RE devono esistere.
    """
    import relictoepub.pipeline as p
    assert hasattr(p, "_DET_PATTERN"), "BUG B32: _DET_PATTERN non hoistato"
    assert hasattr(p, "_LAYOUT_TAG_RE"), "BUG B50: _LAYOUT_TAG_RE non hoistato"
    assert hasattr(p, "_EMPTY_LAYOUT_RE"), "BUG B51: _EMPTY_LAYOUT_RE non hoistato"
    # Verifica che siano pattern compilati, non stringhe
    assert isinstance(p._DET_PATTERN, re.Pattern)
    assert isinstance(p._LAYOUT_TAG_RE, re.Pattern)
    assert isinstance(p._EMPTY_LAYOUT_RE, re.Pattern)

# ----------------------------------------------------------------------
# B36: _check_pandoc caching
# ----------------------------------------------------------------------

def test_check_pandoc_is_cached() -> None:
    """B36: _check_pandoc() deve cachare il path dopo la prima chiamata,
    evitando di rieseguire shutil.which() ad ogni capitolo."""
    from relictoepub.compile.build_epub import _check_pandoc, _pandoc_path_cache
    # Popola la cache
    p1 = _check_pandoc()
    # La cache deve essere stata popolata dopo la prima chiamata
    assert _pandoc_path_cache is not None
    assert _pandoc_path_cache == p1
    # Seconda chiamata: deve ritornare immediatamente lo stesso path (cached)
    p2 = _check_pandoc()
    assert p1 == p2
    assert p2 == _pandoc_path_cache

# ----------------------------------------------------------------------
# B48: advanced_options() istanzia il Dropdown quantizzazione con cache None
# ----------------------------------------------------------------------

def test_advanced_options_quantization_dropdown_is_populated() -> None:
    """B48: la cache globale ``_q_choices`` / ``_q_default`` deve essere
    inizializzata PRIMA che ``advanced_options()`` costruisca il Dropdown.

    Riproduce un bug in cui ``advanced_options()`` legge le variabili globali
    ``_q_choices``/``_q_default`` mentre sono ancora ``None`` (l'inizializzazione
    lazy in ``quantization_choices()`` non viene mai triggerata dall'app UI),
    producendo un Dropdown vuoto (``choices=[]``, ``value=None``) che impedisce
    all'utente di selezionare la quantizzazione.
    """
    import relictoepub.ui.components as components

    # Simula fresh import: azzera la cache globale del modulo.
    components._q_choices = None
    components._q_default = None

    # Sanity check: senza invocare ``quantization_choices()``, le globali
    # restano ``None``. Questo è esattamente lo scenario che si verifica
    # nella vita reale: ``advanced_options()`` viene chiamato da ``build_demo()``
    # senza che nessuno abbia chiamato ``quantization_choices()`` prima.
    assert components._q_choices is None
    assert components._q_default is None

    # Adesso costruisci le opzioni come farebbe ``build_demo()``.
    opts = components.advanced_options()
    qd = opts["quantization"]

    # ASSEGNazione attesa dopo il fix: il Dropdown deve avere almeno una
    # scelta e un valore di default valido (uno delle stringhe delle scelte).
    assert qd.choices, "BUG B48: Dropdown quantizzazione senza scelte (choices vuote)"
    assert qd.value is not None, "BUG B48: Dropdown quantizzazione senza valore di default"
    # Il default deve essere presente nelle scelte (controllo sul valore finale).
    flat_choices = [
        c[-1] if isinstance(c, tuple) else c for c in qd.choices
    ]
    assert qd.value in flat_choices, (
        f"BUG B48: default {qd.value!r} non presente nelle scelte {qd.choices!r}"
    )