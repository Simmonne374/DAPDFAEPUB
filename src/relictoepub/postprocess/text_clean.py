"""Pulizia del testo OCR emesso da Unlimited-OCR.

Per il caso d'uso *libri moderni con grafica* il testo è già
relativamente pulito (i libri moderni usano una tipografia
regolare, niente scomposizioni manuali). Tuttavia:

* I libri più vecchi o di case editrici con layout giustificato
  presentano trattini di sillabazione a fine riga (``"para-\n
  grafo"`` → ``"para-grafo"`` con trattino ``soft``).
* Le citazioni possono avere apostrofi tipografici (``'`` invece
  di ``'``) che alcuni e-Reader non gestiscono bene.
* Spaziature multiple o righe vuote multiple vanno collassate.

Queste regex trasformano il Markdown grezzo in un Markdown
*normalizzato* adatto alla compilazione EPUB3.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Trattino di fine riga seguito da a-capo: "soft hyphen" → da unire.
# Match sia il trattino ASCII che lo Unicode SOFT HYPHEN (U+00AD) che il
# modello OCR può occasionalmente emettere al posto del "-" ASCII.
_END_OF_LINE_HYPHEN = re.compile(r"[-\u00AD]\s*\n\s*")

# B39: blocchi di codice fenced (```` ``` ```` e `~~~`). La regex
# cattura l'intero blocco inclusi i fence di apertura/chiusura e
# l'eventuale info-string (``python``, ``text``, …). Il flag ``re.DOTALL``
# permette al ``.*?`` di matchare i newline (i code block sono multi-linea).
_FENCED_CODE_BLOCK = re.compile(
    r"(?P<fence>```|~~~)[^\n]*\n.*?^(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# B39: righe markdown "orizzontali" o setext-underline, fatte SOLO di
# ``-``, ``_``, ``*`` o ``=`` (con almeno 3 caratteri). Senza protezione,
# la de-hyphenation mangerebbe l'ultimo ``-`` di una riga ``---``,
# trasformandola in ``--`` (non più valida come divisore markdown).
_HORIZONTAL_RULE = re.compile(r"^[ \t]*([-_*])\1{2,}[ \t]*$", re.MULTILINE)

# Placeholder usato per sostituire temporaneamente i code block durante la
# de-hyphenation. La sequenza è volutamente improbabile nel testo OCR
# (``\x00CODEBLOCK0\x00``, ``\x00CODEBLOCK1\x00``, …) così da non collidere
# con il contenuto reale e da essere riconoscibile nei test diagnostici.
_B39_PLACEHOLDER_PREFIX = "\x00CODEBLOCK"
_B39_PLACEHOLDER_SUFFIX = "\x00"

# Apostrofi tipografici → ASCII (gli e-Reader come Kindle base non li gestiscono)
_TYPOGRAPHIC_QUOTES = re.compile(r"[‘’`´]")  # solo la serie "left-single + backtick"
_TYPOGRAPHIC_QUOTES_DOUBLE = re.compile(r"[“”«»]")

# Spaziature multiple
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE = re.compile(r"[ \t]+\n")

# Bolt optimization: Hoisted pre-compiled regex patterns & fast-path triggers for clean_text.
_INLINE_NEWLINE = re.compile(r"(?<=\S)\n(?=\S)")
_CLEAN_DET = re.compile(r"<\|det\|>[^\n]*?\[.*?\][^\n]*?<\|/det\|>")
_CLEAN_BBOX = re.compile(r"<\|bbox\|[^\n]*?\|>")


def clean_text(text: str, *, fix_hyphenation: bool = True, normalize_quotes: bool = True) -> str:
    """Applica la pipeline di normalizzazione al testo OCR.

    Args:
        text: Markdown grezzo emesso da Unlimited-OCR.
        fix_hyphenation: Se ``True``, unisce le parole spezzate a fine riga
            con un trattino ``soft`` (``parola-\\ngraphia`` → ``parolagraphia``
            — si presume che il modello mantenga il senso delle parole).
        normalize_quotes: Se ``True``, sostituisce gli apostrofi/doppie
            virgolette tipografici con equivalenti ASCII.

    Returns:
        Testo pulito, pronto per la conversione in XHTML via pypandoc.
    """
    if not text:
        return text

    # B39: proteggi i blocchi di codice fenced DA TUTTE le trasformazioni.
    # Senza questa protezione due bug distinti si manifestano:
    # 1) ``_END_OF_LINE_HYPHEN`` mangia il ``-`` fine-riga anche quando fa
    #    parte di un URL (``foo-\nbar`` in code block) o di un identifier
    #    (``my-kebab-\ncase_func``), corrompendo URL / sintassi Python /
    #    markdown orizzontale (``---\n``).
    # 2) ``_TYPOGRAPHIC_QUOTES`` contiene il carattere backtick ``\u0060``
    #    che verrebbe sostituito con ``'``, distruggendo i fence ``` ``` e
    #    tutto il markdown strutturato all'interno del blocco.
    # La protezione va applicata PRIMA di ogni altra regex e i blocchi vanno
    # ripristinati ALLA FINE, dopo i collassi di newline/spazi che potrebbero
    # modificare leggermente il testo circostante.
    code_blocks: list[str] = []
    def _stash_code_block(match: re.Match[str]) -> str:
        code_blocks.append(match.group(0))
        return f"{_B39_PLACEHOLDER_PREFIX}{len(code_blocks) - 1}{_B39_PLACEHOLDER_SUFFIX}"
    text = _FENCED_CODE_BLOCK.sub(_stash_code_block, text)

    # B39: proteggi anche le righe orizzontali markdown (``---``, ``***``,
    # ``___``). La regex ``_END_OF_LINE_HYPHEN`` mangerebbe l'ultimo ``-``
    # di ``---\n`` lasciando ``--`` (non più una regola orizzontale valida).
    text = _HORIZONTAL_RULE.sub(_stash_code_block, text)

    if normalize_quotes:
        text = _TYPOGRAPHIC_QUOTES.sub("'", text)
        text = _TYPOGRAPHIC_QUOTES_DOUBLE.sub('"', text)

    if fix_hyphenation:
        # Caso 1: "parola-\ncont" → "parolacont" (sillabazione riunita)
        text = _END_OF_LINE_HYPHEN.sub("", text)
        # Caso 2: "parola \n cont" su righe molto corte → mantengo il
        # newline come singolo spazio, pypandoc gestirà la spaziatura
        text = _INLINE_NEWLINE.sub(" ", text)

    # Rimuovi tag di det/bbox residui (difesa). La pipeline ``pipeline.py``
    # consuma già tutti i tag ``<|det|>...<|/det|>`` noti prima di invocare
    # ``clean_text``; queste regex sono un safety-net per tag malformati
    # sfuggiti al parser. Fast-path: evita l'esecuzione di regex se non presenti.
    if "<|det|>" in text:
        text = _CLEAN_DET.sub("", text)
    if "<|bbox|" in text:
        text = _CLEAN_BBOX.sub("", text)

    # Collassa 3+ newline in 2 (per separare i paragrafi in Markdown)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    # Rimuovi spazi trailing prima di newline
    text = _TRAILING_WHITESPACE.sub("\n", text)

    # B39: ripristina i blocchi di codice protetti (dopo tutti gli altri
    # collassi, così i newline circostanti sono già normalizzati).
    for i, block in enumerate(code_blocks):
        text = text.replace(
            f"{_B39_PLACEHOLDER_PREFIX}{i}{_B39_PLACEHOLDER_SUFFIX}",
            block,
        )

    return text.strip()


def count_words(text: str) -> int:
    """Contatore semplice di parole (whitespace-split)."""
    return len(text.split())


__all__ = ["clean_text", "count_words"]
