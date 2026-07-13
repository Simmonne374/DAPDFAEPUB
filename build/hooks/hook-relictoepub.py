"""Hook PyInstaller per il package ``relictoepub``.

PyInstaller non sempre individua correttamente tutte le importazioni dinamiche
usate dal nostro package (moduli caricati via ``importlib``, Gradio templates,
configurazioni HuggingFace). Questo hook:

* raccoglie moduli importati dinamicamente come ``hiddenimports``;
* include file di dati e template (CSS, JS, icone di Gradio; template
  tokenizer/BPE/SentencePiece di HuggingFace Transformers).

PyInstaller li usa per determinare cosa includere nel bundle.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


# Moduli importati dinamicamente da RelicToEpub
HIDDENIMPORTS = [
    # reload dinamici (es. reload del modulo per test)
    "relictoepub",
    "relictoepub.ui",
    "relictoepub.ui.components",
    "relictoepub.ui.gradio_app",
    "relictoepub.pipeline",
    "relictoepub.ingest",
    "relictoepub.inference",
    "relictoepub.inference.unlimited_ocr",
    "relictoepub.inference.config",
    "relictoepub.postprocess",
    "relictoepub.postprocess.bbox_crop",
    "relictoepub.postprocess.text_clean",
    "relictoepub.postprocess.webp_optim",
    "relictoepub.compile",
    "relictoepub.compile.build_epub",
    "relictoepub.compile.eink_css",
    # moduli di terze parti facilmente dimenticati da PyInstaller
    "bitsandbytes",
    "bitsandbytes.cextension",
    "bitsandbytes.autograd",
    "accelerate",
    "accelerate.utils",
    "tqdm",
    "tqdm.auto",
    "tqdm.notebook",
    "huggingface_hub.constants",
    "huggingface_hub.file_download",
    "huggingface_hub.utils.logging",
    "importlib_metadata",
    "tokenizers",
    "safetensors",
    "safetensors.torch",
    # non serve qui torch (escluso runtime), ma i meta-data sono richiesti da HF
]

# File di dati Gradio (CSS/JS/templates dentro al wheel)
datas = []
datas += collect_data_files("gradio")
datas += collect_data_files("gradio_client")

# Metadata per bitsandbytes, accelerate, transformers (richiesti da HF Hub)
datas += copy_metadata("transformers")
datas += copy_metadata("huggingface-hub")
datas += copy_metadata("tokenizers")
datas += copy_metadata("safetensors")

# Aggiungi tutti i sub-moduli del package locale
hiddenimports = list(HIDDENIMPORTS) + collect_submodules("relictoepub")
