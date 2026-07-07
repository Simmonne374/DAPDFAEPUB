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


# Trattino di fine riga seguito da a-capo: "soft hyphen" → da unire
_END_OF_LINE_HYPHEN = re.compile(r"-\s*\n\s*")

# Apostrofi tipografici → ASCII (gli e-Reader come Kindle base non li gestiscono)
_TYPOGRAPHIC_QUOTES = re.compile(r"[‘’`´]")  # solo la serie "left-single + backtick"
_TYPOGRAPHIC_QUOTES_DOUBLE = re.compile(r"[“”«»]")

# Spaziature multiple
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE = re.compile(r"[ \t]+\n")


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

    if normalize_quotes:
        text = _TYPOGRAPHIC_QUOTES.sub("'", text)
        text = _TYPOGRAPHIC_QUOTES_DOUBLE.sub('"', text)

    if fix_hyphenation:
        # Caso 1: "parola-\ncont" → "parolacont" (sillabazione riunita)
        text = _END_OF_LINE_HYPHEN.sub("", text)
        # Caso 2: "parola \n cont" su righe molto corte → mantengo il
        # newline come singolo spazio, pypandoc gestirà la spaziatura
        text = re.sub(r"(?<=\S)\n(?=\S)", " ", text)

    # Rimuovi tag di det/bbox residui (difesa)
    text = re.sub(r"<\|det\|>.*?\[.*?\]<\|/det\|>", "", text)
    text = re.sub(r"<\|bbox\|.*?\|>", "", text)

    # Collassa 3+ newline in 2 (per separare i paragrafi in Markdown)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    # Rimuovi spazi trailing prima di newline
    text = _TRAILING_WHITESPACE.sub("\n", text)

    return text.strip()


def count_words(text: str) -> int:
    """Contatore semplice di parole (whitespace-split)."""
    return len(text.split())


__all__ = ["clean_text", "count_words"]
