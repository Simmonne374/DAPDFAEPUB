"""Regression test per la visibilita' del componente download EPUB.

Il componente download viene creato **nascosto**: ``components.epub_download()``
usa ``visible=value is not None``, quindi a riposo ``visible=False``.

In Gradio la proprieta' ``visible`` e' "sticky": passare un valore semplice
(``None`` o una stringa) a uno slot aggiorna **solo** ``value`` e lascia
``visible`` invariato. Percio' ogni yield di ``_run_pipeline`` che punta allo
slot download deve usare ``gr.update(value=..., visible=...)``.

Prima del fix (PR #74) gli yield passavano valori semplici: il bottone di
download **non appariva mai** dopo una conversione riuscita.
"""

import ast
import contextlib
import inspect
from pathlib import Path
from unittest.mock import patch

import gradio as gr

from relictoepub.pipeline import PipelineCancelledError, ProgressEvent
from relictoepub.ui.components import epub_download
from relictoepub.ui.gradio_app import _run_pipeline

# Indice dello slot download nella tupla yieldata da ``_run_pipeline``:
# (log, gallery, download, model_status, pipeline_state, stop_btn, run_btn)
DOWNLOAD_SLOT = 2


def _run(**overrides):
    """Invoca ``_run_pipeline`` con kwargs di default sovrascrivibili."""
    kwargs = {
        "pdf_path": None,
        "pages_per_batch": 2,
        "dpi": 150,
        "quantization": "none",
        "eink_optimize": False,
        "title": "Test Book",
        "author": "Test Author",
        "output_dir": "",
        "resume_enabled": False,
        "pipeline_state": None,
    }
    kwargs.update(overrides)
    return _run_pipeline(**kwargs)


def _collect(generator):
    """Drena il generatore raccogliendo gli yield e l'eccezione finale."""
    yields = []
    error = None
    while True:
        try:
            yields.append(next(generator))
        except StopIteration:
            break
        except gr.Error as exc:
            # L'unica eccezione che ``_run_pipeline`` lascia uscire e' la
            # ``gr.Error`` rilanciata nel ramo di errore della conversione.
            error = exc
            break
    return yields, error


def _download_payload(result):
    """Estrae lo slot download e verifica che sia prodotto da ``gr.update()``."""
    payload = result[DOWNLOAD_SLOT]
    assert isinstance(payload, dict), (
        f"lo slot download deve essere un dict gr.update(), got {type(payload).__name__}"
    )
    assert payload.get("__type__") == "update", (
        f"lo slot download deve venire da gr.update(), got {payload!r}"
    )
    return payload


@contextlib.contextmanager
def _mocked_pipeline(*, events=(), error=None):
    """Sostituisce ``Pipeline`` con una mock che emette gli eventi richiesti.

    Gli eventi vengono emessi nell'ordine indicato; ``error`` (se impostato)
    viene sollevato subito dopo l'ultimo evento.
    """
    with (
        patch("relictoepub.ui.gradio_app.Pipeline") as mock_pipeline_cls,
        patch("relictoepub.ui.gradio_app.check_model_status") as mock_status,
    ):
        mock_status.return_value = (True, "🟢 **Modello rilevato localmente**")

        def _side_effect(*args, **kwargs):
            def _gen():
                yield from events
                if error is not None:
                    raise error

            return _gen()

        mock_pipeline_cls.return_value.run_iter.side_effect = _side_effect
        yield mock_pipeline_cls


def _dummy_pdf(tmp_path: Path) -> Path:
    """Crea un PDF finto su disco (serve solo a far passare ``is_file()``)."""
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    return pdf_path


def test_epub_download_component_is_created_hidden():
    """Il download nasce nascosto: va mostrato esplicitamente a runtime."""
    component = epub_download()
    assert component.visible is False


def test_run_pipeline_without_pdf_keeps_download_hidden():
    """Nessun PDF selezionato -> il download resta nascosto."""
    yields, error = _collect(_run(pdf_path=None))
    assert error is None
    assert len(yields) == 1
    payload = _download_payload(yields[0])
    assert payload["value"] is None
    assert payload["visible"] is False


def test_run_pipeline_invalid_file_keeps_download_hidden(tmp_path):
    """File non valido -> il download resta nascosto."""
    yields, error = _collect(_run(pdf_path=str(tmp_path / "missing.pdf")))
    assert error is None
    assert len(yields) == 1
    payload = _download_payload(yields[0])
    assert payload["value"] is None
    assert payload["visible"] is False


def test_run_pipeline_progress_keeps_download_hidden(tmp_path):
    """Durante il run il download resta nascosto (nessun EPUB pronto)."""
    pdf_path = _dummy_pdf(tmp_path)
    events = [
        ProgressEvent(phase="rendering", message="Rendering", current=1, total=2),
        ProgressEvent(phase="ocr", message="OCR", current=1, total=2),
    ]
    with _mocked_pipeline(events=events, error=RuntimeError("boom")):
        yields, error = _collect(_run(pdf_path=str(pdf_path)))

    assert isinstance(error, gr.Error)
    # L'ultimo yield e' quello di errore; tutti i precedenti sono di progresso.
    assert len(yields) == 3
    for result in yields[:-1]:
        payload = _download_payload(result)
        assert payload["visible"] is False
        assert payload["value"] is None


def test_run_pipeline_success_shows_download(tmp_path):
    """Successo -> il download diventa visibile e punta all'EPUB finale."""
    pdf_path = _dummy_pdf(tmp_path)

    # Il producer usa tempfile.gettempdir() + uuid4(): li fissiamo per
    # poter predisporre l'EPUB temporaneo che la pipeline sposta in destinazione.
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (temp_dir / "relictoepub_testtest.epub").write_bytes(b"PK\x03\x04fake epub")

    events = [ProgressEvent(phase="rendering", message="Rendering", current=1, total=1)]

    with (
        _mocked_pipeline(events=events),
        patch("tempfile.gettempdir", return_value=str(temp_dir)),
        patch("uuid.uuid4") as mock_uuid,
    ):
        mock_uuid.return_value.hex = "testtest"
        yields, error = _collect(_run(pdf_path=str(pdf_path)))

    assert error is None
    assert len(yields) == 2

    # Yield di progresso: il download non e' ancora pronto.
    assert _download_payload(yields[0])["visible"] is False

    # Yield finale: il download e' visibile e punta all'EPUB definitivo.
    payload = _download_payload(yields[-1])
    assert payload["visible"] is True
    assert payload["value"] == str(tmp_path / "book.epub")
    assert (tmp_path / "book.epub").is_file()


def test_run_pipeline_cancelled_keeps_download_hidden(tmp_path):
    """Cancellazione -> il download resta nascosto (nessun EPUB pronto)."""
    pdf_path = _dummy_pdf(tmp_path)
    with _mocked_pipeline(error=PipelineCancelledError(completed_batches=2)):
        yields, error = _collect(_run(pdf_path=str(pdf_path)))

    assert error is None
    assert len(yields) == 1
    payload = _download_payload(yields[0])
    assert payload["value"] is None
    assert payload["visible"] is False


def test_run_pipeline_error_keeps_download_hidden(tmp_path):
    """Errore generico -> il download resta nascosto e l'errore viene rilanciato."""
    pdf_path = _dummy_pdf(tmp_path)
    with _mocked_pipeline(error=RuntimeError("boom")):
        yields, error = _collect(_run(pdf_path=str(pdf_path)))

    assert isinstance(error, gr.Error)
    assert len(yields) == 1
    payload = _download_payload(yields[0])
    assert payload["value"] is None
    assert payload["visible"] is False


def test_every_download_slot_yield_uses_gr_update():
    """Invariante esaustiva: nessuno yield puo' usare un valore semplice.

    Copre anche i percorsi aggiunti in futuro: ogni yield di
    ``_run_pipeline`` che punta allo slot download deve chiamare
    ``gr.update(...)``, altrimenti ``visible`` resterebbe invariato e il
    bottone di download non comparirebbe.
    """
    tree = ast.parse(inspect.getsource(_run_pipeline))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Yield) or node.value is None:
            continue
        value = node.value
        elements = value.elts if isinstance(value, ast.Tuple) else [value]
        if len(elements) <= DOWNLOAD_SLOT:
            continue
        slot = elements[DOWNLOAD_SLOT]
        assert isinstance(slot, ast.Call), (
            f"lo slot download deve essere un gr.update(...), trovato {ast.dump(slot)}"
        )
        func = slot.func
        assert isinstance(func, ast.Attribute) and func.attr == "update", (
            f"lo slot download deve chiamare gr.update(), trovato {ast.dump(func)}"
        )
        checked += 1
    assert checked >= 6, (
        f"attesi almeno 6 yield verso lo slot download, trovati {checked}"
    )


def test_gr_update_payload_carries_value_and_visible():
    """``gr.update`` produce sempre un dict con ``value`` e ``visible``."""
    payload = gr.update(value="book.epub", visible=True)
    assert payload["value"] == "book.epub"
    assert payload["visible"] is True
    assert payload["__type__"] == "update"
