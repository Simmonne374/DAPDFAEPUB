"""Componenti Gradio riusabili.

Queste factory functions producono blocchi UI pre-configurati che vengono
composti in :mod:`relictoepub.ui.gradio_app`. Tenerli separati aiuta a:

* evitare di riscrivere le stesse gr.Blocks() ovunque,
* poter testare i singoli blocchi,
* consentire a chi volesse personalizzare la UI di partire dai blocchi
  invece che dall'app intera.
"""

from __future__ import annotations

import gradio as gr
from huggingface_hub import try_to_load_from_cache


def quantization_choices() -> tuple[list, str]:
    """Scelte Quantizzazione adattate all'ambiente (CUDA/CPU, bnb ok?)."""
    choices = [
        ("4-bit (consigliato per GPU ≥8GB)", "int4"),
        ("8-bit (richiede ≥16GB VRAM)", "int8"),
        ("Nessuna quantizzazione (CPU / ≥24GB VRAM)", "none"),
    ]
    default = "int4"
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
    except Exception:
        cuda_ok = False
    try:
        import bitsandbytes  # noqa: F401
        bnb_ok = True
    except Exception:
        bnb_ok = False
    if not cuda_ok or not bnb_ok:
        return [choices[2]], "none"
    return choices, default


def check_model_status(model_id: str = "baidu/Unlimited-OCR") -> tuple[bool, str]:
    """Controlla se il file di configurazione e tutti i pesi del modello sono presenti nella cache locale."""
    import json
    try:
        # 1. Controlla config.json
        config_path = try_to_load_from_cache(model_id, "config.json")
        if not isinstance(config_path, str):
            return False, "🔴 **Modello non presente localmente** (scaricalo ora o verrà scaricato al primo avvio)"
            
        # 2. Controlla se è un modello shardato (cerca l'indice dei pesi)
        index_path = try_to_load_from_cache(model_id, "model.safetensors.index.json")
        if isinstance(index_path, str):
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                weight_files = set(data.get("weight_map", {}).values())
                # Verifica che tutti i file dei pesi siano presenti in cache
                for wf in weight_files:
                    wf_path = try_to_load_from_cache(model_id, wf)
                    if not isinstance(wf_path, str):
                        return False, "🔴 **Modello incompleto** (download pesi in corso o non avviato)"
            return True, "🟢 **Modello rilevato localmente** (pronto all'uso)"
            
        # 3. Altrimenti controlla il file singolo model.safetensors
        single_path = try_to_load_from_cache(model_id, "model.safetensors")
        if isinstance(single_path, str):
            return True, "🟢 **Modello rilevato localmente** (pronto all'uso)"
    except Exception:
        pass
    return False, "🔴 **Modello non presente localmente** (scaricalo ora o verrà scaricato al primo avvio)"


def upload_pdf() -> gr.File:
    """Blocco upload per il PDF sorgente."""
    return gr.File(
        label="📄 PDF sorgente",
        file_types=[".pdf"],
        type="filepath",  # ci serve il path, non i bytes
        height=100,
    )


def destination_folder() -> gr.Textbox:
    """Campo di testo per la cartella di destinazione dell'EPUB."""
    return gr.Textbox(
        label="📁 Cartella di destinazione (opzionale)",
        placeholder="Es: C:\\Libri - Lascia vuoto per salvare nella cartella del PDF",
        lines=1,
    )


def advanced_options() -> dict[str, gr.components.Component]:
    """Ritorna un dict di componenti per l'accordion "Opzioni avanzate".

    Restituisce un dict (e non una gr.Accordion) per consentire all'app
    di montarli insieme in qualunque layout.
    """
    return {
        "pages_per_batch": gr.Slider(
            minimum=1, maximum=20, value=1, step=1,
            label="Pagine per batch OCR (1–20)",
            info="Consigliato: 1 per stabilità e per prevenire OOM/loop su GTX 1080 Ti",
        ),
        "dpi": gr.Slider(
            minimum=150, maximum=600, value=300, step=50,
            label="Risoluzione rendering (DPI)",
            info="300 DPI è il sweet-spot qualità/performance",
        ),
        "quantization": gr.Dropdown(
            choices=quantization_choices()[0],
            value=quantization_choices()[1],
            label="Quantizzazione del modello",
        ),
        "eink_optimize": gr.Checkbox(
            value=True,
            label="Ottimizzazione E-ink (WebP grayscale)",
            info="PNG → WebP, scala di grigi, boost contrasto",
        ),
        "title": gr.Textbox(
            label="Titolo del libro", placeholder="Titolo del libro",
        ),
        "author": gr.Textbox(
            label="Autore", placeholder="Autore (opzionale)",
        ),
    }


def log_panel() -> gr.Textbox:
    """Textbox di log read-only con bottone copia."""
    return gr.Textbox(
        label="📋 Log",
        lines=12,
        max_lines=20,
        interactive=False,
        show_copy_button=True,
        autoscroll=True,
        placeholder="Il log apparirà qui durante la conversione…",
    )


def progress_panel() -> gr.Progress:
    """Componente Progress standalone (da usare come segnaposto).

    Gradio espone ``gr.Progress()`` come context manager — non è
    istanziabile staticamente; questo helper restituisce un segnaposto
    così le firme sono più leggibili. Nel codice reale usa direttamente
    ``gr.Progress()`` dentro la funzione del bottone.
    """
    return None  # type: ignore[return-value]


def gallery_preview() -> gr.Gallery:
    """Galleria di preview delle prime 3 pagine renderizzate."""
    return gr.Gallery(
        label="🖼️ Anteprima delle prime pagine",
        columns=3,
        rows=1,
        height=300,
        object_fit="contain",
    )


def epub_download(value: str | None = None) -> gr.File:
    """Componente per il download dell'EPUB finale."""
    return gr.File(
        label="📥 Download EPUB",
        value=value,
        interactive=False,
        visible=value is not None,
    )


__all__ = [
    "advanced_options",
    "check_model_status",
    "destination_folder",
    "epub_download",
    "gallery_preview",
    "log_panel",
    "upload_pdf",
]
