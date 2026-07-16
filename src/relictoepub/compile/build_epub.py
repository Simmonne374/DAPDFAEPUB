"""Modulo 5 — Compilatore EPUB3.

Pipeline:
1. Markdown sorgente → HTML semantico via ``pypandoc``.
2. Split in capitoli logici sui tag ``<h1>``/``<h2>``.
3. Iniezione delle immagini ritagliate (percorso relativo ``images/``).
4. Generazione del pacchetto EPUB3 con ``ebooklib``
   (metadati, TOC, navigation document, zip finale).
5. Aggiunta del foglio di stile ottimizzato E-ink.

Perché pypandoc + ebooklib invece di una soluzione all-in-one?
* ``pypandoc`` è la soluzione più matura per Markdown → XHTML strict.
* ``ebooklib`` gestisce il packaging EPUB3 standard IDPF; pypandoc
  da solo produce solo HTML.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from relictoepub.compile.eink_css import EINK_CSS

logger = logging.getLogger(__name__)


@dataclass
class ChapterInfo:
    """Metadata di un capitolo del libro.

    Attributes:
        title: Titolo del capitolo (testo del tag ``h1``/``h2``).
        level: 1 o 2 — usato per la TOC nestata.
        filename: Nome del file XHTML interno (``chap_0001.xhtml``).
        xhtml: Contenuto XHTML del capitolo.
    """

    title: str
    level: int
    filename: str
    xhtml: str


@dataclass
class BookMetadata:
    """Metadata del libro da inserire nell'EPUB.

    Attributes:
        title: Titolo del libro (obbligatorio).
        author: Autore.
        language: Codice lingua ISO 639-1 (``"it"``, ``"en"``, …).
        identifier: UUID o ISBN. Default: UUID generato automaticamente.
        cover_image: Path opzionale a un'immagine di copertina.
        chapter_pages: Se impostato e il libro non ha struttura a
            heading (H1/H2), raggruppa le pagine in capitoli di N
            pagine ciascuno. ``None`` disabilita il raggruppamento
            (default: comportamento adattivo puro).
    """

    title: str
    author: str = "Unknown"
    language: str = "it"
    identifier: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    cover_image: Path | None = None
    chapter_pages: int | None = None


_HEADING_PATTERN = re.compile(
    r"^<!-- pagebreak -->$", re.MULTILINE
)
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_on_pattern(
    markdown: str, pattern: re.Pattern[str]
) -> list[dict] | None:
    """Split Markdown su un regex di heading; ritorna ``None`` se <3 match."""
    matches = list(pattern.finditer(markdown))
    if len(matches) < 3:
        return None

    chapters: list[dict] = []
    if matches[0].start() > 0:
        chapters.append(
            {
                "level": 1,
                "title": "",
                "body": markdown[: matches[0].start()].rstrip(),
            }
        )
    for i, match in enumerate(matches):
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[match.end() : next_start].rstrip()
        chapters.append(
            {
                "level": 1,
                "title": match.group(1).strip(),
                "body": body,
            }
        )
    return chapters


def _split_page_grouping(
    markdown: str, group_size: int
) -> list[dict]:
    """Raggruppa le pagine in capitoli di N pagine ciascuno.

    Usa i marker ``<!-- pagebreak -->`` come separatore di pagina.
    """
    pages = [p.strip() for p in _HEADING_PATTERN.split(markdown) if p.strip()]
    if not pages:
        return [{"level": 1, "title": "", "body": markdown.strip()}]
    chapters: list[dict] = []
    for start in range(0, len(pages), group_size):
        chunk = pages[start : start + group_size]
        chapters.append(
            {
                "level": 1,
                "title": f"Pagine {start + 1}-{start + len(chunk)}",
                "body": "\n\n".join(chunk),
            }
        )
    return chapters


def _split_legacy_pagebreaks(markdown: str) -> list[dict]:
    """Fallback legacy: una pagina = un capitolo (basato sui pagebreak).

    Le pagine con body vuoto (es. pagebreak finale senza contenuto che
    segue) vengono scartate.
    """
    matches = list(_HEADING_PATTERN.finditer(markdown))
    if not matches:
        return [{"level": 1, "title": "", "body": markdown.strip()}]

    chapters: list[dict] = []
    if matches[0].start() > 0:
        chapters.append(
            {"level": 1, "title": "", "body": markdown[: matches[0].start()].strip()}
        )
    for i, match in enumerate(matches):
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[match.end() : next_start].strip()
        if body:
            chapters.append({"level": 1, "title": "", "body": body})
    return chapters


def _split_into_chapters(
    markdown: str,
    *,
    chapter_pages: int | None = None,
) -> list[dict]:
    """Divide il Markdown in capitoli logici con strategia adattiva.

    Strategia in ordine di priorità:
    1. **Heading H1** (``^# …``): se ci sono ≥ 3 match, ogni H1 diventa
       un capitolo. È la struttura più pulita per un libro con indice.
    2. **Heading H2** (``^## …``): fallback per libri piatti senza H1
       ma con sotto-sezioni semantiche (≥ 3 match).
    3. **Page grouping**: se ``chapter_pages`` è un intero positivo,
       raggruppa le pagine in chunk da N (usando i marker
       ``<!-- pagebreak -->`` come separatore).
    4. **Page-per-chapter**: nessuna struttura individuabile, ogni
       ``<!-- pagebreak -->`` diventa un capitolo (comportamento
       legacy).

    Restituisce una lista di dict ``{"level", "title", "body"}``.
    """
    if not markdown.strip():
        return []

    # 1) H1
    h1_chapters = _split_on_pattern(markdown, _H1_PATTERN)
    if h1_chapters:
        return h1_chapters

    # 2) H2
    h2_chapters = _split_on_pattern(markdown, _H2_PATTERN)
    if h2_chapters:
        return h2_chapters

    # 3) Page grouping
    if chapter_pages and chapter_pages > 0:
        return _split_page_grouping(markdown, chapter_pages)

    # 4) Legacy: una pagina = un capitolo
    return _split_legacy_pagebreaks(markdown)


def _check_pandoc() -> str:
    """Verifica che pandoc sia installato e ritorna il path."""
    try:
        import pypandoc
        return pypandoc.get_pandoc_path()
    except Exception:
        pass
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise RuntimeError(
            "Pandoc non è installato. Scaricalo da "
            "https://github.com/jgm/pandoc/releases e installalo, "
            "poi riavvia il terminale."
        )
    return pandoc


def _convert_markdown_to_xhtml(markdown: str) -> str:
    """Markdown → XHTML strict via pypandoc."""
    try:
        import pypandoc
    except ImportError as exc:  # pragma: no cover - dipendenza obbligatoria
        raise RuntimeError(
            "pypandoc non installato. Aggiungi 'pypandoc' alle dipendenze."
        ) from exc
    _check_pandoc()  # errore amichevole se manca
    return pypandoc.convert_text(
        markdown, to="html5", format="markdown+smart",
        extra_args=["--standalone", "--no-highlight", "--wrap=none"],
    )


def _chapter_xhtml(title: str, body_markdown: str, level: int) -> str:
    """Crea l'XHTML di un capitolo, wrappato in un body semanticamente corretto."""
    full_md = f"# {title}\n\n{body_markdown}" if title else body_markdown
    html = _convert_markdown_to_xhtml(full_md)
    # Sostituisci il primo heading h1 generato da pypandoc con un <h1 class="chapter-title">
    if title:
        html = re.sub(
            r"<h1[^>]*>.*?</h1>",
            f'<h1 class="chapter-title">{_xml_escape(title)}</h1>',
            html,
            count=1,
            flags=re.DOTALL,
        )
    return html


def _xml_escape(text: str) -> str:
    """Escape minimo per inserire testo in XHTML — gestisce solo i casi necessari."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inject_responsive_images(html: str) -> str:
    """Aggiunge ``max-width:100%; height:auto`` a tutti i ``<img>`` emessi in modo XML-compliant.

    Idempotente — se lo stile è già presente non lo duplica.
    """
    pattern = re.compile(r"<img([^>]*?)(/?)>")
    def repl(m):
        attrs = m.group(1).rstrip()
        is_self_closing = m.group(2) or "/"
        
        if "max-width:100%" in attrs:
            return m.group(0)
            
        if attrs.endswith("/"):
            attrs = attrs[:-1].rstrip()
            is_self_closing = "/"
            
        return f'<img {attrs} style="max-width:100%; height:auto; display:block; margin:1em auto;" {is_self_closing}>'
        
    return pattern.sub(repl, html)


def _add_cover_page(chapters: list[ChapterInfo], cover_image: Path | None) -> list[ChapterInfo]:
    """Se presente una cover, la prepende come primo 'capitolo'."""
    if cover_image is None or not cover_image.exists():
        return chapters
    cover_html = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head><title>Cover</title>'
        '<link rel="stylesheet" href="style.css"/></head>\n'
        '<body><div style="text-align:center; margin:0; padding:0;">'
        '<img src="images/cover.webp" alt="Cover" '
        'style="max-width:100%; height:auto;"/></div></body></html>'
    )
    cover_chapter = ChapterInfo(
        title="Cover", level=1, filename="cover.xhtml", xhtml=cover_html,
    )
    return [cover_chapter] + chapters


def _build_navigation_xhtml(title: str, chapters: list[ChapterInfo]) -> str:
    """Crea il file ``nav.xhtml`` (EPUB3 navigation document) con l'indice dinamico dei capitoli."""
    items = []
    for ch in chapters:
        # Salta la copertina dall'indice dei capitoli principale
        if ch.filename == "cover.xhtml":
            continue
        items.append(
            f'      <li><a href="{ch.filename}">{_xml_escape(ch.title)}</a></li>'
        )
    list_content = "\n".join(items)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{_xml_escape(title)} — Indice</title></head>
<body>
<nav epub:type="toc" id="toc">
  <h1>Indice</h1>
  <ol id="toc-list">
{list_content}
  </ol>
</nav>
</body>
</html>"""


def build_epub(
    markdown: str,
    images: Sequence[str | Path] = (),
    metadata: BookMetadata | None = None,
    output_path: str | Path = "output.epub",
    *,
    cover_image: Path | None = None,
) -> Path:
    """Compila un EPUB3 a partire da Markdown, immagini e metadati.

    Args:
        markdown: Testo Markdown completo del libro (anche più capitoli).
        images: Lista di crop immagini da includere (``relictoepub.postprocess``).
        metadata: Titolo, autore, lingua, identifier. Se ``None``, verranno
            usati dei default sensati.
        output_path: Dove salvare l'.epub finale.
        cover_image: Path opzionale a una cover image (PNG/WebP/JPEG).

    Returns:
        Il :class:`Path` al file ``.epub`` creato.

    Raises:
        RuntimeError: se pandoc non è installato o le dipendenze mancano.
    """
    metadata = metadata or BookMetadata(title="Untitled")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) Split in capitoli (strategia adattiva, vedi _split_into_chapters)
    chapters_raw = _split_into_chapters(
        markdown, chapter_pages=metadata.chapter_pages
    )
    chapters: list[ChapterInfo] = []
    for i, raw in enumerate(chapters_raw):
        body = raw.get("body", "")
        # Il titolo è già stato deciso dallo splitter adattivo (H1, H2,
        # page-grouping, o fallback "Pagina N"). Lo accettiamo così com'è.
        title = raw.get("title", "") or ""

        xhtml = _chapter_xhtml(
            title="",  # non generiamo un titolo H1 duplicato in testa al file
            body_markdown=body,
            level=raw.get("level", 1),
        )
        xhtml = _inject_responsive_images(xhtml)
        chapters.append(
            ChapterInfo(
                title=title or f"Pagina {i+1}",
                level=raw.get("level", 1),
                filename=f"chap_{i+1:04d}.xhtml",
                xhtml=xhtml,
            )
        )

    chapters = _add_cover_page(chapters, cover_image)

    # 2) Crea la cartella EPUB temporanea
    with tempfile.TemporaryDirectory(prefix="relictoepub_epub_") as tmp:
        tmp_path = Path(tmp)
        meta_inf = tmp_path / "META-INF"
        oebps = tmp_path / "OEBPS"
        meta_inf.mkdir()
        oebps.mkdir()
        images_dir = oebps / "images"
        images_dir.mkdir()

        # Mimetype (deve essere il primo file, non compresso)
        (tmp_path / "mimetype").write_text("application/epub+zip", encoding="ascii")

        # Container.xml
        container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        (meta_inf / "container.xml").write_text(container_xml, encoding="utf-8")

        # CSS
        css_path = oebps / "style.css"
        css_path.write_text(EINK_CSS, encoding="utf-8")

        # Capitoli
        for chapter in chapters:
            (oebps / chapter.filename).write_text(chapter.xhtml, encoding="utf-8")

        # Cover image (se presente) — converti in WebP per E-ink
        if cover_image is not None and cover_image.exists():
            cover_dest = images_dir / "cover.webp"
            try:
                from relictoepub.postprocess.webp_optim import optimize_for_eink
                optimize_for_eink(cover_image, cover_dest)
            except Exception:
                shutil.copy(cover_image, cover_dest)

        # Asset images (WebP ready)
        for img_path in images:
            img_path = Path(img_path)
            if not img_path.exists():
                logger.warning("Immagine mancante, skip: %s", img_path)
                continue
            ext = img_path.suffix.lower() or ".webp"
            target = images_dir / (img_path.stem + ext)
            shutil.copy(img_path, target)

        # Navigation document
        (oebps / "nav.xhtml").write_text(_build_navigation_xhtml(metadata.title, chapters), encoding="utf-8")

        # content.opf — descrittore del package
        manifest_items: list[str] = []
        for ch in chapters:
            manifest_items.append(
                f'<item id="{Path(ch.filename).stem}" href="{ch.filename}" '
                f'media-type="application/xhtml+xml"/>'
            )
        for img_file in images_dir.iterdir():
            mt = "image/webp" if img_file.suffix == ".webp" else f"image/{img_file.suffix.lstrip('.')}"
            manifest_items.append(
                f'<item id="{img_file.stem}" href="images/{img_file.name}" media-type="{mt}"/>'
            )

        spine_items = "\n    ".join(
            f'<itemref idref="{Path(ch.filename).stem}"/>' for ch in chapters
        )

        opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{_xml_escape(metadata.identifier)}</dc:identifier>
    <dc:title>{_xml_escape(metadata.title)}</dc:title>
    <dc:creator>{_xml_escape(metadata.author)}</dc:creator>
    <dc:language>{_xml_escape(metadata.language)}</dc:language>
    <meta property="dcterms:modified">2026-07-06T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    {chr(10).join(manifest_items)}
  </manifest>
  <spine>
    {spine_items}
  </spine>
</package>"""
        (oebps / "content.opf").write_text(opf, encoding="utf-8")

        # Comprimi tutto in .epub (zip)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root in (tmp_path,):
                for file in root.rglob("*"):
                    if file.is_file():
                        # EPUB richiede path relativi con separatore forward slash
                        rel = file.relative_to(tmp_path).as_posix()
                        if file.name == "mimetype":
                            zf.write(file, rel, compress_type=zipfile.ZIP_STORED)
                        else:
                            zf.write(file, rel, compress_type=zipfile.ZIP_DEFLATED)

    logger.info("EPUB generato: %s (%.1f KB)", output_path, output_path.stat().st_size / 1024)
    return output_path


__all__ = ["BookMetadata", "ChapterInfo", "build_epub"]
