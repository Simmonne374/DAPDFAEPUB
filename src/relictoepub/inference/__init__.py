"""Inference module — Unlimited-OCR wrapper around HF Transformers."""

from relictoepub.inference.config import InferenceConfig
from relictoepub.inference.unlimited_ocr import UnlimitedOCRRunner

__all__ = ["InferenceConfig", "UnlimitedOCRRunner"]
