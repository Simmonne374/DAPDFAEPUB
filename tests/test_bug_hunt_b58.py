"""Riproduzione isolata del B58: caption OCR troncata.

Il :func:`Pipeline.run_iter` estrae la caption di una figura/tabella
prendendo i primi 400 caratteri di testo successivi al tag
``<|det|>image_caption[...]<|/det|>``. Quando la caption reale è più
lunga di 400 caratteri (es. didascalie di figure con descrizione
estesa, didascalie di tabelle con molte colonne), viene troncata.

Questo test:
1. Verifica la riproduzione: caption di >400 caratteri troncata a 400.
2. Verifica il fix: caption preservata integralmente.

Scenario (modello OCR emette, in una sola pagina):

    <|det|>image[100,100,800,700]<|/det|>
    <|det|>image_caption[120,720,780,770]<|/det|><long_caption_text>

Atteso: ``<figcaption>`` contiene l'intera caption, NON troncata.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig
from relictoepub.pipeline import Pipeline

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc non installato (richiesto per build_epub)",
)


# Caption su UNA sola riga, >400 char. Il modello emette la caption
# senza "\n" intermedi (la chiusura col newline arriva dopo il testo).
LONG_CAPTION = (
    "Figura 1: schema dettagliato dell'architettura del sistema di "
    "compressione del modello Unlimited-OCR con pipeline a 4 stadi "
    "(encoder, decoder, quantizzazione e post-processing) e relativi "
    "iperparametri di training, validazione e inferenza su GPU NVIDIA "
    "Ada Lovelace con 48GB di VRAM e supporto al formato FP16 a basso "
    "consumo, ottimizzato per ambienti cloud con fattore di forma 2U "
    "rack-mountable e raffreddamento ad aria forzata con ventole PWM."
)
# Sanity check: la caption è effettivamente >400 char.
assert len(LONG_CAPTION) > 400, (
    f"LONG_CAPTION deve essere > 400 char per il test; ha {len(LONG_CAPTION)}"
)


class _LongCaptionRunner:
    """Mock OCR che emette image + image_caption con caption >400 char."""

    DEFAULT_MARKDOWN = (
        "# Capitolo con didascalia lunga\n\n"
        "Testo OCR.\n\n"
        "<|det|>image[100, 100, 800, 700]<|/det|>\n"
        f"<|det|>image_caption[120, 720, 780, 770]<|/det|>{LONG_CAPTION}\n"
    )

    def __init__(self, config: InferenceConfig) -> None:
        self.config = config

    def run_batch_iter(self, image_paths):
        yield self.DEFAULT_MARKDOWN, "running"
        yield self.DEFAULT_MARKDOWN, "done"

    def run_batch(self, image_paths):
        result = MagicMock()
        result.markdown = self.DEFAULT_MARKDOWN
        result.raw_text = self.DEFAULT_MARKDOWN
        result.page_separators = len(image_paths)
        return result

    @staticmethod
    def _strip_image_tokens(text: str) -> str:
        return text


def _patch_long_caption_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sostituisce il runner OCR con quello che emette una caption >400 char."""

    def _factory(config):
        return _LongCaptionRunner(config)

    monkeypatch.setattr("relictoepub.pipeline.UnlimitedOCRRunner", _factory)


def test_long_caption_is_not_truncated_to_400_chars(
    sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B58 - REPRODUZIONE + FIX.

    La caption di una figura deve essere preservata per intero.
    Il bug attuale tronca a 400 caratteri, perdendo informazione.

    Inoltre, il testo caption troncato si riversa nel flusso principale
    come testo orfano dopo il ``</figure>``, causando duplicazione.
    """
    _patch_long_caption_runner(monkeypatch)
    pipeline = Pipeline(
        inference_config=InferenceConfig(pages_per_batch=1),
        dpi=150,
        target_size=512,
        max_pages_per_batch=1,
        eink_optimize=False,
        metadata=BookMetadata(title="T"),
    )
    out = tmp_path / "long_caption.epub"
    result = pipeline.run(sample_pdf, out, progress_callback=lambda e: None)
    cleaned = result.extra["cleaned_markdown"]

    expected = LONG_CAPTION.strip()

    # La caption COMPLETA deve essere presente nel <figcaption>
    assert expected in cleaned, (
        "BUG B58: la caption OCR è stata troncata.\n"
        f"Atteso (len={len(expected)}): {expected!r}\n"
        f"Ottenuto: {cleaned!r}"
    )

    # La caption NON deve essere tagliata a 400 char (sentinella del bug).
    # Verifica che la parte finale della caption ("...ventole PWM.")
    # sia presente nel testo e che NON ci sia testo duplicato fuori dal
    # <figcaption> (sintomo del bug originale: la caption veniva troncata
    # e la parte eccedente re-inclusa come paragrafo orfano dopo </figure>).
    occurrences = cleaned.count("ventole PWM.")
    # sample_pdf è di 3 pagine -> la caption appare 1 volta per pagina.
    assert occurrences == 3, (
        f"BUG B58: la caption e' troncata a 400 char. Atteso 3 occorrenze "
        f"(1 per pagina del sample), trovate {occurrences}. cleaned:\n{cleaned!r}"
    )
    # Regressione specifica: la caption NON deve comparire anche come
    # testo orfano DOPO </figure>. Il pattern e' "<figcaption>...</figcaption>"
    # seguito da "\n\n" + testo duplicato.
    import re as _re
    orphan_matches = _re.findall(
        r"</figcaption>\s*</figure>\s*\n\n([^<\n].*?ventole PWM\.)",
        cleaned,
    )
    assert not orphan_matches, (
        f"BUG B58 (regressione): la caption appare DUPLICATA come testo orfano "
        f"dopo </figure> per {len(orphan_matches)} pagine. cleaned:\n{cleaned!r}"
    )