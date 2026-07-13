"""RelicToEpub — PDF-to-EPUB3 pipeline powered by Baidu Unlimited-OCR."""

# Import espliciti di sub-dipendenze di Gradio che PyInstaller non riesce a
# raccogliere tramite l'analisi statica perché Gradio le importa in modo
# dinamico/lazy. Senza questi import, ``import groovy`` fallisce a runtime
# perché il bytecode non finisce nel PYZ di PyInstaller.
import groovy  # noqa: F401  # type: ignore
import safehttpx  # noqa: F401  # type: ignore

__version__ = "0.1.0"

__all__ = ["__version__"]
