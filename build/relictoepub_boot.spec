# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec per il bootstrap GPU-aware (``RelicToEpubBoot.exe``).

Questo exe è piccolo (~30 MB): include solo pynvml + requests per il
rilevamento GPU e il download del wheel torch. PyTorch NON è incluso
(verrà scaricato runtime). tkinter NON è incluso (lo splash partirà come
subprocess separato solo se gpu_splash.py viene lanciato a sua volta).
"""

from PyInstaller.utils.hooks import collect_submodules

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(SPEC)))
# Importiamo progress_state per assicurarci sia nella path di PyInstaller
from launchers.progress_state import ProgressState  # noqa: F401


hiddenimports = (
    [
        "progress_state",
        "gpu_bootstrap",
        "pynvml",
        "requests",
    ]
    + collect_submodules("pynvml")
    + collect_submodules("requests")
)


a = Analysis(
    ["launchers/gpu_bootstrap.py"],
    pathex=[os.path.dirname(os.path.abspath(SPEC))],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(os.path.dirname(os.path.abspath(SPEC)), "hooks")],
    runtime_hooks=[],
    excludes=[
        # Escludi esplicitamente i pacchetti pesanti — qui non servono
        "torch", "torchvision", "torchaudio",
        "transformers", "accelerate", "bitsandbytes",
        "gradio", "pymupdf", "fitz",
        "PIL", "Pillow",
        "numpy",
        # Escludi tkinter: il boot NON lancia splash direttamente.
        # Se serve lo splash, l'app principale lo farà in un subprocess.
        "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.font",
        "tkinter.messagebox", "tkinter.scrolledtext", "tkinter.dialog",
        "tkinter.colorchooser", "tkinter.commondialog", "tkinter.dnd",
        "tkinter.tix", "tkinter.__main__",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Singolo executable: sarà il point-of-entry.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir
    name="RelicToEpubBoot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX rompe spesso i runtime di pynvml
    console=True,  # console visibile per mostrare errori tecnici
    icon=os.path.join(os.path.dirname(os.path.abspath(SPEC)), "icon.ico") if os.path.exists(
        os.path.join(os.path.dirname(os.path.abspath(SPEC)), "icon.ico")
    ) else None,
    disable_windowed_traceback=False,
    target_arch=None,  # arch corrente
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="boot",
)
