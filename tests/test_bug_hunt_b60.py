r"""Riproduzione isolata del bug B60 — ``_TYPOGRAPHIC_QUOTES`` mangia i
backtick ASCII e rompe l'inline code Markdown.

BUG B60: la regex ``_TYPOGRAPHIC_QUOTES`` in
``src/relictoepub/postprocess/text_clean.py`` ha un character class che
include quattro caratteri:

    [‘’ ` ´]
     │  │ │
     │  │ └─ U+00B4 ACUTE ACCENT
     │  └─── U+0060 GRAVE ACCENT / BACKTICK  ← BUG
     └────── U+2019 / U+2018 typographic apostrophes

Il backtick ASCII NON è un apostrofo tipografico: è il delimitatore
dell'inline code Markdown (`` `foo()` ``). Sostituirlo con ``'``
distrugge l'inline code: `` `foo()` `` diventa ``'foo()'`` e a valle
(pypandoc → XHTML) viene renderizzato come testo normale invece che
come ``<code>foo()</code>``.

L'autore autor del B39 era consapevole del problema (``text_clean.py``
linee ~90-94 cita esplicitamente il backtick come "distruggere i fence
```` ``` ````"), ma la protezione con ``_FENCED_CODE_BLOCK`` /
``_HORIZONTAL_RULE`` copre SOLO i blocchi di codice fenced e le righe
orizzontali. L'inline code (`` `foo()` ``) resta esposto.

Questo file:

1. **Riproduce** il bug con un test failing: ``clean_text`` sostituisce
   il backtick dell'inline code con ``'``.
2. **Valida** il fix atteso: il backtick viene preservato, pypandoc
   emette ``<code>foo()</code>``.
3. **Anti-regressione**: gli altri caratteri della regex (acute
   accent ``´``, apostrofi tipografici ``'``/``'``, virgolette
   doppie tipografiche ``"``/``"``/``«``/``»``) continuano a essere
   normalizzati.

Conseguenze del bug in produzione (libri tecnici):

* Identificatori in inline code (`` `my_func()` ``) appaiono come
  ``'my_func()'``, perdendo lo stile ``<code>`` e la possibilità di
  copiarli con un tap sull'e-Reader.
* Comandi shell in inline code (`` `ls -la` ``) appaiono come
  ``'ls -la'``, distorcendo la comprensione del testo tecnico.
* Escape di caratteri ``\``` `` diventa ``\'`` che è un backslash
  seguito da apostrofo — diverso significato.
"""

from __future__ import annotations

import re
import shutil

import pytest

from relictoepub.postprocess.text_clean import (
    _TYPOGRAPHIC_QUOTES,
    clean_text,
)

BT = "`"  # backtick abbreviato per evitare problemi di escaping


# ===============================================================
# B60 — REPRODUCTION (failing test sul master)
# ===============================================================


def test_b60_inline_code_backtick_is_not_replaced() -> None:
    """B60 — REPRODUZIONE.

    ``clean_text`` NON deve sostituire il backtick ASCII usato come
    delimitatore dell'inline code Markdown.

    Scenario riprodotto fedelmente: testo OCR di un libro tecnico
    contiene `` `foo()` `` per indicare una chiamata di funzione.
    Con il bug, l'output contiene ``'foo()'`` e il successivo
    rendering pypandoc perde il tag ``<code>``.

    Asserzione post-fix: il backtick ASCII (U+0060) NON è presente
    nel character class di ``_TYPOGRAPHIC_QUOTES`` e quindi
    ``clean_text`` lo preserva.
    """
    raw = "Usa " + BT + "foo()" + BT + " per la funzione."
    cleaned = clean_text(raw)

    # L'inline code deve essere preservato byte-per-byte.
    assert BT + "foo()" + BT in cleaned, (
        "BUG B60: clean_text ha sostituito i backtick dell'inline code. "
        f"Atteso {BT + 'foo()' + BT!r} in output, ottenuto {cleaned!r}. "
        "La regex _TYPOGRAPHIC_QUOTES include erroneamente il backtick "
        "ASCII (U+0060) e lo sostituisce con apostrofo (')."
    )


def test_b60_double_tick_code_is_not_replaced() -> None:
    """B60 — REPRODUZIONE variante.

    Anche il double-backtick code (`` ``foo()`` ``) deve essere
    preservato. Con il bug, `` `` `` viene sostituito con ``''``
    rendendo impossibile a pypandoc riconoscere il code span.
    """
    raw = "Usa " + BT + BT + "foo()" + BT + BT + " per la funzione."
    cleaned = clean_text(raw)

    assert BT + BT + "foo()" + BT + BT in cleaned, (
        "BUG B60: clean_text ha sostituito i double-backtick del code span. "
        f"Atteso {BT + BT + 'foo()' + BT + BT!r} in output, ottenuto {cleaned!r}."
    )


def test_b60_escaped_backtick_is_not_replaced() -> None:
    """B60 — REPRODUZIONE variante.

    L'escape backtick (comune quando si vuole mostrare il carattere
    letterale in un code span) deve essere preservato.
    """
    raw = "Carattere escape: \\" + BT + " (backtick escaped)."
    cleaned = clean_text(raw)

    assert "\\" + BT in cleaned, (
        "BUG B60: clean_text ha sostituito il backtick escaped. "
        f"Atteso '\\{BT}' in output, ottenuto {cleaned!r}."
    )


def test_b60_regex_pattern_excludes_backtick() -> None:
    """B60 — verifica statica della regex.

    Il pattern ``_TYPOGRAPHIC_QUOTES.pattern`` NON deve contenere il
    carattere U+0060 (backtick ASCII). Verifica difensiva: anche se
    il behavior dinamico cambiasse, il pattern deve essere
    strutturalmente corretto.
    """
    pattern = _TYPOGRAPHIC_QUOTES.pattern
    # Estrai i caratteri del character class.
    m = re.search(r"\[(.+?)\]", pattern)
    assert m is not None, f"Pattern senza character class: {pattern!r}"
    chars = m.group(1)

    assert BT not in chars, (
        "BUG B60: il backtick ASCII (U+0060) è ancora nel character class "
        f"di _TYPOGRAPHIC_QUOTES (chars={chars!r}). Rimuoverlo: la sua "
        "presenza rompe l'inline code Markdown."
    )


# ===============================================================
# B60 — FIX VALIDATION (post-fix rendering end-to-end con pypandoc)
# ===============================================================


@pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc non installato (richiesto per validazione end-to-end)",
)
def test_b60_inline_code_renders_as_code_tag_after_fix() -> None:
    """B60 — VALIDATION.

    Dopo il fix, il testo pulito con inline code deve rendere come
    ``<code>foo()</code>`` quando passato a pypandoc. Con il bug, il
    backtick mancante faceva sì che pypandoc vedesse solo
    ``'foo()'`` e non producesse alcun tag ``<code>``.
    """
    raw = "Usa " + BT + "foo()" + BT + " per la funzione."
    cleaned = clean_text(raw)

    # Sanity: il backtick deve essere preservato da clean_text.
    assert BT + "foo()" + BT in cleaned, (
        f"Pre-condizione fallita: clean_text non ha preservato l'inline "
        f"code. cleaned={cleaned!r}"
    )

    # Verifica il rendering pypandoc.
    import pypandoc
    html = pypandoc.convert_text(
        cleaned, to="html5", format="markdown+smart",
        extra_args=["--wrap=none"],
    )
    assert "<code>foo()</code>" in html, (
        "BUG B60 (post-fix validation): pypandoc non ha reso l'inline "
        f"code come <code>. HTML ottenuto: {html!r}. Il fix deve "
        "preservare i backtick in clean_text."
    )


# ===============================================================
# B60 — ANTI-REGRESSION (altri caratteri della regex)
# ===============================================================


def test_b60_apostrophe_left_single_is_still_replaced() -> None:
    """B60 — anti-regressione: U+2018 (left single quote) continua
    a essere normalizzato in apostrofo ASCII.

    Protegge contro un fix troppo aggressivo che disabiliti del
    tutto la normalizzazione delle virgolette tipografiche.
    """
    raw = "l\u2018hello"
    cleaned = clean_text(raw)
    assert "\u2018" not in cleaned, (
        f"BUG B60 (anti-regressione): U+2018 non normalizzato: {cleaned!r}"
    )
    assert "l'hello" in cleaned, (
        f"BUG B60 (anti-regressione): apostrofo non sostituito: {cleaned!r}"
    )


def test_b60_apostrophe_right_single_is_still_replaced() -> None:
    """B60 — anti-regressione: U+2019 (right single quote) continua
    a essere normalizzato in apostrofo ASCII.
    """
    raw = "don\u2019t"
    cleaned = clean_text(raw)
    assert "\u2019" not in cleaned, (
        f"BUG B60 (anti-regressione): U+2019 non normalizzato: {cleaned!r}"
    )
    assert "don't" in cleaned, (
        f"BUG B60 (anti-regressione): apostrofo non sostituito: {cleaned!r}"
    )


def test_b60_acute_accent_is_still_replaced() -> None:
    """B60 — anti-regressione: U+00B4 (acute accent) continua a
    essere normalizzato in apostrofo ASCII.

    L'acute accent è una sostituzione OCR comune per l'apostrofo
    in italiano (``perch´`` → ``perch'``), spagnolo e francese.
    Mantenerlo nella regex è una scelta deliberata.
    """
    raw = "perch\u00B4 cos\u00B4"
    cleaned = clean_text(raw)
    assert "\u00B4" not in cleaned, (
        f"BUG B60 (anti-regressione): U+00B4 non normalizzato: {cleaned!r}"
    )
    assert "perch'" in cleaned, (
        f"BUG B60 (anti-regressione): acute accent non sostituito: {cleaned!r}"
    )


def test_b60_double_typographic_quotes_still_replaced() -> None:
    """B60 — anti-regressione: virgolette doppie tipografiche
    (``"``/``"``/``«``/``»``) continuano a essere normalizzate.
    """
    raw = "\u201Chello\u201D \u00ABworld\u00BB"
    cleaned = clean_text(raw)
    for ch in ("\u201C", "\u201D", "\u00AB", "\u00BB"):
        assert ch not in cleaned, (
            f"BUG B60 (anti-regressione): {ch!r} (U+{ord(ch):04X}) non "
            f"normalizzato in {cleaned!r}"
        )
    assert '"hello"' in cleaned
    assert '"world"' in cleaned