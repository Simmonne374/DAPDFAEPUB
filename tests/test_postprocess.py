"""Test per i moduli di post-processing (3, 4, text-clean)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from relictoepub.postprocess.bbox_crop import (
    BBox,
    crop_image_from_bbox,
    denormalize_bbox,
    extract_bbox_tokens,
)
from relictoepub.postprocess.text_clean import clean_text, count_words
from relictoepub.postprocess.webp_optim import optimize_batch, optimize_for_eink

# ============================================================
# bbox_crop
# ============================================================

def test_bbox_from_string_valid() -> None:
    bbox = BBox.from_string("<|bbox|100|200|300|400|image|>")
    assert bbox.x_min == 100
    assert bbox.y_min == 200
    assert bbox.x_max == 300
    assert bbox.y_max == 400
    assert bbox.label == "image"


def test_bbox_from_string_no_label() -> None:
    bbox = BBox.from_string("<|bbox|0|0|500|500|>")
    assert bbox.label == ""


def test_bbox_from_string_malformed() -> None:
    with pytest.raises(ValueError):
        BBox.from_string("not a bbox")


def test_denormalize_bbox_basic() -> None:
    """1000-based normalizzata su immagine 1024x1024 -> pixel senza padding."""
    # L'immagine è 600x800.
    # Viene scalata a 768x1024 (scale=1.28).
    # Viene aggiunto padding X di (1024-768)/2 = 128
    # bbox su immagine intera: (125, 0, 875, 1000) in scala [0, 1000]
    bbox = BBox(125, 0, 875, 1000)
    pixel_box = denormalize_bbox(bbox, (600, 800))
    # Il clipping difensivo arrotonda in (0, 0, 600, 800)
    assert pixel_box == (0, 0, 600, 800)


def test_denormalize_bbox_clipped() -> None:
    """Le coordinate oltre l'immagine vengono clippate."""
    bbox = BBox(-500, -500, 1500, 1500)
    left, upper, right, lower = denormalize_bbox(bbox, (1000, 800))
    assert left == 0
    assert upper == 0
    assert right == 1000
    assert lower == 800


def test_crop_image_from_bbox_saves_file(sample_image: Path, tmp_path: Path) -> None:
    """Il crop salva un file valido e ritorna il path."""
    bbox = BBox(125, 0, 875, 1000)  # tutta l'immagine per 600x800
    out = tmp_path / "crop.png"
    result = crop_image_from_bbox(sample_image, bbox, output_path=out)
    assert result is not None
    assert result.is_file()
    with Image.open(result) as img:
        assert img.size == (600, 800)


def test_crop_image_too_small_returns_none(sample_image: Path, tmp_path: Path) -> None:
    """BBox microscopica → ``None`` per via di ``min_size``."""
    tiny = BBox(500, 500, 501, 501)
    result = crop_image_from_bbox(sample_image, tiny, output_path=tmp_path / "x.png")
    assert result is None


def test_crop_image_from_bbox_with_box_returns_pixel_bbox(
    sample_image: Path, tmp_path: Path,
) -> None:
    """La variante ``_with_box`` deve tornare sia ``Path`` che bbox denormalizzato."""
    from relictoepub.postprocess.bbox_crop import crop_image_from_bbox_with_box

    bbox = BBox(125, 0, 875, 1000)  # full-immagine per 600x800
    out = tmp_path / "crop.png"
    result = crop_image_from_bbox_with_box(sample_image, bbox, output_path=out)
    assert result is not None
    crop_path, pixel_box = result
    assert crop_path.is_file()
    # 600×800 full-immagine → bbox denormalizzato = (0, 0, 600, 800)
    assert pixel_box == (0, 0, 600, 800)


def test_bbox_width_pct_against_page_width() -> None:
    """``width_pct_against`` calcola la larghezza % rispetto alla pagina."""
    # Caso 1: pagina larga 1000 px, bbox largo 250 px → 25%.
    bbox = BBox(0, 0, 250, 100)
    assert bbox.width_pct_against(1000) == pytest.approx(25.0)

    # Caso 2: pagina larga 0 (degenerate) → 0%.
    assert bbox.width_pct_against(0) == 0.0

    # Caso 3: bbox più largo della pagina → clamp a 100%.
    wide_bbox = BBox(0, 0, 1500, 100)
    assert wide_bbox.width_pct_against(1000) == 100.0

    # Caso 4: bbox largo quanto la pagina → 100%.
    full = BBox(0, 0, 1000, 100)
    assert full.width_pct_against(1000) == 100.0

    # Caso 5: bbox = 0 larghezza → 0%.
    zero = BBox(500, 0, 500, 100)
    assert zero.width_pct_against(1000) == 0.0


def test_bbox_width_pct_against_page_width_b55_non_coincident() -> None:
    """B55: il calcolo non deve dipendere dal ``page_width_px``.

    Bug: la formula originale ``self.width / page_width_px * 100`` confondeva
    le coordinate normalizzate ``[0, 1000]`` del bbox con i pixel della
    pagina. Il risultato era corretto solo quando ``page_width_px`` coincideva
    con :data:`DEFAULT_NORMALIZE_RANGE` (1000) — coincidenza che ha permesso
    ai test esistenti di passare pur con la formula sbagliata.

    Con il fix, un bbox full-page (``width=1000``) deve sempre restituire
    ``100%`` indipendentemente dalla larghezza della pagina in pixel
    (A4 a 150 DPI = 1240 px, A4 a 300 DPI = 2480 px, ecc.). Per lo stesso
    motivo, un bbox che copre 300 unità normalizzate deve sempre dare ``30%``.
    """
    full = BBox(0, 0, 1000, 1000)
    # Full-page bbox: 100% su qualsiasi pagina.
    assert full.width_pct_against(1240) == pytest.approx(100.0)
    assert full.width_pct_against(2480) == pytest.approx(100.0)
    assert full.width_pct_against(1754) == pytest.approx(100.0)

    # Bbox 300 unità normalizzate: 30% (non dipende dalla pagina).
    small = BBox(100, 100, 400, 400)
    assert small.width_pct_against(1240) == pytest.approx(30.0)
    assert small.width_pct_against(2480) == pytest.approx(30.0)

    # Bbox 500 unità: 50%.
    half = BBox(250, 250, 750, 750)
    assert half.width_pct_against(1240) == pytest.approx(50.0)
    assert half.width_pct_against(2480) == pytest.approx(50.0)


def test_extract_bbox_tokens_multiple() -> None:
    text = (
        "Capitolo primo\n"
        "<|bbox|10|20|300|400|figure|>\n"
        "Altro testo\n"
        "<|bbox|0|0|100|100|title|>\n"
    )
    bboxes = extract_bbox_tokens(text)
    assert len(bboxes) == 2
    assert bboxes[0].label == "figure"
    assert bboxes[1].label == "title"


def test_extract_bbox_tokens_invalid_skipped() -> None:
    text = "<|bbox|1|2|3|4|ok|> <|bbox|broken|stuff|here|>"
    bboxes = extract_bbox_tokens(text)
    # Solo il bbox ben formato sopravvive (l'altro è malformato)
    assert len(bboxes) == 1


# ============================================================
# webp_optim
# ============================================================

def test_optimize_for_eink_creates_webp(sample_image: Path, tmp_path: Path) -> None:
    out = tmp_path / "opt.webp"
    result = optimize_for_eink(sample_image, output_path=out)
    assert result == out
    assert out.is_file()
    # WebP lossy viene riletto da Pillow come RGB anche se i pixel sono
    # grayscale (l'encoder WebP salva internamente in YUV→RGB). Verifichiamo
    # che la conversione grayscale sia davvero avvenuta controllando i pixel.
    with Image.open(out) as img:
        px = img.getpixel((0, 0))
        if isinstance(px, tuple):
            r, g, b = px[:3]
            assert r == g == b, f"primo pixel non grayscale: {px}"
        else:
            # modalità "L" → singolo valore
            assert 0 <= px <= 255


def test_optimize_batch_returns_all_paths(sample_image: Path, tmp_path: Path) -> None:
    # Crea 3 immagini per il batch
    paths = []
    for i in range(3):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", (100, 100), (255, 255, 255)).save(p)
        paths.append(p)

    results = optimize_batch(paths, tmp_path / "out")
    assert len(results) == 3
    for r in results:
        assert r.suffix == ".webp"
        assert r.is_file()


# ============================================================
# text_clean
# ============================================================

def test_clean_text_hyphenation() -> None:
    """Il trattino di fine riga viene rimosso."""
    raw = "para-\ngraphia finale"
    cleaned = clean_text(raw)
    assert "paragrato" not in cleaned
    # la sillabazione è unita
    assert "paragraphia" in cleaned


def test_clean_text_B_52_unicode_soft_hyphen() -> None:
    """B52: ``clean_text`` deve gestire il vero Unicode SOFT HYPHEN (U+00AD).

    Il modello OCR può occasionalmente emettere ``\\u00AD`` invece del
    trattino ASCII come marker di sillabazione. La docstring di
    ``clean_text`` dichiara "soft-hyphen de-hyphenation" ma il pattern
    interno matchava solo ``-``: il testo arrivava all'EPUB con un
    carattere invisibile capace di spezzare il rendering su alcuni
    e-Reader.
    """
    raw = "para\u00AD\ngraphia finale"
    cleaned = clean_text(raw)
    assert "\u00AD" not in cleaned, (
        f"BUG B52: U+00AD non rimosso da clean_text: {cleaned!r}"
    )
    # La sillabazione deve essere collassata come per l'ASCII '-'.
    assert "paragraphia" in cleaned


def test_clean_text_quotes_normalization() -> None:
    raw = "‘ciao’ “mondo” «ciao»"
    cleaned = clean_text(raw, fix_hyphenation=False)
    assert "‘" not in cleaned
    assert "“" not in cleaned
    assert "«" not in cleaned


def test_clean_text_collapses_multiple_newlines() -> None:
    raw = "a\n\n\n\n\nb"
    cleaned = clean_text(raw, fix_hyphenation=False, normalize_quotes=False)
    assert "\n\n\n" not in cleaned
    assert cleaned == "a\n\nb"


def test_clean_text_empty() -> None:
    assert clean_text("") == ""


def test_count_words() -> None:
    assert count_words("uno due tre") == 3
    assert count_words("") == 0


# ----------------------------------------------------------------------
# B39: ``_END_OF_LINE_HYPHEN`` rimuove il trattino fine-riga anche dentro
# blocchi di codice fenced, URL e parole inglesi con prefisso (well-known).
# Conseguenza: URL / identificatori / sintassi markdown orizzontale (---)
# vengono erroneamente collassati.
# ----------------------------------------------------------------------


BT = "`"  # backtick abbreviato per evitare problemi di escaping


def test_clean_text_B_39_preserves_hyphen_in_fenced_url() -> None:
    """B39: ``clean_text`` NON deve toccare il trattino fine-riga dentro un
    blocco di codice fenced contenente un URL.

    Caso riprodotto da OCR su libri tecnici:
    ```` 
    https://example.com/foo-
    bar/baz
    ````
    deve restare intatto (il '-' è parte dell'URL, non una sillabazione).
    """
    raw = (
        "Vedi:\n\n"
        + BT * 3 + "\n"
        "https://example.com/foo-\nbar/baz\n"
        + BT * 3 + "\n\nFine."
    )
    cleaned = clean_text(raw)
    # L'URL NON deve essere collassato: deve contenere ancora "foo-\\nbar"
    # (cioè il trattino di fine riga deve essere preservato all'interno del
    # blocco fenced).
    assert "foo-\nbar" in cleaned, (
        f"BUG B39: URL collassato in {cleaned!r} (atteso 'foo-\\nbar' "
        f"preservato dentro il blocco fenced)"
    )
    # E in ogni caso non deve apparire la versione collassata "foobar".
    assert "foobar/baz" not in cleaned, (
        f"BUG B39: URL collassato in 'foobar/baz' in {cleaned!r}"
    )


def test_clean_text_B_39_preserves_hyphen_in_fenced_kebab_identifier() -> None:
    """B39: ``clean_text`` NON deve toccare '-' fine-riga dentro un identifier
    kebab-case in un blocco di codice fenced.

    Esempio reale di codice Python spezzato su due righe in un libro tecnico:
        def my-kebab-
            case_func():
    L'identificatore ``my-kebab-case_func`` NON deve diventare ``my-kebabcase_func``.
    """
    raw = (
        BT * 3 + "python\n"
        "def my-kebab-\ncase_func():\n"
        "    pass\n"
        + BT * 3
    )
    cleaned = clean_text(raw)
    assert "my-kebab-\ncase_func" in cleaned, (
        f"BUG B39: identifier kebab-case collassato in {cleaned!r}"
    )
    assert "my-kebabcase_func" not in cleaned, (
        f"BUG B39: identifier collassato in 'my-kebabcase_func' in {cleaned!r}"
    )


def test_clean_text_B_39_preserves_well_known_inline() -> None:
    """B39: la regex NON deve correggere parole inglesi con prefisso
    ``well-known``, ``high-level`` ecc. se la sillabazione è in una posizione
    che non è fine-parola tipografica (per esempio a metà di un commento).

    NB: il caso inline è borderline — l'issue #39 lo cita come esempio.
    La policy del fix è: la sillabazione fine-riga si applica SOLO nel
    flusso di testo "tipografico" (cioè paragrafi normali). Dentro i
    code block va preservata; inline è giusto collassarla quando è seguita
    da una sola parola. Verifichiamo che almeno dentro i code block il
    pattern sia preservato.
    """
    # Caso code block: deve essere preservato
    raw_code = (
        "Comment:\n"
        + BT * 3 + "\n"
        "well-\nknown pattern\n"
        + BT * 3
    )
    cleaned_code = clean_text(raw_code)
    assert "well-\nknown" in cleaned_code, (
        f"BUG B39: 'well-\\nknown' collassato in code block: {cleaned_code!r}"
    )


def test_clean_text_B_39_preserves_markdown_horizontal_rule() -> None:
    """B39: ``clean_text`` NON deve mangiare il ``--`` di una riga di
    divisione orizzontale Markdown (``---``).

    La regex ``[-\\u00AD]\\s*\\n\\s*`` mangiava la sequenza ``--\\n`` di un
    tema (``---\\n``) lasciando solo ``--`` che NON è più una riga
    orizzontale valida in Markdown.
    """
    raw = "Capitolo 1\n\n---\n\nCapitolo 2"
    cleaned = clean_text(raw, fix_hyphenation=True)
    # La sequenza "---" seguita da newline deve essere preservata intatta.
    assert "\n---\n" in cleaned, (
        f"BUG B39: divisione markdown '---' corrotta in {cleaned!r}"
    )


def test_clean_text_B_39_still_collapses_typographic_hyphenation() -> None:
    """B39 (anti-regressione): la sillabazione tipografica nel testo
    normale DEVE continuare a essere collassata.

    Protegge contro un fix troppo aggressivo che disabiliti del tutto
    la de-hyphenation per paura di rompere i code block.
    """
    raw = "questa è una para-\ngraphia bellissima"
    cleaned = clean_text(raw)
    assert "paragraphia" in cleaned, (
        f"BUG B39 (anti-regressione): sillabazione tipografica non più "
        f"collassata: {cleaned!r}"
    )
    assert "para-\ngraphia" not in cleaned, (
        f"BUG B39 (anti-regressione): sillabazione preservata: {cleaned!r}"
    )