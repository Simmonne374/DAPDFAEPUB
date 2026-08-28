"""Regression test per bug B33 (issue #33).

Bug originale:
    ``_run_pipeline`` in ``src/relictoepub/ui/gradio_app.py`` (linea ~155)
    crea un EPUB temporaneo in ``tempfile.gettempdir() /
    "relictoepub_{uuid}.epub"`` e poi (linee ~234-249) **copia** il file
    nella destinazione finale. Tuttavia il file temporaneo originale in
    ``%TEMP%``/``/tmp`` **non viene mai eliminato** nel caso di successo.

Conseguenze:
    * Su una macchina dove l'utente converte molti libri (uso tipico del
      progetto: scansioni di libri cartacei), ``%TEMP%`` accumula una
      copia orfana di ogni EPUB generato, ciascuna di diversi MB.
    * Solo i casi di errore/cancellazione hanno il cleanup (``unlink``
      esplicito, linee 279-283 e 293-298).
    * Per confronto, l'uso CLI (``scripts/convert_one.py``) scrive
      direttamente nella destinazione e non ha questo problema.

Aspettativa (post-fix):
    * Dopo un run andato a buon fine, il file temporaneo ``temp_output_epub``
      (in ``tempfile.gettempdir()``) deve essere stato rimosso.
    * Il fix deve preservare l'attuale comportamento per i casi di errore
      e cancellazione (già coperti).

Strategia del test (mock-driven, niente GPU/modello OCR):
    1. ``monkeypatch.setattr`` su ``tempfile.gettempdir`` per restituire
       una directory temporanea sotto ``tmp_path``.
    2. Mock di ``Pipeline`` che, quando chiamato, scrive un finto EPUB
       nel ``temp_output_epub`` ricevuto come argomento.
    3. Guidiamo ``_run_pipeline`` fino al completamento.
    4. Assert: il file in ``tempfile.gettempdir()/relictoepub_*.epub``
       **non esiste più** dopo il completamento.
    5. La copia nella destinazione finale (``pdf_path_obj.with_suffix('.epub')``)
       invece **deve esistere** (regressione: non cancellare l'output
       dell'utente).
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _reload_gradio_app():
    """Reimporta ``gradio_app`` per azzerare eventuali cache di import."""
    from relictoepub.ui import gradio_app

    return importlib.reload(gradio_app)


def _make_fake_pipeline(filled_bytes: bytes = b"PK\x03\x04" + b"x" * (100 * 1024)):
    """Crea un mock di ``Pipeline`` che scrive un finto EPUB nel ``temp_output_epub``
    ricevuto da ``run_iter``, emette un singolo evento ``done`` e termina.

    Ritorna ``(fake_pipeline, state)`` dove ``state["captured_temp_path"]`` è
    popolato con il path del tempfile creato (o ``None`` se non chiamato).
    """
    state: dict = {"captured_temp_path": None}

    mock_event = MagicMock()
    mock_event.phase = "done"
    mock_event.percent = 100.0
    mock_event.total = 1
    mock_event.message = "Fatto"
    mock_event.extra = {}

    def _run_iter_side_effect(pdf_path_obj_arg, output_epub_arg):  # noqa: ARG001
        """Generator function: scrive il finto EPUB e poi yield mock_event."""
        temp_path = Path(output_epub_arg)
        state["captured_temp_path"] = temp_path
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(filled_bytes)
        yield mock_event

    fake_pipeline = MagicMock()
    fake_pipeline.run_iter.side_effect = _run_iter_side_effect
    fake_pipeline.is_cancelled.return_value = False
    fake_pipeline.cancel.return_value = None

    return fake_pipeline, state


def test_run_pipeline_removes_temp_epub_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B33 - REPRODUZIONE + FIX.

    Il file temporaneo ``tempfile.gettempdir()/relictoepub_*.epub`` deve
    essere rimosso dopo il successo della conversione.
    """
    pdf_dir = tmp_path / "input"
    pdf_dir.mkdir()
    pdf_path = pdf_path_obj = pdf_dir / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% dummy\n")

    # Sposta ``tempfile.gettempdir`` sotto tmp_path così possiamo
    # intercettare il file ``relictoepub_*.epub`` creato dal codice
    # sotto test senza inquinare la vera directory di sistema.
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    gradio_app = _reload_gradio_app()
    fake_pipeline, state = _make_fake_pipeline()

    with patch("relictoepub.ui.gradio_app.Pipeline", return_value=fake_pipeline), \
         patch("relictoepub.ui.gradio_app.check_model_status",
               return_value=(True, "🟢 OK")):

        generator = gradio_app._run_pipeline(
            pdf_path=str(pdf_path),
            pages_per_batch=2,
            dpi=150,
            quantization="none",
            eink_optimize=False,
            title="T",
            author="A",
            output_dir="",  # salva accanto al PDF sorgente
            resume_enabled=True,
            pipeline_state=None,
        )

        # La pipeline mock completa con successo; nessuna eccezione attesa.
        results = list(generator)

    captured = state["captured_temp_path"]
    assert captured is not None, "Pipeline mock non ha catturato il path del tempfile"
    assert "relictoepub_" in captured.name, (
        f"path tempfile inatteso: {captured}"
    )

    # ASSErTIONE CHIAVE (B33): dopo il successo, il file temporaneo
    # orfano in %TEMP% NON deve più esistere.
    assert not captured.exists(), (
        f"BUG B33: il file temporaneo {captured} sopravvive in "
        f"{tempfile.gettempdir()} dopo la conversione andata a buon fine. "
        f"Ogni conversione lascia una copia orfana dell'EPUB in %TEMP%."
    )

    # Regressione: l'EPUB destinato all'utente deve invece esistere.
    final_dest = pdf_path_obj.with_suffix(".epub")
    assert final_dest.exists(), (
        f"L'EPUB finale {final_dest} non è stato creato accanto al PDF "
        f"sorgente (regressione rispetto al comportamento atteso)."
    )
    assert final_dest.stat().st_size > 0, "L'EPUB finale è vuoto"


def test_run_pipeline_removes_temp_epub_on_success_with_custom_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B33 - variante: anche con ``output_dir`` custom il tempfile va pulito."""
    pdf_dir = tmp_path / "input"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% dummy\n")

    out_dir = tmp_path / "epubs_out"
    out_dir.mkdir()

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    gradio_app = _reload_gradio_app()
    fake_pipeline, state = _make_fake_pipeline(filled_bytes=b"x" * 50_000)

    with patch("relictoepub.ui.gradio_app.Pipeline", return_value=fake_pipeline), \
         patch("relictoepub.ui.gradio_app.check_model_status",
               return_value=(True, "🟢 OK")):

        generator = gradio_app._run_pipeline(
            pdf_path=str(pdf_path),
            pages_per_batch=2,
            dpi=150,
            quantization="none",
            eink_optimize=False,
            title="T",
            author="A",
            output_dir=str(out_dir),
            resume_enabled=True,
            pipeline_state=None,
        )

        list(generator)

    captured = state["captured_temp_path"]
    assert captured is not None, (
        "Mock non ha catturato il path del tempfile durante il run"
    )
    assert not captured.exists(), (
        f"BUG B33 (custom output_dir): il tempfile {captured} "
        f"non è stato rimosso dopo il successo."
    )

    # La copia nella cartella di destinazione custom deve esistere.
    expected_dest = out_dir / pdf_path.with_suffix(".epub").name
    assert expected_dest.exists(), (
        f"L'EPUB finale {expected_dest} non è stato creato in output_dir."
    )


def test_run_pipeline_removes_temp_epub_on_pipeline_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B33 (no-regression): il cleanup su ``PipelineCancelledError`` resta valido."""
    from relictoepub.pipeline import PipelineCancelledError

    pdf_dir = tmp_path / "input"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% dummy\n")

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    gradio_app = _reload_gradio_app()

    state: dict = {"captured_temp_path": None}

    def _run_iter_side_effect(pdf_path_obj_arg, output_epub_arg):  # noqa: ARG001
        temp_path = Path(output_epub_arg)
        state["captured_temp_path"] = temp_path
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(b"PK\x03\x04")
        # ``raise`` dentro un generator function solleva
        # ``PipelineCancelledError`` alla prima iterazione.
        raise PipelineCancelledError(completed_batches=0)
        if False:  # pragma: no cover  (per far riconoscere la funzione come generator)
            yield

    fake_pipeline = MagicMock()
    fake_pipeline.run_iter.side_effect = _run_iter_side_effect

    with patch("relictoepub.ui.gradio_app.Pipeline", return_value=fake_pipeline), \
         patch("relictoepub.ui.gradio_app.check_model_status",
               return_value=(True, "🟢 OK")):

        generator = gradio_app._run_pipeline(
            pdf_path=str(pdf_path),
            pages_per_batch=2,
            dpi=150,
            quantization="none",
            eink_optimize=False,
            title="T",
            author="A",
            output_dir="",
            resume_enabled=True,
            pipeline_state=None,
        )
        list(generator)

    captured = state["captured_temp_path"]
    assert captured is not None, "Mock non ha catturato il tempfile path"
    # Il file DEVE essere stato rimosso (cleanup pre-esistente).
    assert not captured.exists(), (
        f"REGRESSIONE: il cleanup su cancel è saltato: {captured} "
        f"esiste ancora."
    )


def test_run_pipeline_removes_temp_epub_on_pipeline_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B33 (no-regression): il cleanup su errore generico resta valido."""
    pdf_dir = tmp_path / "input"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% dummy\n")

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    gradio_app = _reload_gradio_app()

    state: dict = {"captured_temp_path": None}

    def _run_iter_side_effect(pdf_path_obj_arg, output_epub_arg):  # noqa: ARG001
        temp_path = Path(output_epub_arg)
        state["captured_temp_path"] = temp_path
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(b"PK\x03\x04")
        raise RuntimeError("errore simulato OCR")
        if False:  # pragma: no cover
            yield

    fake_pipeline = MagicMock()
    fake_pipeline.run_iter.side_effect = _run_iter_side_effect

    with patch("relictoepub.ui.gradio_app.Pipeline", return_value=fake_pipeline), \
         patch("relictoepub.ui.gradio_app.check_model_status",
               return_value=(True, "🟢 OK")):

        generator = gradio_app._run_pipeline(
            pdf_path=str(pdf_path),
            pages_per_batch=2,
            dpi=150,
            quantization="none",
            eink_optimize=False,
            title="T",
            author="A",
            output_dir="",
            resume_enabled=True,
            pipeline_state=None,
        )
        # La funzione solleva ``gr.Error`` su errori generici.
        import gradio as gr
        with pytest.raises(gr.Error):
            list(generator)

    captured = state["captured_temp_path"]
    assert captured is not None
    assert not captured.exists(), (
        f"REGRESSIONE: il cleanup su errore è saltato: {captured} "
        f"esiste ancora."
    )
