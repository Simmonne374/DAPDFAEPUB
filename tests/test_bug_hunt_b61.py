"""Riproduzione isolata del bug B61 — regressione introdotta dal Bolt
optimization di ``BBox.from_string``.

BUG B61: l'ottimizzazione Bolt "fast-path substring branching" (commit
``cbfe32d``, refuso in ``f926dd5``) ha modificato ``BBox.from_string``
in ``src/relictoepub/postprocess/bbox_crop.py`` da::

    match = _DET_PATTERN.search(raw) if "<|det|>" in raw else None
    if not match:
        match = _BBOX_PATTERN.search(raw) if "<|bbox|" in raw else None
        if not match:
            raise ValueError(...)
        ...

a::

    if "<|det|>" in raw:
        match = _DET_PATTERN.search(raw)
        if match:
            ...return...
    elif "<|bbox|" in raw:
        match = _BBOX_PATTERN.search(raw)
        if match:
            ...return...

Il refuso ``if/elif`` invece di ``if/if/if/if`` annidati ha **perso la
logica di fallback**: prima, se il match DET falliva, il codice tentava
il pattern BBOX; ora, se la stringa contiene ``<|det|>`` la funzione
prova SOLO il pattern DET e solleva ``ValueError`` su qualsiasi stringa
che abbia ``<|det|>`` malformato ma contenga un ``<|bbox|>`` valido.

Conseguenze reali (non solo teoriche):

* OCR reale su libri scientifici: il modello può occasionalmente
  emettere token ``<|det|>`` malformati (label con parentesi quadre
  OCR-misclassified, spazi extra, ecc.) **insieme** a tag ``<|bbox|>``
  validi nello stesso file di output. La pipeline chiama
  ``BBox.from_string`` indirettamente solo in contesti ristretti,
  ma il rischio di regressione è concreto per gli utenti che
  importano ``from relictoepub.postprocess.bbox_crop import BBox`` e
  parsano output OCR "rumoroso" con ``from_string``.

* Anti-regressione: il comportamento originale era
  "fall-through proporzionalmente greedy" (prova DET, poi BBOX).
  Dopo il refuso Bolt, il comportamento è "fail-fast sul primo tag
  presente", che è una semantica diversa e non documentata.

Questo test:
1. **Riproduce** la regressione: ``BBox.from_string`` con stringa
   contentente ``<|det|>`` malformato + ``<|bbox|>`` valido solleva
   ``ValueError`` invece di ritornare il BBox.
2. **Valida** il fix atteso: ``from_string`` ritorna correttamente
   il ``BBox`` dal pattern BBOX anche in presenza di ``<|det|>`.
3. **Anti-regressione**: il comportamento "DET-only → BBox" continua
   a funzionare (cioè ``from_string`` ritorna il BBox dal pattern
   DET quando il pattern DET matcha, ignorando qualsiasi ``<|bbox|>``
   presente).
"""
from __future__ import annotations

import pytest

from relictoepub.postprocess.bbox_crop import BBox


# ===============================================================
# B61 — REPRODUCTION (failing test sul master)
# ===============================================================


def test_b61_falls_back_to_bbox_when_det_does_not_match() -> None:
    """B61 — REPRODUZIONE.

    Quando ``raw`` contiene ``<|det|>`` MA il pattern DET non matcha
    (es. label con caratteri non validi, regex malformata), la
    funzione DEVE fare fallback al pattern BBOX se presente.

    Comportamento attuale (post-regressione B61): ``ValueError``.
    Comportamento atteso: ``BBox`` dal pattern BBOX.
    """
    raw = "<|det|>MALFORMED<|/det|> <|bbox|100|200|300|400|image|>"

    bbox = BBox.from_string(raw)

    # Il bbox deve essere estratto dal pattern <|bbox|> valido
    assert bbox.x_min == 100, (
        f"BUG B61: x_min non estratto dal fallback bbox (atteso 100, "
        f"ottenuto {bbox.x_min}). La stringa contiene <|det|> malformato "
        f"e <|bbox|> valido: il fallback al bbox è stato perso nella "
        f"ottimizzazione Bolt."
    )
    assert bbox.y_min == 200
    assert bbox.x_max == 300
    assert bbox.y_max == 400
    assert bbox.label == "image", (
        f"BUG B61: label non preservata (atteso 'image', ottenuto "
        f"{bbox.label!r})"
    )


def test_b61_falls_back_to_bbox_with_unmatched_det_pattern() -> None:
    """B61 — REPRODUZIONE variante.

    Il pattern DET richiede ``[<x1>, <y1>, <x2>, <y2>]`` (coordinate con
    virgole). Se il contenuto del tag ha solo testo senza casini, il
    pattern DET non matcha — ma il fallback a BBOX deve funzionare.
    """
    raw = "<|det|>image_caption_no_brackets_here<|/det|> \n <|bbox|50|60|70|80|title|>"

    bbox = BBox.from_string(raw)

    assert bbox.x_min == 50
    assert bbox.y_min == 60
    assert bbox.x_max == 70
    assert bbox.y_max == 80
    assert bbox.label == "title", (
        f"BUG B61: label non estratta dal fallback bbox (atteso 'title', "
        f"ottenuto {bbox.label!r})"
    )


def test_b61_det_pattern_match_still_wins_over_bbox() -> None:
    """B61 — anti-regressione.

    Quando ENTRAMBI i pattern matchano (caso limite in cui la stringa
    contiene entrambi i tag), il pattern DET vince (è il primo
    tentativo). Questo è il comportamento storico e non va cambiato.
    """
    # Stringa che contiene un <|det|> valido + un <|bbox|> valido.
    # Il pattern DET è più specifico (ha label + brackets), quindi
    # vincerebbe comunque con la regex ``search`` se fosse il primo
    # tentativo. Verifichiamo che il fix mantenga questa priorità.
    raw = (
        "<|det|>image [10,20,30,40]<|/det|> "
        "<|bbox|100|200|300|400|figure|>"
    )

    bbox = BBox.from_string(raw)

    # Il DET pattern ha coordinate 10/20/30/40, label 'image'.
    # Il BBOX pattern ha coordinate 100/200/300/400, label 'figure'.
    assert bbox.x_min == 10, (
        f"BUG B61 (anti-regressione): DET pattern dovrebbe vincere su "
        f"BBOX. Ottenuto x_min={bbox.x_min} (atteso 10 dal DET)."
    )
    assert bbox.label == "image", (
        f"BUG B61 (anti-regressione): label dovrebbe essere 'image' "
        f"(dal DET). Ottenuto {bbox.label!r}."
    )


def test_b61_bbox_only_still_works() -> None:
    """B61 — anti-regressione: solo BBOX continua a funzionare."""
    bbox = BBox.from_string("<|bbox|100|200|300|400|image|>")
    assert bbox.x_min == 100
    assert bbox.label == "image"


def test_b61_det_only_still_works() -> None:
    """B61 — anti-regressione: solo DET continua a funzionare."""
    bbox = BBox.from_string("<|det|>image [100,200,300,400]<|/det|>")
    assert bbox.x_min == 100
    assert bbox.label == "image"


def test_b61_truly_malformed_still_raises() -> None:
    """B61 — anti-regressione: nessun pattern valido → ValueError.

    Una stringa completamente malformata (senza tag riconoscibili) deve
    continuare a sollevare ``ValueError`` dopo il fix.
    """
    with pytest.raises(ValueError):
        BBox.from_string("this is just random text without any tags")