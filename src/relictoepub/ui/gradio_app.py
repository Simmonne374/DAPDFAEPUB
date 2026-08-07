"""Modulo 6 — App Gradio di RelicToEpub.

Layout a 2 colonne in ``gr.Blocks``:

* **Sinistra**: upload PDF + opzioni avanzate collassate + bottone avvio.
* **Destra**: log streaming, galleria preview, file EPUB scaricabile,
  riepilogo finale.

L'utente clicca **Converti in EPUB**; la UI chiama la pipeline passando
i parametri e fa streaming degli eventi nel log e nella progress bar.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterator
from pathlib import Path

import gradio as gr

from relictoepub.checkpoint import (
    CheckpointStore,
    resolve_checkpoint_dir,
)
from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig, QuantizationMode
from relictoepub.pipeline import (
    Pipeline,
    PipelineCancelledError,
    ProgressEvent,
)
from relictoepub.ui.components import (
    advanced_options,
    check_model_status,
    destination_folder,
    epub_download,
    gallery_preview,
    log_panel,
    upload_pdf,
)

logger = logging.getLogger(__name__)


def _toggle_run_button(pdf_path: str | None) -> dict:
    """Abilita la conversione solo quando Gradio fornisce un file PDF."""
    return gr.update(interactive=bool(pdf_path))


def _inspect_checkpoint(pdf_path: str | None) -> str:
    """Ritorna una stringa markdown con lo stato del checkpoint per ``pdf_path``.

    Mostrato nella UI sotto l'upload PDF come feedback. Tre stati possibili:
    * Nessun PDF selezionato → stringa vuota.
    * Checkpoint non presente → "Nessun checkpoint disponibile".
    * Checkpoint presente → conta batch completati e aggiornato a.
    """
    if not pdf_path:
        return ""
    store = CheckpointStore(resolve_checkpoint_dir(Path(pdf_path)))
    state = store.load()
    if state is None:
        return "ℹ️ Nessun checkpoint disponibile (verrà creato al primo avvio)."
    n_done = len(state.completed_batches)
    return (
        f"♻️ Checkpoint trovato: **{n_done}/{state.total_batches}** "
        f"batch già OCR-ati (aggiornato: {state.updated_at}). "
        f"Spunta 'Riprendi' per non rifarli."
    )


def _clear_checkpoint(pdf_path: str | None) -> str:
    """Cancella il checkpoint del PDF corrente. Ritorna stringa di feedback."""
    if not pdf_path:
        return "⚠️ Seleziona prima un PDF."
    store = CheckpointStore(resolve_checkpoint_dir(Path(pdf_path)))
    if not store.exists():
        return "ℹ️ Nessun checkpoint da cancellare."
    store.clear()
    return "🗑️ Checkpoint eliminato. La prossima conversione ripartirà da zero."


def _request_stop(pipeline_state) -> tuple[str, gr.update]:
    """Gestisce il click sul bottone Stop. Chiama ``Pipeline.cancel()``
    in modo cooperativo (il batch in corso finirà, poi la pipeline emette
    :class:`PipelineCancelledError`). Restituisce ``(messaggio_log, btn_update)``.
    """
    if pipeline_state is None:
        return "ℹ️ Nessuna conversione attiva.", gr.update(interactive=False)
    pipeline: Pipeline = pipeline_state
    if pipeline.is_cancelled():
        return "⚠️ Cancellazione già richiesta.", gr.update(interactive=False)
    pipeline.cancel()
    return (
        "⏹️ Cancellazione richiesta: il batch in corso finirà, poi la pipeline si fermerà e salverà lo stato.",
        gr.update(value="⏳ Arresto…", interactive=False),
    )


def _format_event(event: ProgressEvent) -> str:
    bar = ""
    if event.total:
        filled = round(event.percent / 5)
        bar = f" [{('█' * filled):<20s}] {event.percent:5.1f}%"
    return f"[{event.phase.upper():<10s}]{bar} {event.message}"


def _run_pipeline(
    pdf_path: str | None,
    pages_per_batch: int,
    dpi: int,
    quantization: str,
    eink_optimize: bool,
    title: str,
    author: str,
    output_dir: str,
    resume_enabled: bool,
    pipeline_state,
) -> Iterator[tuple[str, list, object, object, object, gr.update, gr.update]]:
    """Wrapper Gradio di :meth:`Pipeline.run_iter`.

    Yields tuple ``(log_text, gallery_items, download_file, model_status,
    pipeline_state, stop_btn_update, run_btn_update)`` per aggiornare
    i componenti della UI. ``run_btn`` viene disabilitato durante il
    run per impedire seconde esecuzioni in parallelo (BUG #11).
    """
    base_log_text = ""
    gallery: list = []

    if pdf_path is None:
        gr.Warning("Nessun PDF selezionato.")
        yield "❌ Nessun PDF selezionato.", gallery, None, gr.update(), None, gr.update(), gr.update()
        return

    gr.Info("Avvio conversione del PDF, attendere prego...")
    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.is_file():
        gr.Warning("File non valido.")
        yield (
            f"❌ File non valido: {pdf_path}", gallery, None, gr.update(),
            None, gr.update(), gr.update(),
        )
        return

    import shutil
    import tempfile
    import uuid

    # Definisci il path temporaneo sicuro in cui compilare l'EPUB (per bypassare la sandbox Gradio)
    temp_output_epub = Path(tempfile.gettempdir()) / f"relictoepub_{uuid.uuid4().hex[:8]}.epub"

    metadata = BookMetadata(
        title=title or pdf_path_obj.stem,
        author=author or "Unknown",
        language="it",
    )
    # Auto-fallback: se l'utente ha scelto int4/int8 ma la quantizzazione non è
    # utilizzabile (es. CPU-only), ripieghiamo su "none" per non crashare.
    quant_mode = QuantizationMode(quantization)
    if quant_mode != QuantizationMode.NONE:
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
        except ImportError:
            cuda_ok = False
        if not cuda_ok:
            base_log_text = (
                f"\n⚠️ Quantizzazione {quant_mode.value} non disponibile senza CUDA; "
                f"uso 'none' (lento)."
            )
            quant_mode = QuantizationMode.NONE
    config = InferenceConfig(
        quantization=quant_mode,
        pages_per_batch=pages_per_batch,
    )

    # Checkpoint: store persistente accanto al PDF sorgente. Se
    # ``resume_enabled`` è False, cancelliamo qualunque stato pregresso.
    cp_dir = resolve_checkpoint_dir(pdf_path_obj)
    checkpoint_store: CheckpointStore | None = None
    if resume_enabled:
        checkpoint_store = CheckpointStore(cp_dir)
        if not checkpoint_store.exists():
            base_log_text += "\nℹ️ Nessun checkpoint precedente: creo da zero."
    else:
        if cp_dir.is_dir():
            import shutil as _sh
            _sh.rmtree(cp_dir, ignore_errors=True)
            base_log_text += "\n🗑️ Checkpoint precedente eliminato (--no-resume)."

    pipeline = Pipeline(
        inference_config=config,
        dpi=dpi,
        max_pages_per_batch=pages_per_batch,
        eink_optimize=eink_optimize,
        metadata=metadata,
        checkpoint_store=checkpoint_store,
    )

    try:
        for event in pipeline.run_iter(pdf_path_obj, temp_output_epub):
            line = _format_event(event)

            # Se l'evento è transitorio (streaming token), non lo salviamo nella storia di base
            is_transient = event.extra and event.extra.get("transient")
            if is_transient:
                log_to_show = (base_log_text + "\n" + line).strip()
            else:
                base_log_text = (base_log_text + "\n" + line).strip()
                log_to_show = base_log_text



            # Aggiorna la gallery di preview dopo la fase rendering
            if event.phase == "rendering" and event.extra.get("output_dir"):
                work_dir = Path(event.extra["output_dir"])
                model_dir = work_dir / "model_input"
                if model_dir.is_dir():
                    thumbs = sorted(model_dir.glob("page_*.png"))[:3]
                    gallery = [(str(t), None) for t in thumbs]

            yield (
                log_to_show, gallery, None, gr.update(),
                pipeline, gr.update(value="⏹️ Stop", interactive=True),
                # BUG #11: disabilita run_btn durante l'esecuzione.
                gr.update(interactive=False),
            )

        # Copia il file temporaneo sicuro nella destinazione scelta
        final_dest_str = ""
        if output_dir.strip():
            try:
                output_dir_path = Path(output_dir.strip())
                output_dir_path.mkdir(parents=True, exist_ok=True)
                final_dest_epub = output_dir_path / pdf_path_obj.with_suffix(".epub").name
                shutil.copy(temp_output_epub, final_dest_epub)
                final_dest_str = f"\n📁 Copiato nella cartella di destinazione: {final_dest_epub}"
            except (OSError, shutil.SameFileError) as e:
                gr.Warning(f"Impossibile copiare nella cartella di destinazione: {e}")
                final_dest_str = f"\n⚠️ Impossibile copiare nella cartella di destinazione: {e}"
        else:
            try:
                final_dest_epub = pdf_path_obj.with_suffix(".epub")
                shutil.copy(temp_output_epub, final_dest_epub)
                final_dest_str = f"\n📁 Salvato in: {final_dest_epub}"
            except (OSError, shutil.SameFileError) as e:
                gr.Warning(f"Impossibile salvare nella cartella del PDF: {e}")
                final_dest_str = f"\n⚠️ Impossibile salvare nella cartella del PDF: {e}"

        # Evento finale: aggiungi riepilogo e abilita il download
        summary = (
            f"\n\n✅ EPUB pronto!"
            f"{final_dest_str}"
            f"\n📁 Dimensione: {temp_output_epub.stat().st_size / 1024:.1f} KB"
        )
        base_log_text = (base_log_text + summary).strip()
        gr.Info("Conversione EPUB completata con successo!")
        yield (
            base_log_text, gallery, str(temp_output_epub),
            check_model_status()[1],
            None,  # pipeline_state → reset per prossima run
            gr.update(value="⏹️ Stop", interactive=False),
            # BUG #11: ri-abilita il pulsante Converti.
            gr.update(interactive=True),
        )
    except PipelineCancelledError as exc:
        msg = (
            f"\n⏹️ Interrotto dall'utente dopo "
            f"{exc.completed_batches} batch OCR. "
            f"Lo stato è stato salvato (riprendi con la checkbox 'Riprendi')."
        )
        base_log_text = (base_log_text + msg).strip()
        # BUG #10: ripulisci il file EPUB orfano in /tmp, se esiste.
        try:
            if temp_output_epub.exists():
                temp_output_epub.unlink()
        except OSError:
            pass
        gr.Warning("Conversione interrotta. Checkpoint salvato.")
        yield (
            base_log_text, gallery, None,
            gr.update(),
            None,  # reset pipeline_state
            gr.update(value="⏹️ Stop", interactive=False),
            gr.update(interactive=True),
        )
    except (RuntimeError, ValueError, OSError, ImportError, TimeoutError) as exc:
        # BUG #10: cleanup tempfile anche su errori generici.
        try:
            if temp_output_epub.exists():
                temp_output_epub.unlink()
        except OSError:
            pass
        err = f"\n❌ Errore: {exc}\n{traceback.format_exc()}"
        base_log_text = (base_log_text + err).strip()
        yield (
            base_log_text, gallery, None,
            gr.update(),
            None,
            gr.update(value="⏹️ Stop", interactive=False),
            gr.update(interactive=True),
        )
        raise gr.Error(f"Errore durante la conversione: {exc}") from exc


def _download_model_ui() -> Iterator[tuple[str, str, gr.components.Component, gr.update]]:
    """Avvia il download del modello Unlimited-OCR via ``huggingface_hub.snapshot_download``.

    Usa ``gr.Progress(track_tqdm=True)`` per mostrare filename + % live.
    Yields tuple (log_text, model_status_text, download_button_update, progress_update).
    """
    log_text = "🔄 Inizio download del modello 'baidu/Unlimited-OCR' (~6 GB)."
    log_text += "\n\nRestando in questa pagina vedrai i file scaricati uno per uno."
    gr.Info("Avvio download del modello, potrebbe richiedere diversi minuti...")
    yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False), gr.update(visible=True, value=0, label="Download modello…")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log_text += "\n\n❌ `huggingface_hub` non installato. Installazione automatica…"
        yield log_text, "🔴 **Dipendenza mancante**", gr.Button(interactive=True), gr.update(label="Errore")
        raise gr.Error("huggingface_hub non installato.")

    try:
        path = snapshot_download(
            repo_id="baidu/Unlimited-OCR",
            allow_patterns=[
                "*.json", "*.py", "*.txt", "*.md", "*.model",
                "*.safetensors", "*.bin",
                "tokenizer*", "vocab.*", "merges.*", "special_tokens*",
            ],
            tqdm_class=None,  # gradio traccia già la tqdm di default
        )
    except (RuntimeError, ValueError, OSError, TimeoutError, ConnectionError) as exc:
        log_text += f"\n\n❌ Download fallito: {exc}"
        yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True), gr.update(visible=True, label="Riprova download")
        raise gr.Error(f"Download modello fallito: {exc}") from exc

    log_text += f"\n\n✅ Modello scaricato in cache HuggingFace.\nPath: {path}"
    _, status_str = check_model_status()
    gr.Info("Modello scaricato con successo!")
    yield log_text, status_str, gr.Button(interactive=True), gr.update(visible=True, label="Modello scaricato", value=1.0)


def build_demo() -> gr.Blocks:
    """Costruisce l'app Gradio completa e la restituisce non ancora lanciata."""
    opts = advanced_options()

    with gr.Blocks(
        title="RelicToEpub",
        theme=gr.themes.Soft(primary_hue="slate"),
        css="""
        .gradio-container { max-width: 1200px !important; }
        """,
    ) as demo:
        gr.Markdown(
            "# RelicToEpub\n"
            "**PDF → EPUB3** tramite *Baidu Unlimited-OCR* (R-SWA).\n\n"
            "Modello SOTA OmniDocBench (93.23 overall) — quantizzato 4-bit per GTX 1080 Ti."
        )

        with gr.Row():
            # ============= COLONNA SINISTRA — input =============
            with gr.Column(scale=1):
                # Nuova sezione download modello
                with gr.Group():
                    gr.Markdown("### 📦 Modello OCR (Unlimited-OCR)")
                    model_status = gr.Markdown(value=check_model_status()[1])
                    download_btn = gr.Button("📥 Scarica/Aggiorna Modello (~6 GB)", variant="secondary")

                pdf_input = upload_pdf()
                dest_input = destination_folder()

                # Sezione checkpoint / resume
                with gr.Group():
                    gr.Markdown("### ♻️ Ripresa conversione (checkpoint)")
                    checkpoint_status = gr.Markdown(
                        value="ℹ️ Carica un PDF per controllare i checkpoint.",
                    )
                    with gr.Row():
                        resume_toggle = gr.Checkbox(
                            label="Riprendi da checkpoint esistente",
                            value=True,
                            info="Se presente, salta le pagine già OCR-ate.",
                        )
                        clear_checkpoint_btn = gr.Button(
                            "🗑️ Pulisci checkpoint",
                            variant="stop",
                            size="sm",
                        )
                with gr.Accordion("⚙️ Opzioni avanzate", open=False):
                    opts_rendered = [
                        ("pages_per_batch", opts["pages_per_batch"]),
                        ("dpi", opts["dpi"]),
                        ("quantization", opts["quantization"]),
                        ("eink_optimize", opts["eink_optimize"]),
                    ]
                    for _key, comp in opts_rendered:
                        comp.render()  # monta il componente nell'accordion
                    opts["title"].render()
                    opts["author"].render()

                run_btn = gr.Button("🚀 Converti in EPUB", variant="primary", size="lg", interactive=False)
                stop_btn = gr.Button(
                    "⏹️ Stop",
                    variant="stop",
                    size="lg",
                    interactive=False,
                    visible=True,
                )
                # Stato che mantiene l'istanza Pipeline viva durante il run,
                # così il bottone Stop può chiamare pipeline.cancel().
                pipeline_state = gr.State(value=None)

            # ============= COLONNA DESTRA — output =============
            with gr.Column(scale=1):
                log = log_panel()
                gallery = gallery_preview()
                download = epub_download()

        # Wiring per abilitare il bottone di conversione solo quando un PDF è selezionato
        pdf_input.change(
            fn=_toggle_run_button,
            inputs=[pdf_input],
            outputs=[run_btn],
        )
        # Wiring per ispezione checkpoint al cambio PDF
        pdf_input.change(
            fn=_inspect_checkpoint,
            inputs=[pdf_input],
            outputs=[checkpoint_status],
        )
        # Wiring bottone "Pulisci checkpoint"
        clear_checkpoint_btn.click(
            fn=_clear_checkpoint,
            inputs=[pdf_input],
            outputs=[checkpoint_status],
        )

        # Wiring per il download del modello
        download_btn.click(
            fn=_download_model_ui,
            inputs=[],
            outputs=[log, model_status, download_btn, download_btn],
        )

        # Wiring: click → streaming updates su log, gallery, download, status
        run_btn.click(
            fn=_run_pipeline,
            inputs=[
                pdf_input,
                opts["pages_per_batch"],
                opts["dpi"],
                opts["quantization"],
                opts["eink_optimize"],
                opts["title"],
                opts["author"],
                dest_input,
                resume_toggle,
                pipeline_state,
            ],
            outputs=[
                log, gallery, download, model_status,
                pipeline_state, stop_btn, run_btn,
            ],
        )

        # Wiring: bottone Stop → chiama pipeline.cancel() in modo cooperativo.
        stop_btn.click(
            fn=_request_stop,
            inputs=[pipeline_state],
            outputs=[log, stop_btn],
        )

    return demo


__all__ = ["build_demo"]
