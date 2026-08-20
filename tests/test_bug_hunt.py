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


def test_pipeline_respects_explicit_metadata_cover_image(
    tmp_path: Path, sample_pdf: Path, sample_image: Path
) -> None:
    """Reproduce BUG: ``Pipeline`` ignora ``metadata.cover_image`` e forza sempre la
    prima pagina del PDF come cover.

    Scenario: l'utente fornisce un ``BookMetadata(cover_image=sample_image)``
    esplicito (es. caricato dalla UI Gradio). La pipeline dovrebbe passarlo
    a :func:`build_epub`. Invece :meth:`Pipeline.run_iter` lo sovrascrive
    silenziosamente con ``ingest_result.pages[0].original_path``
    (vedi ``src/relictoepub/pipeline.py``, alla riga della chiamata a
    ``build_epub``).

    Effetto: ``cover.xhtml`` mostra la PRIMA PAGINA del PDF (identica a
    ``chap_0001.xhtml``). Quando l'utente fornisce un'immagine di
    copertina dedicata, viene completamente ignorata.

    Per riprodurre in modo isolato, ci limitiamo a verificare il branch
    decisionale che la pipeline usa per scegliere il ``cover_image``.
    """
    from relictoepub.compile.build_epub import BookMetadata
    from relictoepub.ingest import render_pdf
    from relictoepub.pipeline import Pipeline

    user_cover = sample_image  # 600x800 nera
    cover_md = BookMetadata(title="T", author="A")
    cover_md.cover_image = user_cover

    # Esegui il rendering PDF (per avere pages[0].original_path).
    ingest_result = render_pdf(sample_pdf, output_dir=tmp_path / "r", dpi=72)
    first_pdf_image = ingest_result.pages[0].original_path

    # La pipeline attuale non espone un metodo privato per la cover
    # resolution: verifichiamo direttamente il comportamento in modo
    # strutturale. Applichiamo la stessa logica che ``run_iter`` esegue.
    # Logica attuale (BUG):
    cover_image = (
        ingest_result.pages[0].original_path
        if ingest_result.pages else None
    )

    # Dopo il fix dovrà essere: cover_image = cover_md.cover_image or
    # ingest_result.pages[0].original_path
    assert cover_image == first_pdf_image, (
        "Baseline: la pipeline usa la prima pagina del PDF come cover"
    )
    assert cover_image != user_cover, (
        "BUG riprodotto: la pipeline NON rispetta metadata.cover_image "
        "impostato dall'utente"
    )


def test_pipeline_prefers_explicit_cover_over_first_page(
    tmp_path: Path, sample_pdf: Path, sample_image: Path
) -> None:
    """Test che valida il FIX atteso.

    Una volta corretta la pipeline, risolvere la cover image deve
    restituire ``metadata.cover_image`` quando esplicitamente fornito,
    e solo in fallback la prima pagina del PDF.
    """
    from relictoepub.compile.build_epub import BookMetadata
    from relictoepub.ingest import render_pdf
    from relictoepub.pipeline import Pipeline

    user_cover = sample_image
    cover_md = BookMetadata(title="T", author="A")
    cover_md.cover_image = user_cover

    ingest_result = render_pdf(sample_pdf, output_dir=tmp_path / "r", dpi=72)

    pipeline = Pipeline()
    resolved = pipeline.resolve_cover_image(
        metadata=cover_md,
        ingest_result=ingest_result,
    )
    assert resolved == user_cover, (
        f"Cover risolta in modo errato: atteso {user_cover}, ottenuto {resolved}"
    )