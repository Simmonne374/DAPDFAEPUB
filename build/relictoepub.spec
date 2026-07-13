# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec per l'app principale RelicToEpub.

Produce due eseguibili nella stessa cartella ``_internal``:

* ``RelicToEpubUI.exe``  — lancia ``launch_ui_launcher.py`` (Gradio)
* ``RelicToEpubCLI.exe`` — lancia ``launch_cli_launcher.py`` (convert_one)

PyTorch NON è bundle qui (lo installa il bootstrap GPU-aware a runtime).
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(SPEC)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(SPEC)), os.pardir, "src"))

# Import path entry point per garantire che i moduli siano visibili a PyInstaller
from relictoepub.ui.components import check_model_status  # noqa: F401


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), os.pardir))
BUILD_DIR = os.path.dirname(os.path.abspath(SPEC))

# Entry points separati: UI e CLI
UI_ENTRY = os.path.join(BUILD_DIR, "launchers", "launch_ui_launcher.py")
CLI_ENTRY = os.path.join(BUILD_DIR, "launchers", "launch_cli_launcher.py")
ICON_PATH = os.path.join(BUILD_DIR, "icon.ico")
HOOKS_PATH = os.path.join(BUILD_DIR, "hooks")

# Se l'icona non esiste ancora, la creiamo a runtime
if not os.path.exists(ICON_PATH):
    ICON_PATH = None


# Hidden imports comuni a entrambi gli exe
COMMON_HIDDENIMPORTS = (
    [
        "relictoepub",
        "relictoepub.ui",
        "relictoepub.ui.components",
        "relictoepub.ui.gradio_app",
        "relictoepub.pipeline",
        "relictoepub.ingest",
        "relictoepub.inference.unlimited_ocr",
        "relictoepub.inference.config",
        "relictoepub.postprocess.bbox_crop",
        "relictoepub.postprocess.text_clean",
        "relictoepub.postprocess.webp_optim",
        "relictoepub.compile.build_epub",
        "relictoepub.compile.eink_css",
        "bitsandbytes",
        "bitsandbytes.cextension",
        "bitsandbytes.autograd",
        "accelerate",
        "accelerate.utils",
        "transformers",
        "huggingface_hub",
        "huggingface_hub.utils",
        "huggingface_hub.file_download",
        "tqdm",
        "tqdm.auto",
        "tokenizers",
        "safetensors",
        "safetensors.torch",
        "importlib_metadata",
        "pypandoc",
        "pypandoc.pandoc_download",
        "ebooklib",
        "ebooklib.epub",
        # Gradio runtime
                "gradio",
                "gradio.themes",
                "gradio.themes.utils",
                # Sub-dipendenze di gradio che PyInstaller non raccoglie perché non sono
                # importate direttamente; servono perché gradio_blocks importa groovy
                # e altri in modo dinamico.
                "groovy",
                "safehttpx",
                "semantic_version",
                "ffmpy",
                "orjson",
                "aiohttp",
            ]
            + collect_submodules("gradio")
            + collect_submodules("transformers")
            + collect_submodules("huggingface_hub")
            + collect_submodules("safehttpx")
            + collect_submodules("groovy")
        )

# Datas: file CSS/JS di Gradio + metadata HF
import gradio as _gr  # type: ignore  # noqa: E402
gr_path = os.path.dirname(_gr.__file__)

import huggingface_hub as _hfh  # type: ignore  # noqa: E402
hf_path = os.path.dirname(_hfh.__file__)

import transformers as _t  # type: ignore  # noqa: E402
t_path = os.path.dirname(_t.__file__)

datas = [
    # Gradio: bundled CSS/JS templates
    (gr_path, "gradio"),
    # HF hub: include init.py
    (hf_path, "huggingface_hub"),
    # Transformers: config templates e tokenizer
    (t_path, "transformers"),
    # Pacchetti con version.txt usato a runtime da gradio
    *collect_data_files("safehttpx"),
    *collect_data_files("groovy"),
    # Anche altri pacchetti con data files non-MODULE
    *collect_data_files("gradio_client"),
    *collect_data_files("gradio"),
]


# PyInstaller supports single spec with multiple EXE using --onedir by
# providing them all in the same Analysis. However we need two slightly
# different Analyses (different entry-points). Workaround: build two COLLECT
# targets sharing the same _internal assets.

# ----- UI Analysis -----
a_ui = Analysis(
    [UI_ENTRY],
    pathex=[
        BUILD_DIR,
        PROJECT_ROOT,
        os.path.join(PROJECT_ROOT, "src"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=COMMON_HIDDENIMPORTS,
    hookspath=[HOOKS_PATH],
    runtime_hooks=[],
    excludes=[
        "tkinter",  # UI non usa tkinter
        # torch NON bundlato: installato runtime dal bootstrap GPU-aware.
        # PyInstaller include torch per default se presente in deps; lo
        # escludiamo esplicitamente per non duplicare 300+ MB.
        "torch",
        "torchvision",
        "torch.nn",
        "torch.cuda",
        "torch.utils",
        "torch.autograd",
        "torch.optim",
        "torch.distributed",
        "torch.jit",
        "torch.onnx",
    ],
    noarchive=False,
    optimize=0,
)
pyz_ui = PYZ(a_ui.pure)

exe_ui = EXE(
    pyz_ui,
    a_ui.scripts,
    [],
    exclude_binaries=True,
    name="RelicToEpubUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # UI Gradio: GUI subsystem così facendo doppio-click non apre terminale
    console=False,
    icon=ICON_PATH,
    disable_windowed_traceback=False,
)

# ----- CLI launcher (se esiste, altrimenti fallback a convert_one.py) -----
CLI_FALLBACK = os.path.join(PROJECT_ROOT, "scripts", "convert_one.py")
if not os.path.exists(CLI_ENTRY):
    CLI_ENTRY = CLI_FALLBACK

a_cli = Analysis(
    [CLI_ENTRY],
    pathex=[
        BUILD_DIR,
        PROJECT_ROOT,
        os.path.join(PROJECT_ROOT, "src"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=COMMON_HIDDENIMPORTS,
    hookspath=[HOOKS_PATH],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        # torch NON bundlato: installato runtime dal bootstrap GPU-aware
        "torch",
        "torchvision",
        "torch.nn",
        "torch.cuda",
        "torch.utils",
        "torch.autograd",
        "torch.optim",
        "torch.distributed",
        "torch.jit",
        "torch.onnx",
    ],
    noarchive=False,
    optimize=0,
)
pyz_cli = PYZ(a_cli.pure)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="RelicToEpubCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # CLI: deve mostrare output testuale
    icon=ICON_PATH,
    disable_windowed_traceback=False,
)

# COLLECT: singola cartella condivisa. Entrambi gli exe puntano agli stessi
# asset in _internal via COLLECT (ma PyInstaller non lo permette nativamente
# per più Analysis separate; quindi creiamo due COLLECT, e lo script di build
# si occuperà di merge delle _internal).
coll = COLLECT(
    exe_ui,
    a_ui.binaries,
    a_ui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    name="RelicToEpub",
)
