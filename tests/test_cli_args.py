"""Test di validazione degli argomenti CLI di ``convert_one.py``.

Issue #36 — ``--pages-per-batch`` e ``--dpi`` sono accettati senza
validazione di range:

* ``--pages-per-batch 1000`` viene silenziosamente cappato a 20 da
  ``Pipeline.__init__`` (``min(max_pages_per_batch, config.pages_per_batch)``),
  senza nessun warning all'utente.
* ``--dpi 50`` o ``--dpi 5000`` viene passato direttamente a PyMuPDF
  e può causare OOM (matrice di rendering fuori scala) o crash
  senza un messaggio di errore chiaro.

Questi test verificano che la CLI rifiuti esplicitamente i valori
fuori range con un messaggio amichevole prima di avviare la
pipeline.

Constraints scelti (coerenti con piano + README):

* ``--pages-per-batch`` ∈ [1, 30] — il paper di Unlimited-OCR
  raccomanda batch ≤ 30 pagine per stare nel contesto 32K token.
* ``--dpi`` ∈ {150, 200, 250, 300, 400, 600} — insieme chiuso dei
  valori più usati (qualità archiviatica e ragionevoli per E-ink).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Aggiunge la root del progetto al ``sys.path`` per poter importare
# ``scripts.convert_one`` (che è nella directory ``scripts/`` alla
# radice del repository) — stesso meccanismo già usato dallo script
# stesso quando viene eseguito da qualunque working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _import_parse_args():
    """Import lazy di ``_parse_args``: evita side-effect al collezionamento dei test."""
    from scripts.convert_one import _parse_args

    return _parse_args


# ---------------------------------------------------------------------------
# Test #36 — --pages-per-batch fuori range
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [0, -1, -5, 31, 50, 100, 1000])
def test_cli_pages_per_batch_rejects_out_of_range(bad_value: int) -> None:
    """Issue #36: ``--pages-per-batch`` fuori [1, 30] deve essere rifiutato.

    Pre-fix: ``argparse`` accetta qualunque intero, il capping avviene
    silenziosamente in ``Pipeline.__init__`` senza feedback all'utente.

    Post-fix atteso: ``parser.error()`` con messaggio che cita il valore
    ricevuto e l'intervallo valido.
    """
    parse_args = _import_parse_args()
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["input.pdf", "--pages-per-batch", str(bad_value)])
    assert exc_info.value.code == 2, (
        f"--pages-per-batch={bad_value} dovrebbe uscire con codice 2, "
        f"ottenuto {exc_info.value.code}"
    )


@pytest.mark.parametrize("good_value", [1, 5, 10, 20, 25, 30])
def test_cli_pages_per_batch_accepts_in_range(good_value: int) -> None:
    """I valori in [1, 30] devono passare senza errori (no falsi positivi)."""
    parse_args = _import_parse_args()
    ns = parse_args(["input.pdf", "--pages-per-batch", str(good_value)])
    assert ns.pages_per_batch == good_value


def test_cli_pages_per_batch_error_message_mentions_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Il messaggio d'errore deve mostrare il valore ricevuto
    e l'intervallo valido, in modo che l'utente sappia come correggere.
    """
    parse_args = _import_parse_args()
    with pytest.raises(SystemExit):
        parse_args(["input.pdf", "--pages-per-batch", "1000"])
    captured = capsys.readouterr()
    combined = captured.err + captured.out
    assert "1000" in combined, (
        f"Errore deve menzionare il valore 1000 ricevuto, ottenuto: {combined!r}"
    )
    assert "pages-per-batch" in combined, (
        f"Errore deve menzionare il flag --pages-per-batch: {combined!r}"
    )


# ---------------------------------------------------------------------------
# Test #36 — --dpi fuori dal set consentito
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        -100,  # negativo
        -1,
        0,
        1,  # sotto il minimo
        12,  # sotto il primo step
        72,  # primo DPI logico di PyMuPDF ma non nel set
        100,  # sotto
        149,
        175,
        281,
        299,
        301,  # off-by-one intorno a 300
        500,
        601,  # off-by-one intorno a 600
        999,
        5000,  # OOM-class
    ],
)
def test_cli_dpi_rejects_unsupported_value(bad_value: int) -> None:
    """Issue #36: ``--dpi`` deve essere rifiutato fuori dal set
    {150, 200, 250, 300, 400, 600}.
    """
    parse_args = _import_parse_args()
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["input.pdf", "--dpi", str(bad_value)])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("good_value", [150, 200, 250, 300, 400, 600])
def test_cli_dpi_accepts_supported_values(good_value: int) -> None:
    """Tutti e 6 i valori supportati devono passare senza errori."""
    parse_args = _import_parse_args()
    ns = parse_args(["input.pdf", "--dpi", str(good_value)])
    assert ns.dpi == good_value


def test_cli_dpi_error_message_mentions_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Il messaggio d'errore deve menzionare il valore ricevuto."""
    parse_args = _import_parse_args()
    with pytest.raises(SystemExit):
        parse_args(["input.pdf", "--dpi", "5000"])
    captured = capsys.readouterr()
    combined = captured.err + captured.out
    assert "5000" in combined, (
        f"Errore deve menzionare il valore 5000 ricevuto: {combined!r}"
    )
    assert "--dpi" in combined, (
        f"Errore deve menzionare il flag --dpi: {combined!r}"
    )


# ---------------------------------------------------------------------------
# Test invarianti — default e sanity check
# ---------------------------------------------------------------------------


def test_cli_default_pages_per_batch_is_20() -> None:
    """Default CLI non deve cambiare accidentalmente col fix."""
    parse_args = _import_parse_args()
    ns = parse_args(["input.pdf"])
    assert ns.pages_per_batch == 20


def test_cli_default_dpi_is_300() -> None:
    """Default CLI non deve cambiare accidentalmente col fix."""
    parse_args = _import_parse_args()
    ns = parse_args(["input.pdf"])
    assert ns.dpi == 300


def test_cli_combined_valid_args_pass() -> None:
    """Combinazione di flag validi non rifiuta correttamente."""
    parse_args = _import_parse_args()
    ns = parse_args(
        [
            "input.pdf",
            "--pages-per-batch", "20",
            "--dpi", "300",
            "--quant", "int4",
            "--title", "Test",
            "--author", "Tester",
        ],
    )
    assert ns.pages_per_batch == 20
    assert ns.dpi == 300
    assert ns.quant == "int4"
    assert ns.title == "Test"
    assert ns.author == "Tester"
