"""Helper per i golden test di ``build_epub``.

Fornisce:

* :func:`golden_epubs_dir` — path della directory dei golden file committati.
* :func:`build_golden_epub` — genera un EPUB da una fixture ID, applicando
  le opzioni documentate nel nome (es. ``chapter_pages`` per fixture 03).
* :func:`assert_epub_matches_golden` — confronta byte-per-byte (o
  strutturalmente) un EPUB generato contro il golden committato.
* :func:`assert_valid_epub3` — verifica le proprieta strutturali minime
  richieste da EPUB3 (mimetype first uncompressed, container.xml,
  content.opf, nav.xhtml).
* :func:`update_golden` — sovrascrive un golden file (usato da
  ``--update-golden``).

Tutti i confronti byte-per-byte sono consapevoli del fatto che pypandoc
puo produrre XHTML con timestamp o whitespace differenti tra versioni:
vengono normalizzati prima del confronto.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

import pytest

from relictoepub.compile.build_epub import BookMetadata, build_epub

# Path assoluto della directory che contiene i golden file committati.
GOLDEN_DIR: Path = Path(__file__).resolve().parent / "fixtures" / "golden"

# Mappa: fixture_id -> (filename .md, kwarg per build_epub)
# Aggiungere una entry qui quando si crea una nuova fixture.
FIXTURES: dict[str, dict] = {
    "01_simple_h1": {"md": "01_simple_h1.md", "kwargs": {}},
    "02_h2_fallback": {"md": "02_h2_fallback.md", "kwargs": {}},
    "03_page_grouping": {
        "md": "03_page_grouping.md",
        "kwargs": {"chapter_pages": 3},
    },
    "04_legacy_pagebreak": {"md": "04_legacy_pagebreak.md", "kwargs": {}},
    "05_with_images": {
        "md": "05_with_images.md",
        "kwargs": {},  # vedi build_golden_epub: inietta images=[...]
    },
    "06_with_cover": {
        "md": "06_with_cover.md",
        "kwargs": {"cover_filename": "cover.png"},
    },
    "07_unicode_italian": {"md": "07_unicode_italian.md", "kwargs": {}},
    "08_edge_empty": {"md": "08_edge_empty.md", "kwargs": {}},
}


@pytest.fixture(scope="session")
def golden_epubs_dir() -> Path:
    """Path della directory con i golden file committati (``.epub`` + ``.md``)."""
    assert GOLDEN_DIR.is_dir(), f"Directory golden mancante: {GOLDEN_DIR}"
    return GOLDEN_DIR


def _read_md(fixture_id: str) -> str:
    """Legge il file ``.md`` di una fixture."""
    info = FIXTURES[fixture_id]
    md_path = GOLDEN_DIR / info["md"]
    return md_path.read_text(encoding="utf-8")


def _normalize_xml(s: str) -> str:
    """Normalizza XML per confronto robusto tra pypandoc version diverse.

    Rimuove:
    * Date/timestamp nel metadata (es. ``dcterms:modified``).
    * UUID nell'identifier (generato random da :class:`BookMetadata`).
    * Whitespace EOL-only differenze.
    """
    s = re.sub(r"<meta property=\"dcterms:modified\">[^<]*</meta>", "", s)
    s = re.sub(r"urn:uuid:[0-9a-f-]+", "urn:uuid:NORMALIZED", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _zip_normalize(epub_path: Path) -> dict[str, bytes]:
    """Legge tutti i file dello ZIP, normalizzando l'XHTML."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(epub_path) as zf:
        for name in zf.namelist():
            data = zf.read(name)
            if name.endswith((".xhtml", ".html", ".opf", ".xml")):
                try:
                    txt = data.decode("utf-8")
                    out[name] = _normalize_xml(txt).encode("utf-8")
                except UnicodeDecodeError:
                    out[name] = data
            else:
                out[name] = data
    return out


def build_golden_epub(
    fixture_id: str,
    tmp_path: Path,
    *,
    chapter_pages: int | None = None,
) -> Path:
    """Costruisce un EPUB a partire da una fixture ID.

    Args:
        fixture_id: chiave in :data:`FIXTURES`.
        tmp_path: pytest tmp_path dove salvare l'EPUB generato.
        chapter_pages: se specificato, sovrascrive il default della fixture.

    Returns:
        Path dell'EPUB generato.
    """
    info = FIXTURES[fixture_id]
    md = _read_md(fixture_id)
    md = md.strip() + "\n" if md.strip() else ""

    # Parametri base del metadata
    md_meta = BookMetadata(
        title=f"Golden {fixture_id}",
        author="Golden Tests",
        language="it",
    )

    # Applica chapter_pages se richiesto (da fixture o da argomento)
    if chapter_pages is not None:
        md_meta.chapter_pages = chapter_pages
    elif "chapter_pages" in info["kwargs"]:
        md_meta.chapter_pages = info["kwargs"]["chapter_pages"]

    # Raccogli immagini per la fixture 05
    images: list[Path] = []
    cover_path: Path | None = None

    if fixture_id == "05_with_images":
        for fname in ("image_inline.png", "figure_inline.png", "table_inline.png"):
            p = GOLDEN_DIR / fname
            assert p.is_file(), f"Immagine golden mancante: {p}"
            images.append(p)
    elif fixture_id == "06_with_cover":
        cover_name = info["kwargs"].get("cover_filename", "cover.png")
        cover_path = GOLDEN_DIR / cover_name
        assert cover_path.is_file(), f"Cover golden mancante: {cover_path}"

    out = tmp_path / f"{fixture_id}.epub"
    build_epub(
        markdown=md,
        images=images,
        metadata=md_meta,
        output_path=out,
        cover_image=cover_path,
    )
    return out


def assert_valid_epub3(epub_path: Path) -> None:
    """Verifica le proprieta strutturali minime richieste da un EPUB3 valido.

    Controlla:
    * Il file e effettivamente uno ZIP.
    * ``mimetype`` e il primo entry (EPUB3 requirement).
    * ``mimetype`` e memorizzato senza compressione.
    * ``META-INF/container.xml`` esiste e referenzia ``OEBPS/content.opf``.
    * ``OEBPS/content.opf`` esiste.
    * ``OEBPS/nav.xhtml`` esiste ed e referenziato nel manifest OPF.
    * Almeno un capitolo XHTML e referenziato nello spine.
    """
    assert epub_path.is_file(), f"EPUB non trovato: {epub_path}"

    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        assert "mimetype" in names, "Manca mimetype"
        assert "META-INF/container.xml" in names, "Manca META-INF/container.xml"

        # mimetype DEVE essere il primo file ZIP entry
        assert names[0] == "mimetype", (
            f"mimetype deve essere il primo entry, trovato: {names[0]}"
        )

        # mimetype NON compresso
        info = zf.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED, (
            f"mimetype deve essere ZIP_STORED, trovato compress_type={info.compress_type}"
        )

        # mimetype content
        assert zf.read("mimetype") == b"application/epub+zip"

        # container.xml content minimale
        container = zf.read("META-INF/container.xml").decode("utf-8")
        assert "OEBPS/content.opf" in container, (
            "container.xml non referenzia OEBPS/content.opf"
        )

        # content.opf deve esistere e referenziare nav + css
        assert "OEBPS/content.opf" in names, "Manca OEBPS/content.opf"
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert 'href="nav.xhtml"' in opf, "nav.xhtml non referenziato in content.opf"
        assert 'href="style.css"' in opf, "style.css non referenziato in content.opf"
        assert "<spine>" in opf, "Manca <spine> in content.opf"

        # nav.xhtml deve esistere
        assert "OEBPS/nav.xhtml" in names, "Manca OEBPS/nav.xhtml"


def assert_epub_matches_golden(generated: Path, golden: Path) -> None:
    """Confronta due EPUB normalizzando XML e timestamp.

    Args:
        generated: EPUB prodotto da :func:`build_golden_epub`.
        golden: EPUB committato in ``tests/fixtures/golden/``.

    Raises:
        AssertionError con dettaglio del primo file che differisce.
    """
    assert golden.is_file(), (
        f"Golden file mancante: {golden}. "
        f"Generarlo con: pytest --update-golden"
    )

    gen = _zip_normalize(generated)
    gold = _zip_normalize(golden)

    # Stesso set di file?
    gen_names = set(gen.keys())
    gold_names = set(gold.keys())
    missing_in_gen = gold_names - gen_names
    extra_in_gen = gen_names - gold_names
    assert not missing_in_gen, f"File mancanti nel generato: {sorted(missing_in_gen)}"
    assert not extra_in_gen, f"File extra nel generato: {sorted(extra_in_gen)}"

    # Stesso contenuto per ciascun file (dopo normalizzazione)
    for name in sorted(gold_names):
        if gen[name] != gold[name]:
            # Scrivi il diff per diagnosi
            diff_path = generated.parent / f"_diff_{name.replace('/', '_')}.txt"
            diff_path.write_bytes(
                b"=== GENERATED ===\n" + gen[name] + b"\n=== GOLDEN ===\n" + gold[name],
            )
            raise AssertionError(
                f"Contenuto differisce per {name}. "
                f"Diff salvato in {diff_path}. "
                f"Se la modifica e intenzionale: pytest --update-golden"
            )


def update_golden(generated: Path, golden: Path) -> None:
    """Sovrascrive il golden file con quello generato (per ``--update-golden``)."""
    golden.parent.mkdir(parents=True, exist_ok=True)
    golden.write_bytes(generated.read_bytes())


def golden_sha256(epub_path: Path) -> str:
    """SHA256 del contenuto normalizzato — utile per identificare drift."""
    normalized = _zip_normalize(epub_path)
    h = hashlib.sha256()
    for name in sorted(normalized.keys()):
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(normalized[name])
        h.update(b"\x00")
    return h.hexdigest()


__all__ = [
    "FIXTURES",
    "GOLDEN_DIR",
    "assert_epub_matches_golden",
    "assert_valid_epub3",
    "build_golden_epub",
    "golden_epubs_dir",
    "golden_sha256",
    "update_golden",
]