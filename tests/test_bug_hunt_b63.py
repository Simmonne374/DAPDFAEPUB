"""Riproduzione isolata del bug B63 — il resume del checkpoint perde le
immagini ritagliate (crop), producendo EPUB con ``<img>`` pendenti.

BUG B63: la funzionalità di **checkpoint & resume** riusa il *markdown
finale* di ogni batch già elaborato. Quel markdown contiene già i tag::

    <img src="images/page0001_image0.png" style="width:..." />

cioè i riferimenti ai crop generati durante il primo run. Al riprendere,
però, il ramo "batch cached" in :meth:`Pipeline.run_iter` esegue un
``continue`` **prima** del loop che estrae i bbox e materializza i crop::

    if cached_markdown is not None:
        cached_parts = cached_markdown.split("\\n\\n<!-- pagebreak -->\\n\\n")
        ...
        all_markdown_parts.extend(cached_parts)
        all_pages_processed += batch_end - batch_start
        yield ProgressEvent(..., extra={"cached": True})
        continue                      # <-- salta TUTTA l'estrazione crop

I crop quindi non vengono mai rigenerati. Inoltre, con il default
``work_dir=None`` la work dir è una ``tempfile.mkdtemp()`` **nuova a ogni
esecuzione**, quindi i crop del run precedente non sono più su disco.

La lista ``saved_crops`` — che alimenta ``optimize_batch`` e poi
``build_epub(images=...)`` — resta vuota per i batch cached. Risultato:
l'EPUB ricostruito contiene riferimenti ``images/...`` verso file **mai
copiati** in ``OEBPS/images/``, cioè immagini rotte nel libro finale.

ROOT CAUSE
----------

Il checkpoint persiste solo il **testo derivato** (markdown con
riferimenti alle immagini) ma non gli **artefatti derivati** che quel
testo referenzia (i file crop). Il resume riutilizza il primo senza
riutilizzare il secondo.

FIX
---

1. Quando il checkpoint è attivo, i crop vengono scritti in
   ``checkpoint_store.directory / "crops"`` (accanto allo stato, quindi
   persistenti fra i run) invece che nella work dir effimera. Cancellare
   la cartella di checkpoint ripulisce anche i crop.
2. Il ramo "batch cached" richiama
   ``_rehydrate_crops_from_markdown()``, che rimette in ``saved_crops``
   i file crop già presenti su disco e referenziati dal markdown cached
   (gestendo il mismatch ``.png`` su disco vs ``.webp`` referenziato
   quando l'ottimizzazione e-ink è attiva).

Questo test:
1. **Riproduce** il bug end-to-end: esegue la pipeline due volte sullo
   stesso PDF con lo stesso ``CheckpointStore`` e verifica che nel
   secondo (resume) EPUB ogni ``src="images/..."`` corrisponda a un file
   realmente presente in ``OEBPS/images/``.
2. **Anti-regressione**: i crop senza checkpoint restano nella work dir;
   il resume continua a saltare l'OCR (nessuna inferenza ri-eseguita);
   l'helper di re-idratazione gestisce HTML/Markdown, il fallback
   ``.webp`` → ``.png`` e non duplica.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from relictoepub.checkpoint import CheckpointStore, resolve_checkpoint_dir
from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig
from relictoepub.pipeline import Pipeline

# ``_rehydrate_crops_from_markdown`` è importato dentro ai singoli test:
# sulla base pre-fix l'helper non esiste, e un import a livello di modulo
# farebbe fallire la *collezione* invece di far fallire i test di
# riproduzione, che devono poter girare (e fallire) anche senza il fix.


# ===============================================================
# B63 — RIPRODUZIONE (failing test sul master)
# ===============================================================


class _FakeImageOCR:
    """Runner OCR finto: emette un ``<|det|>image[...]`` per ogni pagina.

    Conta le invocazioni in ``calls`` per verificare che il resume
    skippi davvero l'inferenza.
    """

    calls = 0

    def __init__(self, cfg) -> None:
        pass

    def run_batch_iter(self, paths, cancel_check=None):
        type(self).calls += 1
        parts = []
        for i in range(len(paths)):
            parts.append(
                f"<page># Capitolo {i}\n\n"
                f"<|det|>image[100,100,500,500]<|/det|>\n\n"
                f"Testo pagina {i}.\n"
            )
        text = "".join(parts)
        yield text, "running"
        yield text, "done"

    @staticmethod
    def _strip_image_tokens(text: str) -> str:
        return text


def _make_pdf(path: Path, n_pages: int) -> Path:
    """Crea un PDF reale (PyMuPDF) con ``n_pages`` pagine A4."""
    import pymupdf as fitz

    doc = fitz.open()
    try:
        for i in range(n_pages):
            pg = doc.new_page(width=595, height=842)
            pg.insert_text(
                (72, 72 + i * 20), f"Pagina di test {i + 1}", fontsize=14,
            )
        doc.save(str(path))
    finally:
        doc.close()
    return path


def _epub_img_refs(epub_path: Path) -> list[str]:
    """Ritorna i riferimenti ``src="images/..."`` presenti nei XHTML."""
    refs: list[str] = []
    with zipfile.ZipFile(epub_path) as zf:
        for name in zf.namelist():
            if name.startswith("OEBPS/") and name.endswith(".xhtml"):
                body = zf.read(name).decode("utf-8")
                marker = 'src="images/'
                start = 0
                while (idx := body.find(marker, start)) != -1:
                    start = idx + len(marker)
                    end = body.find('"', start)
                    refs.append(body[start:end])
    return refs


def _epub_images(epub_path: Path) -> set[str]:
    """Ritorna l'insieme dei nomi dei file presenti in ``OEBPS/images/``.

    I nomi sono restituiti senza prefisso, cosi' sono direttamente
    confrontabili con quanto estratto da :func:`_epub_img_refs`.
    """
    with zipfile.ZipFile(epub_path) as zf:
        return {
            n[len("OEBPS/images/"):]
            for n in zf.namelist()
            if n.startswith("OEBPS/images/") and not n.endswith("/")
        }


def _run_pipeline(
    pdf: Path, out_epub: Path, store: CheckpointStore, *, batch_size: int = 2
) -> None:
    """Esegue la pipeline fino in fondo (consuma il generatore di ``run_iter``)."""
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=batch_size),
        max_pages_per_batch=batch_size,
        eink_optimize=False,
        metadata=BookMetadata(title="Test B63"),
        checkpoint_store=store,
    )
    list(pipeline.run_iter(pdf, out_epub))


@pytest.fixture(autouse=True)
def _reset_ocr_calls() -> None:
    """Azzera il contatore di invocazioni OCR prima di ogni test."""
    _FakeImageOCR.calls = 0


def test_b63_resume_does_not_lose_cropped_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B63 — RIPRODUZIONE.

    Dopo un resume dal checkpoint, **ogni** ``<img src="images/...">``
    presente nei capitoli XHTML deve corrispondere a un file realmente
    presente in ``OEBPS/images/``.

    Sul master il secondo run produce 3 riferimenti pendenti: il ramo
    "batch cached" salta l'estrazione dei crop e la work dir (nuova
    ``mkdtemp`` a ogni run) non contiene più nulla del primo run.
    """
    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", _FakeImageOCR,
    )

    pdf = _make_pdf(tmp_path / "book.pdf", 3)
    store = CheckpointStore(resolve_checkpoint_dir(pdf))

    # 1) Primo run: tutto da zero, i crop vengono generati.
    _run_pipeline(pdf, tmp_path / "first.epub", store)
    first_refs = _epub_img_refs(tmp_path / "first.epub")
    assert first_refs, (
        "BUG B63 (setup): il primo run non ha prodotto alcun <img>: "
        "il test non sarebbe in grado di riprodurre il bug."
    )
    assert set(first_refs) <= _epub_images(tmp_path / "first.epub"), (
        "BUG B63 (setup): il primo run ha già riferimenti pendenti."
    )

    # 2) Secondo run: resume puro dal checkpoint.
    _run_pipeline(pdf, tmp_path / "resume.epub", store)

    resume_refs = _epub_img_refs(tmp_path / "resume.epub")
    resume_images = _epub_images(tmp_path / "resume.epub")

    assert resume_refs, (
        "BUG B63: il resume non ha prodotto alcun <img>, il checkpoint "
        "sembrerebbe perdere il markdown. Verifica del fix."
    )

    missing = sorted(set(resume_refs) - resume_images)
    assert not missing, (
        f"BUG B63: il resume ha prodotto {len(missing)} <img> pendenti, "
        f"cioè riferimenti a immagini mai copiate in OEBPS/images/. "
        f"Mancanti: {missing}. "
        f"Presenti: {sorted(resume_images)}. "
        f"Il ramo 'batch cached' deve riagganciare i crop già su disco."
    )


def test_b63_resume_preserves_image_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B63 — RIPRODUZIONE variante: il resume non deve perdere immagini.

    L'insieme delle immagini presenti nell'EPUB dopo il resume deve
    coincidere con quello del primo run.
    """
    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", _FakeImageOCR,
    )

    pdf = _make_pdf(tmp_path / "book.pdf", 3)
    store = CheckpointStore(resolve_checkpoint_dir(pdf))

    _run_pipeline(pdf, tmp_path / "first.epub", store)
    _run_pipeline(pdf, tmp_path / "resume.epub", store)

    first_images = _epub_images(tmp_path / "first.epub")
    resume_images = _epub_images(tmp_path / "resume.epub")

    lost = sorted(first_images - resume_images)
    assert not lost, (
        f"BUG B63: il resume ha perso {len(lost)} immagini presenti nel "
        f"primo run: {lost}. "
        f"Primo run: {sorted(first_images)}. "
        f"Resume: {sorted(resume_images)}."
    )


# ===============================================================
# B63 — ANTI-REGRESSIONE
# ===============================================================


def test_b63_resume_still_skips_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B63 — anti-regressione: il resume deve continuare a saltare l'OCR.

    Il fix riaggancia i crop già estratti: NON deve re-eseguire
    l'inferenza (che è la fase lenta che il resume esiste per evitare).
    """
    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", _FakeImageOCR,
    )

    pdf = _make_pdf(tmp_path / "book.pdf", 3)
    store = CheckpointStore(resolve_checkpoint_dir(pdf))

    _run_pipeline(pdf, tmp_path / "first.epub", store)
    calls_after_first = _FakeImageOCR.calls
    assert calls_after_first > 0, "BUG B63 (setup): primo run senza OCR."

    _run_pipeline(pdf, tmp_path / "resume.epub", store)
    assert _FakeImageOCR.calls == calls_after_first, (
        f"BUG B63 (regressione): il resume ha ri-eseguito l'OCR "
        f"({calls_after_first} → {_FakeImageOCR.calls} invocazioni). "
        f"Il resume deve riusare il checkpoint senza inferenza."
    )


def test_b63_without_checkpoint_crops_stay_in_work_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B63 — anti-regressione: senza checkpoint i crop vanno nella work dir.

    Il fix cambia la destinazione dei crop **solo** quando un checkpoint
    store è configurato. Senza checkpoint il comportamento preesistente
    (crop nella work dir) deve essere preservato.
    """
    monkeypatch.setattr(
        "relictoepub.pipeline.UnlimitedOCRRunner", _FakeImageOCR,
    )

    pdf = _make_pdf(tmp_path / "book.pdf", 2)
    work_dir = tmp_path / "work"

    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=2),
        max_pages_per_batch=2,
        eink_optimize=False,
        metadata=BookMetadata(title="Test B63"),
        work_dir=work_dir,
    )
    list(pipeline.run_iter(pdf, tmp_path / "out.epub"))

    crops = work_dir / "crops"
    pngs = sorted(crops.glob("*.png")) if crops.is_dir() else []
    assert pngs, (
        f"BUG B63 (regressione): senza checkpoint i crop devono restare "
        f"nella work dir {work_dir / 'crops'}, ma la cartella è vuota o "
        f"inesistente."
    )


def test_b63_rehydrate_picks_up_html_and_markdown_refs(tmp_path: Path) -> None:
    """B63 — anti-regressione del componente di fix.

    ``_rehydrate_crops_from_markdown`` deve riconoscere sia la forma HTML
    (``<img src="images/x.png">``) sia quella Markdown (``![](images/y.png)``).
    """
    from relictoepub.pipeline import _rehydrate_crops_from_markdown

    crops = tmp_path / "crops"
    crops.mkdir()
    (crops / "a.png").write_bytes(b"x")
    (crops / "b.png").write_bytes(b"x")

    md = (
        '<img src="images/a.png" style="width:50%;" />\n\n'
        "![b](images/b.png)\n"
    )
    saved: list[Path] = []
    _rehydrate_crops_from_markdown(md, crops, saved)

    assert saved == [crops / "a.png", crops / "b.png"], (
        f"BUG B63 (regressione): l'helper di re-idratazione non ha "
        f"riconosciuto entrambe le forme di riferimento. Trovato: {saved}."
    )


def test_b63_rehydrate_maps_webp_ref_to_png_on_disk(tmp_path: Path) -> None:
    """B63 — anti-regressione: il riferimento ``.webp`` (e-ink) deve
    risolvere sul crop ``.png`` effettivamente scritto su disco.

    ``out_path`` è hardcoded a ``.png`` mentre il nome referenziato nel
    markdown è ``.webp`` quando ``eink_optimize`` è attivo.
    """
    from relictoepub.pipeline import _rehydrate_crops_from_markdown

    crops = tmp_path / "crops"
    crops.mkdir()
    (crops / "page0001_image0.png").write_bytes(b"x")

    saved: list[Path] = []
    _rehydrate_crops_from_markdown(
        '<img src="images/page0001_image0.webp" />', crops, saved,
    )

    assert saved == [crops / "page0001_image0.png"], (
        f"BUG B63 (regressione): il fallback .webp → .png non funziona. "
        f"Trovato: {saved}."
    )


def test_b63_rehydrate_ignores_missing_and_dedupes(tmp_path: Path) -> None:
    """B63 — anti-regressione: i riferimenti senza file su disco devono
    essere ignorati e ogni file aggiunto una sola volta.
    """
    from relictoepub.pipeline import _rehydrate_crops_from_markdown

    crops = tmp_path / "crops"
    crops.mkdir()
    (crops / "a.png").write_bytes(b"x")

    md = '<img src="images/a.png" />\n<img src="images/ghost.png" />\n'
    saved: list[Path] = []
    _rehydrate_crops_from_markdown(md, crops, saved)

    assert saved == [crops / "a.png"], (
        f"BUG B63 (regressione): l'helper deve ignorare i file assenti e "
        f"non duplicare quelli presenti. Trovato: {saved}."
    )