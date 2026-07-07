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
import os
import time
import traceback
from collections.abc import Iterator
from pathlib import Path

import gradio as gr

import sys
import subprocess

from relictoepub.compile.build_epub import BookMetadata
from relictoepub.inference.config import InferenceConfig, QuantizationMode
from relictoepub.pipeline import Pipeline, ProgressEvent
from relictoepub.ui.components import (
    advanced_options,
    epub_download,
    gallery_preview,
    log_panel,
    upload_pdf,
    check_model_status,
    destination_folder,
)

logger = logging.getLogger(__name__)


def _format_event(event: ProgressEvent) -> str:
    elapsed_str = "" if not event.extra else ""
    bar = ""
    if event.total:
        filled = int(round(event.percent / 5))
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
    progress: gr.Progress = gr.Progress(),
) -> Iterator[tuple[str, list, object, object]]:
    """Wrapper Gradio di :meth:`Pipeline.run_iter`.

    Yields tuple ``(log_text, gallery_items, download_file, model_status)`` per
    aggiornare i componenti della UI.
    """
    log_text = ""
    gallery: list = []

    if pdf_path is None:
        yield "❌ Nessun PDF selezionato.", gallery, None, gr.update()
        return

    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.is_file():
        yield f"❌ File non valido: {pdf_path}", gallery, None, gr.update()
        return

    import tempfile
    import uuid
    import shutil

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
        except Exception:
            cuda_ok = False
        if not cuda_ok:
            log_text = (
                f"\n⚠️ Quantizzazione {quant_mode.value} non disponibile senza CUDA; "
                f"uso 'none' (lento)."
            )
            quant_mode = QuantizationMode.NONE
    config = InferenceConfig(
        quantization=quant_mode,
        pages_per_batch=pages_per_batch,
    )
    pipeline = Pipeline(
        inference_config=config,
        dpi=dpi,
        max_pages_per_batch=pages_per_batch,
        eink_optimize=eink_optimize,
        metadata=metadata,
    )

    try:
        for event in pipeline.run_iter(pdf_path_obj, temp_output_epub):
            line = _format_event(event)
            log_text = (log_text + "\n" + line).strip()
            # Aggiorna la Progress nativa di Gradio
            if event.total:
                progress((event.current, event.total), desc=event.message)
            else:
                progress(0, desc=event.message)

            # Aggiorna la gallery di preview dopo la fase rendering
            if event.phase == "rendering" and event.extra.get("output_dir"):
                work_dir = Path(event.extra["output_dir"])
                model_dir = work_dir / "model_input"
                if model_dir.is_dir():
                    thumbs = sorted(model_dir.glob("page_*.png"))[:3]
                    gallery = [(str(t), None) for t in thumbs]

            yield log_text, gallery, None, gr.update()

        # Copia il file temporaneo sicuro nella destinazione scelta
        final_dest_str = ""
        if output_dir.strip():
            try:
                output_dir_path = Path(output_dir.strip())
                output_dir_path.mkdir(parents=True, exist_ok=True)
                final_dest_epub = output_dir_path / pdf_path_obj.with_suffix(".epub").name
                shutil.copy(temp_output_epub, final_dest_epub)
                final_dest_str = f"\n📁 Copiato nella cartella di destinazione: {final_dest_epub}"
            except Exception as e:
                final_dest_str = f"\n⚠️ Impossibile copiare nella cartella di destinazione: {e}"
        else:
            try:
                final_dest_epub = pdf_path_obj.with_suffix(".epub")
                shutil.copy(temp_output_epub, final_dest_epub)
                final_dest_str = f"\n📁 Salvato in: {final_dest_epub}"
            except Exception as e:
                final_dest_str = f"\n⚠️ Impossibile salvare nella cartella del PDF: {e}"

        # Evento finale: aggiungi riepilogo e abilita il download
        summary = (
            f"\n\n✅ EPUB pronto!"
            f"{final_dest_str}"
            f"\n📁 Dimensione: {temp_output_epub.stat().st_size / 1024:.1f} KB"
        )
        log_text = (log_text + summary).strip()
        yield log_text, gallery, str(temp_output_epub), check_model_status()[1]
    except Exception as exc:
        err = f"\n❌ Errore: {exc}\n{traceback.format_exc()}"
        log_text = (log_text + err).strip()
        yield log_text, gallery, None, gr.update()


def _download_model_ui() -> Iterator[tuple[str, str, gr.components.Component]]:
    """Avvia il download del modello Unlimited-OCR tramite subprocess.
    
    Yields tuple (log_text, model_status_text, download_button_update).
    """
    log_text = "🔄 Inizio download del modello 'baidu/Unlimited-OCR' (circa 6 GB)..."
    yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False)
    
    # Trova il percorso dell'eseguibile huggingface-cli
    cli_path = Path(sys.executable).parent / "huggingface-cli"
    if sys.platform == "win32" or cli_path.with_suffix(".exe").exists():
        cli_path = cli_path.with_suffix(".exe")
    if not cli_path.exists():
        cli_path = Path("huggingface-cli")  # fallback
        
    cmd = [
        str(cli_path), "download", "baidu/Unlimited-OCR",
        "--include", "*.safetensors", "--include", "*.json", "--include", "*.py",
        "--include", "*.txt", "--include", "*.bin", "--include", "*.md",
        "--quiet"
    ]
    
    process = None
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env
        )
        
        # Legge lo standard output del processo riga per riga
        for line in iter(process.stdout.readline, ""):
            log_text = (log_text + "\n" + line.strip()).strip()
            yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False)
            
        process.wait()
        if process.returncode == 0:
            log_text += "\n\n✅ Modello scaricato e verificato con successo!"
            _, status_str = check_model_status()
            yield log_text, status_str, gr.Button(interactive=True)
        else:
            log_text += f"\n\n❌ Errore durante il download del modello. Codice d'uscita: {process.returncode}"
            yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True)
    except Exception as e:
        log_text += f"\n\n❌ Errore imprevisto durante l'avvio del download: {e}"
        yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True)
    finally:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


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

                run_btn = gr.Button("🚀 Converti in EPUB", variant="primary", size="lg")

            # ============= COLONNA DESTRA — output =============
            with gr.Column(scale=1):
                log = log_panel()
                gallery = gallery_preview()
                download = epub_download()

        # Wiring per il download del modello
        download_btn.click(
            fn=_download_model_ui,
            inputs=[],
            outputs=[log, model_status, download_btn],
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
            ],
            outputs=[log, gallery, download, model_status],
        )

    return demo


__all__ = ["build_demo"]
