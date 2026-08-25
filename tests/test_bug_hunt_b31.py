"""Riproduzione isolata del bug B31 — UI Slider ``pages_per_batch`` con
default incoerente rispetto a CLI / ``InferenceConfig``.

Issue #31: in ``relictoepub.ui.components.advanced_options`` il
``gr.Slider`` per ``pages_per_batch`` è istanziato con ``value=1``,
``maximum=20``. Questo contraddice:

* la default ``InferenceConfig.pages_per_batch = 20``
* la CLI ``convert_one.py --pages-per-batch`` con ``default=20``
* il README (sezione Usage e tabella CLI flags)
* il piano ``docs/superpowers/plans/...`` (paper: 20–30 pagine)

Effetto: un utente che apre la UI Gradio senza toccare lo slider
parte con 1 pagina per batch, comportamento lentissimo rispetto al
resto della pipeline. Quando l'utente NON tocca lo slider perché si
fida del default della UI, ottiene prestazioni molto peggiori della
CLI.

Questo test:

1. Verifica la riproduzione: il valore iniziale dello slider è 1
   (bug) mentre la sorgente canonica di verità (``InferenceConfig``)
   dice 20.
2. Verifica il fix atteso: dopo la correzione il valore dello slider
   deve essere 20, identico al default di ``InferenceConfig`` e alla
   CLI.
"""

from __future__ import annotations

from dataclasses import fields


def _slider_pages_per_batch_value() -> int:
    """Restituisce il valore iniziale del Slider ``pages_per_batch``.

    Evita di importare direttamente ``gradio`` nei test (che potrebbe
    non essere disponibile o potrebbe avere side effects su
    inizializzazione) leggendo il codice sorgente del modulo.
    In alternativa, quando Gradio è disponibile, istanzia
    ``advanced_options()`` ed estrae ``.value``.
    """
    try:
        from relictoepub.ui.components import advanced_options
    except Exception as exc:  # pragma: no cover - Gradio non installato
        raise RuntimeError(
            "Impossibile importare advanced_options(): Gradio mancante?"
        ) from exc

    opts = advanced_options()
    slider = opts["pages_per_batch"]
    # gr.Slider espone ``.value`` come proprietà pubblica.
    return int(slider.value)


def _cli_pages_per_batch_default() -> int:
    """Default di ``--pages-per-batch`` nella CLI.

    Parsing deterministico: leggiamo il codice sorgente di
    ``convert_one.py`` ed estraiamo il valore letterale di
    ``default=`` dopo ``add_argument("--pages-per-batch"``. Questo
    evita di dover istanziare argparse (che fallirebbe sui
    posizionali obbligatori) ed è indipendente dai side-effect
    dell'import del modulo.
    """
    import re
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    convert_one = scripts_dir / "convert_one.py"
    text = convert_one.read_text(encoding="utf-8")
    # Pattern: ``--pages-per-batch``, ``type=int``, ``default=N``
    # dove N può essere un int letterale.
    match = re.search(
        r'add_argument\(\s*[\'"]--pages-per-batch[\'"][^)]*?default\s*=\s*(\d+)',
        text,
        flags=re.DOTALL,
    )
    assert match, (
        f"Impossibile trovare il default di --pages-per-batch in {convert_one}"
    )
    return int(match.group(1))


def _inference_config_default() -> int:
    """Default di ``InferenceConfig.pages_per_batch``."""
    from relictoepub.inference.config import InferenceConfig
    cfg = InferenceConfig()
    return int(cfg.pages_per_batch)


def test_inference_config_has_pages_per_batch_field() -> None:
    """Sentinella: il field deve esistere (protegge da typo futuri)."""
    from relictoepub.inference.config import InferenceConfig
    names = {f.name for f in fields(InferenceConfig())}
    assert "pages_per_batch" in names


def test_b31_pages_per_batch_slider_default_must_match_inference_config() -> None:
    """B31 - REPRODUZIONE + FIX.

    Lo Slider della UI deve avere lo stesso default di ``InferenceConfig``.
    """
    cfg_default = _inference_config_default()
    slider_value = _slider_pages_per_batch_value()

    assert slider_value == cfg_default, (
        f"B31 REPRODUCED: UI Slider pages_per_batch.value={slider_value}, "
        f"ma InferenceConfig.pages_per_batch={cfg_default}. "
        f"Aggiornare il default dello slider per coerenza con CLI/README/config."
    )


def test_b31_pages_per_batch_slider_default_must_match_cli() -> None:
    """B31 - REPRODUZIONE + FIX (lato CLI).

    Lo Slider della UI deve avere lo stesso default della CLI.
    """
    cli_default = _cli_pages_per_batch_default()
    slider_value = _slider_pages_per_batch_value()

    assert slider_value == cli_default, (
        f"B31 REPRODUCED: UI Slider pages_per_batch.value={slider_value}, "
        f"ma --pages-per-batch CLI default={cli_default}."
    )


def test_b31_slider_maximum_at_least_default() -> None:
    """B31 - REGRESSION GUARD.

    Lo Slider deve poter esprimere almeno il proprio default
    (``maximum >= value``). Senza questo controllo, un futuro
    refactor che dimentichi di aggiornare ``maximum`` dopo aver
    cambiato ``value`` reintroduce silenziosamente il bug.
    """
    from relictoepub.ui.components import advanced_options
    opts = advanced_options()
    slider = opts["pages_per_batch"]
    assert int(slider.maximum) >= int(slider.value), (
        f"Slider massimo={slider.maximum} < valore={slider.value}: "
        "impossibile esprimere il default con il range attuale."
    )


def test_b31_slider_minimum_is_one() -> None:
    """B31 - REGRESSION GUARD.

    Lo Slider deve partire almeno da 1 (non si possono avere batch
    di zero pagine). Protegge da refactor che azzerino il minimo.
    """
    from relictoepub.ui.components import advanced_options
    opts = advanced_options()
    slider = opts["pages_per_batch"]
    assert int(slider.minimum) >= 1, (
        f"Slider minimum={slider.minimum}: un batch di <1 pagine "
        "non ha senso (sarebbe ignorato dal loop)."
    )
