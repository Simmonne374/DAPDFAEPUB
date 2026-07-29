"""RelicToEpub — PDF-to-EPUB3 pipeline powered by Baidu Unlimited-OCR."""

# Import espliciti di sub-dipendenze di Gradio che PyInstaller non riesce a
# raccogliere tramite l'analisi statica perché Gradio le importa in modo
# dinamico/lazy. Senza questi import, ``import groovy`` fallisce a runtime
# perché il bytecode non finisce nel PYZ di PyInstaller.
import groovy  # type: ignore # noqa: F401
import safehttpx  # type: ignore # noqa: F401

# Versione single-source-of-truth: legge da pyproject.toml via importlib.metadata
# in modo che launcher, installer e Python package restino sempre allineati.
# In ambienti "editable" (pip install -e .) il valore coincide con la stringa
# dentro pyproject.toml; in ambienti dove il pacchetto non e installato
# (es. PyInstaller bundle "onedir" che non include i metadata) importlib.metadata
# potrebbe non trovarlo, quindi lasciamo un fallback hardcoded come ultima rete
# di sicurezza. Il bootstrap / installer sovrascrive la stringa a build time
# con quella di pyproject.toml (vedi build/build_windows.ps1).
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("relictoepub")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
except ImportError:  # pragma: no cover - importlib.metadata sempre disponibile in Py3.10+
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
