"""Test per ``relictoepub.inference.config``.

Non carica il modello reale (che richiede GPU + download GB);
verifica solo la configurazione e la logica di selezione device/quant.
"""

from __future__ import annotations

from relictoepub.inference.config import InferenceConfig, QuantizationMode


def test_quantization_modes() -> None:
    assert QuantizationMode("int4").value == "int4"
    assert QuantizationMode("int8").value == "int8"
    assert QuantizationMode("none").value == "none"


def test_inference_config_defaults() -> None:
    """I default devono corrispondere a quelli documentati nel piano."""
    cfg = InferenceConfig()
    assert cfg.model_id == "baidu/Unlimited-OCR"
    assert cfg.pages_per_batch == 20
    assert cfg.image_size == 1024
    assert cfg.base_size == 1024
    assert cfg.ngram_no_repeat_size == 35
    assert cfg.ngram_window_multi == 1024
    assert cfg.skip_special_tokens is False
    assert cfg.max_new_tokens == 8192


def test_inference_config_quantization_default() -> None:
    cfg = InferenceConfig()
    assert cfg.quantization == QuantizationMode.INT4


def test_inference_config_resolve_device_returns_string() -> None:
    """``resolve_device`` ritorna sempre ``"cuda"`` o ``"cpu"``."""
    cfg = InferenceConfig()
    device = cfg.resolve_device()
    assert device in ("cuda", "cpu")


def test_inference_config_passes_pages_through() -> None:
    cfg = InferenceConfig(pages_per_batch=12)
    assert cfg.pages_per_batch == 12


def test_inference_config_to_dict_serializable() -> None:
    """La config deve essere serializzabile (es. per logging)."""
    cfg = InferenceConfig()
    d = cfg.to_dict()
    assert d["model_id"] == "baidu/Unlimited-OCR"
    assert d["quantization"] == "int4"
    assert d["pages_per_batch"] == 20