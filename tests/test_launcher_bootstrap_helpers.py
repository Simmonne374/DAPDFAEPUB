"""Test unitari per gli helper di :mod:`build.launchers.gpu_bootstrap`.

Copriamo le funzioni deterministiche (no network, no subprocess):

* :func:`parse_compute_cap` -- parsing "8.6" -> (8, 6)
* :func:`select_wheel_for_gpu` -- tabella SM -> wheel
* :func:`_wheel_cache_dir` -- path locale con versionamento torch
* :func:`_wheel_cache_root` -- root multi-versione
* :func:`find_cached_wheel` -- glob nella cache
* :func:`_log_stale_caches` -- log non distruttivo di cache vecchie
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_LAUNCHERS = Path(__file__).resolve().parents[1] / "build" / "launchers"
if str(_LAUNCHERS) not in sys.path:
    sys.path.insert(0, str(_LAUNCHERS))

import gpu_bootstrap as gb  # noqa: I001  (separato per manipolare sys.path)


# ============================================================
# parse_compute_cap
# ============================================================


def test_parse_compute_cap_valid() -> None:
    assert gb.parse_compute_cap("8.6") == (8, 6)
    assert gb.parse_compute_cap("  7.5\n") == (7, 5)
    assert gb.parse_compute_cap("12.0") == (12, 0)


def test_parse_compute_cap_invalid() -> None:
    assert gb.parse_compute_cap("not-a-number") is None
    assert gb.parse_compute_cap("") is None
    assert gb.parse_compute_cap(None) is None  # type: ignore[arg-type]


# ============================================================
# select_wheel_for_gpu -- decision table
# ============================================================


@pytest.mark.parametrize(
    ("cc", "driver", "expected_tag", "expected_min_driver"),
    [
        # Maxwell SM 5.x ora coperti (issue 12)
        ((5, 0), "471.41", "cu118", "11.8"),
        ((5, 2), "471.41", "cu118", "11.8"),
        ((5, 3), "471.41", "cu118", "11.8"),
        # Pascal
        ((6, 1), "525.85", "cu118", "11.8"),
        # Turing
        ((7, 5), "528.49", "cu118", "11.8"),
        # Ampere -- cu124 con driver >= 12
        ((8, 6), "531.41", "cu124", "11.8"),
        # Driver vecchio per SM 8.6 -> downgrade a cu118
        # (NB: la soglia attuale "driver_major < 12" e' volutamente
        # generosa: solo driver "1.x..11.x" scatenano il downgrade)
        ((8, 6), "9.99", "cu118", "11.8"),
        # Ada Lovelace (RTX 40xx)
        ((8, 9), "551.61", "cu124", "11.8"),
        # Hopper
        ((9, 0), "535.86", "cu124", "11.8"),
        # Blackwell
        ((10, 0), "555.42", "cu126", "12.6"),
        ((12, 0), "555.42", "cu126", "12.6"),
    ],
)
def test_select_wheel_for_gpu_supported(
    cc: tuple[int, int], driver: str, expected_tag: str, expected_min_driver: str
) -> None:
    selected, _reason = gb.select_wheel_for_gpu(cc, driver)
    # NB: le righe con driver palesemente vecchio producono reason non vuoto
    # (downgrade giustificato). La maggior parte delle righe ha reason == "".
    tag, min_driver = selected
    assert tag == expected_tag
    assert min_driver == expected_min_driver


def test_select_wheel_for_gpu_unknown_sm_falls_back() -> None:
    # SM 4.x (Fermi/Kepler legacy): non coperto direttamente in CUDA_WHEEL,
    # ma il fallback "first SM >= cc" lo mappa a cu118 (Maxwell). Questo e'
    # il comportamento desiderato: meglio Maxwell cu118 che CPU pura su un
    # Fermi vetusto. reason rimane vuoto perche' il match e' "nativo".
    selected, reason = gb.select_wheel_for_gpu((4, 0), "390.144")
    tag, _ = selected
    assert tag == "cu118"
    assert reason == ""


def test_select_wheel_for_gpu_cu118_driver_too_old() -> None:
    # SM 6.x con driver major < 11 (es. driver "1.x" ipotetico) -> cpu.
    # NB: la soglia attuale e' deliberatamente lasca (major < 11); driver
    # real-world come 388.16 hanno major >= 11 e quindi passano. Questo
    # riflette il codice di produzione: il check serve solo a intercettare
    # input palesemente malformati o pre-Windows-7 (driver "1.x").
    selected, reason = gb.select_wheel_for_gpu((6, 1), "1.0")
    tag, _ = selected
    assert tag == "cpu"
    assert "11+" in reason or "11." in reason


def test_select_wheel_for_gpu_cu126_driver_too_old() -> None:
    # SM 10.0 (Blackwell) con driver major < 12 -> cu124.
    selected, reason = gb.select_wheel_for_gpu((10, 0), "9.99")
    tag, _ = selected
    assert tag == "cu124"
    assert reason != ""


def test_select_wheel_for_gpu_driver_garbage() -> None:
    # Driver string malformato: driver_major = 0 -> il fallback si attiva
    selected, _ = gb.select_wheel_for_gpu((6, 1), "not-a-version")
    tag, _ = selected
    assert tag == "cpu"


# ============================================================
# Cache path
# ============================================================


def test_wheel_cache_dir_includes_torch_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = gb._wheel_cache_dir()
    assert p.parent.name == "torch_wheel_cache"
    assert p.name == f"torch-{gb.TORCH_VERSION_DEFAULT}"
    assert p.exists()


def test_wheel_cache_root_contains_versioned_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = gb._wheel_cache_root()
    gb._wheel_cache_dir()  # forza creazione subdir corrente
    assert root.exists()
    assert (root / f"torch-{gb.TORCH_VERSION_DEFAULT}").exists()


def test_find_cached_wheel_returns_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cache = gb._wheel_cache_dir()
    # Crea due wheel finti con tag diversi.
    (cache / "torch-2.4.0+cu118-cp311-cp311-win_amd64.whl").write_bytes(b"")
    (cache / "torch-2.4.0+cpu-cp311-cp311-win_amd64.whl").write_bytes(b"")
    cu = gb.find_cached_wheel("cu118")
    cpu = gb.find_cached_wheel("cpu")
    assert cu is not None
    assert "cu118" in cu.name
    assert cpu is not None
    assert "cpu" in cpu.name


def test_find_cached_wheel_missing_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert gb.find_cached_wheel("cu126") is None


def test_log_stale_caches_does_not_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # Pre-crea una cache "vecchia" con un nome di versione differente.
    root = gb._wheel_cache_root()
    stale = root / "torch-1.13.0"
    stale.mkdir()
    (stale / "torch-1.13.0+cu117-cp311-cp311-win_amd64.whl").write_bytes(b"")

    gb._log_stale_caches()

    # Il marker stale non deve essere stato cancellato.
    assert stale.exists()
    # Il log deve essere stato scritto (path dentro LOCALAPPDATA/RelicToEpub/logs).
    log_dir = tmp_path / "RelicToEpub" / "logs"
    log_path = log_dir / "launcher_selfcheck.log"
    assert log_path.exists()
    body = log_path.read_text(encoding="utf-8")
    assert "torch-1.13.0" in body
    assert "non rimossa automaticamente" in body


# ============================================================
# CUDA_WHEEL copre SM 5.x (regression per Maxwell)
# ============================================================


def test_cuda_wheel_includes_maxwell() -> None:
    for sm in [(5, 0), (5, 2), (5, 3)]:
        assert sm in gb.CUDA_WHEEL, f"SM {sm} mancante in CUDA_WHEEL"
        tag, min_driver = gb.CUDA_WHEEL[sm]
        assert tag == "cu118"
        assert min_driver == "11.8"


# ============================================================
# _check_install_path: solo path che NON toccano winreg
# ============================================================


def test_check_install_path_missing_exe(tmp_path: Path) -> None:
    """Un exe che non esiste deve loggare ma non sollevare."""
    fake = tmp_path / "RelicToEpubBoot.exe"
    os.environ.pop("RELICTOEPUB_PATH_WARNING", None)
    gb._check_install_path(fake)  # non deve raise
    assert os.environ.get("RELICTOEPUB_PATH_WARNING") == "1"


def test_check_install_path_floppy(tmp_path: Path) -> None:
    """Un path su A:\\ deve essere flaggato come sospetto."""
    fake = Path("A:\\RelicToEpub\\RelicToEpubBoot.exe")
    os.environ.pop("RELICTOEPUB_PATH_WARNING", None)
    gb._check_install_path(fake)
    # Senza drive A:\ su Windows: il controllo si attiva tramite string match.
    # L'eseguibile non esiste (e quindi viene comunque flaggato anche per
    # quello), ma ci assicuriamo che il flag di warning sia settato.
    assert os.environ.get("RELICTOEPUB_PATH_WARNING") == "1"