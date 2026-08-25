"""Regression tests per bug B30 (issue #30).

Bug originale:
    ``InferenceConfig.resolve_device()`` maschera tutti gli errori torch
    come "no CUDA": quando torch è installato ma il runtime CUDA è rotto
    (driver mancante, libreria libcuda.so non raggiungibile, device
    inizializzato male, OOM al probe), ``torch.cuda.is_available()`` o
    ``torch.cuda.mem_get_info()`` possono sollevare eccezioni non
    ``ImportError`` che non vengono gestite.

Aspettativa (post-fix):
    * Tutti gli errori CUDA devono essere catturati e portare al fallback
      CPU in modo deterministico.
    * Il fallback deve essere loggato a livello ``WARNING`` con la
      categoria di errore (ImportError, RuntimeError, OSError, ...),
      così l'utente può diagnosticare il problema.
    * La firma del metodo non cambia: ritorna ancora ``"cuda"`` o ``"cpu"``.
"""

from __future__ import annotations

import logging
import sys

import pytest

from relictoepub.inference.config import InferenceConfig


class _FakeCuda:
    """Mock minimale del modulo ``torch.cuda`` con failure iniettabile."""

    def __init__(self, *, available: bool, mem_info_error: Exception | None = None) -> None:
        self._available = available
        self._mem_info_error = mem_info_error

    def is_available(self) -> bool:
            return self._available

    def mem_get_info(self) -> tuple[int, int]:
        if self._mem_info_error is not None:
            raise self._mem_info_error
        # 24 GB liberi, 0 usati — sopra la soglia di 8 GB
        return (24 * (1024**3), 0)


class _FakeTorch:
    """Mock minimale del modulo ``torch`` con cuda iniettabile."""

    def __init__(self, cuda: _FakeCuda) -> None:
        self.cuda = cuda


def _install_fake_torch(monkeypatch: pytest.MonkeyPatch, torch: _FakeTorch) -> None:
    """Installa (o rimuove) il modulo ``torch`` dal ``sys.modules``."""
    monkeypatch.setitem(sys.modules, "torch", torch)


def test_resolve_device_falls_back_to_cpu_when_torch_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ImportError`` su torch → CPU senza eccezione."""
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    cfg = InferenceConfig(device="auto")
    assert cfg.resolve_device() == "cpu"


def test_resolve_device_falls_back_to_cpu_when_cuda_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA non disponibile → CPU."""
    _install_fake_torch(monkeypatch, _FakeTorch(_FakeCuda(available=False)))
    cfg = InferenceConfig(device="auto")
    assert cfg.resolve_device() == "cpu"


def test_resolve_device_returns_cuda_when_memory_sufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA disponibile con VRAM sufficiente → ``"cuda"``."""
    _install_fake_torch(monkeypatch, _FakeTorch(_FakeCuda(available=True)))
    cfg = InferenceConfig(device="auto", min_gpu_memory_gb=8.0)
    assert cfg.resolve_device() == "cuda"


def test_resolve_device_falls_back_when_memory_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA disponibile ma VRAM insufficiente → CPU."""
    cuda = _FakeCuda(available=True)
    # 4 GB liberi, sotto soglia di 8 GB
    cuda.mem_get_info = lambda: (4 * (1024**3), 0)  # type: ignore[assignment]
    _install_fake_torch(monkeypatch, _FakeTorch(cuda))
    cfg = InferenceConfig(device="auto", min_gpu_memory_gb=8.0)
    assert cfg.resolve_device() == "cpu"


def test_resolve_device_catches_runtime_error_from_is_available(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``RuntimeError`` da ``torch.cuda.is_available()`` → CPU + log WARNING.

    Riproduce il caso in cui i driver NVIDIA sono installati ma
    la libreria ``libcuda`` non è raggiungibile (es. installazione
    corrotta, container senza mount del device).
    """
    class _BrokenCuda(_FakeCuda):
        def is_available(self) -> bool:
            raise RuntimeError("libcuda.so.1: cannot open shared object file")

    _install_fake_torch(monkeypatch, _FakeTorch(_BrokenCuda(available=True)))
    cfg = InferenceConfig(device="auto")

    with caplog.at_level(logging.WARNING, logger="relictoepub.inference.config"):
        device = cfg.resolve_device()

    assert device == "cpu"
    assert any("libcuda" in rec.message for rec in caplog.records), (
        "Il fallback deve loggare la causa del fallimento CUDA"
    )


def test_resolve_device_catches_runtime_error_from_mem_get_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``RuntimeError`` da ``torch.cuda.mem_get_info()`` → CPU + log WARNING.

    Riproduce il caso in cui ``is_available()`` ritorna ``True`` ma il
    device context non è inizializzato correttamente (es. GPU in
    power-saving state, ECC error, OOM al probe).
    """
    cuda = _FakeCuda(
        available=True,
        mem_info_error=RuntimeError("CUDA error: out of memory (initial probe)"),
    )
    _install_fake_torch(monkeypatch, _FakeTorch(cuda))
    cfg = InferenceConfig(device="auto")

    with caplog.at_level(logging.WARNING, logger="relictoepub.inference.config"):
        device = cfg.resolve_device()

    assert device == "cpu"
    assert any("out of memory" in rec.message for rec in caplog.records)


def test_resolve_device_catches_oserror_from_mem_get_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``OSError`` (es. NVML broken) da ``mem_get_info`` → CPU."""
    cuda = _FakeCuda(
        available=True,
        mem_info_error=OSError("NVML: GPU lost"),
    )
    _install_fake_torch(monkeypatch, _FakeTorch(cuda))
    cfg = InferenceConfig(device="auto")

    with caplog.at_level(logging.WARNING, logger="relictoepub.inference.config"):
        device = cfg.resolve_device()

    assert device == "cpu"
    assert any("NVML" in rec.message for rec in caplog.records)


def test_resolve_device_explicit_device_returns_value_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``device != "auto"`` non consulta torch e ritorna il valore."""
    # Anche con torch assente, device="cuda" deve essere restituito
    # tale e quale: è una scelta esplicita dell'utente.
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    cfg = InferenceConfig(device="cuda")
    assert cfg.resolve_device() == "cuda"


def test_resolve_device_signature_unchanged() -> None:
    """Contratto pubblico: ritorna sempre ``str`` fra ``{"cuda", "cpu"}``."""
    cfg = InferenceConfig()
    result = cfg.resolve_device()
    assert isinstance(result, str)
    assert result in ("cuda", "cpu")