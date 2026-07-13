"""Bootstrap GPU-aware per l'installer Windows di RelicToEpub.

Questo script è il point-of-entry "invisibile" per gli eseguibili UI e CLI
prodotti da PyInstaller. Il flusso è:

1. Rileva se l'utente ha una GPU NVIDIA via ``nvidia-smi`` + ``pynvml``.
2. Se nessuna GPU (o driver assente): salta bootstrap e lancia direttamente
   l'applicazione principale (verrà usato PyTorch CPU-only).
3. Se GPU presente: determina la build CUDA corretta dalla compute capability:
   SM 6.x / 7.x → CUDA 11.8, SM 8.x / 9.x → CUDA 12.4,
   SM 10.x / 12.x (Blackwell) → CUDA 12.6, fallback CPU se CUDA driver < 11.8.
4. Scarica il wheel ``torch`` con download stream per mostrare progress
   granulare via :class:`ProgressState`.
5. Installazione nel ``sys.executable``-embedded.
6. Setta env var ``RELICTOEPUB_BOOT_OK=1`` e rilancia l'app vera.

Tutte le fasi aggiornano lo stato condiviso, in modo che lo splash Tkinter
mostri sempre cosa sta succedendo (mai "sospeso" >2 s).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Quando questo script è bundlato da PyInstaller, le dipendenze di launcher
# (tkinter non serve qui) sono già disponibili via sys.path.
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

try:
    from progress_state import ProgressState  # type: ignore
except ImportError:
    # Quando bundlato, progress_state.py è allo stesso livello; in dev può
    # essere importato come ``build.launchers.progress_state``. Proviamo entrambi.
    try:
        from build.launchers.progress_state import ProgressState  # type: ignore
    except ImportError:
        raise SystemExit(
            "Errore interno: modulo progress_state.py non trovato accanto a gpu_bootstrap.py"
        )


# ----- Mappatura compute capability → wheel torch -----
CUDA_WHEEL = {
    # SM major.minor (da "nvidia-smi --query-gpu=compute_cap") → (wheel index URL, versione minima driver)
    (6, 0): ("cu118", "11.8"),  # Pascal P100
    (6, 1): ("cu118", "11.8"),  # GTX 10xx (1080 Ti)
    (6, 2): ("cu118", "11.8"),  # Titan Xp
    (7, 0): ("cu118", "11.8"),  # Volta V100
    (7, 5): ("cu118", "11.8"),  # Turing GTX 16xx, RTX 20xx
    (8, 0): ("cu124", "11.8"),  # Ampere A100
    (8, 6): ("cu124", "11.8"),  # Ampere RTX 30xx
    (8, 9): ("cu124", "11.8"),  # Ada Lovelace RTX 40xx
    (9, 0): ("cu124", "11.8"),  # Hopper H100
    (10, 0): ("cu126", "12.6"),  # Blackwell B100
    (12, 0): ("cu126", "12.6"),  # Blackwell consumer (RTX 50xx)
}

WHEEL_BASE = "https://download.pytorch.org/whl"
TORCH_VERSION_DEFAULT = "2.4.0"


# ============================================================
# GPU detection
# ============================================================


def parse_compute_cap(text: str) -> Optional[tuple[int, int]]:
    """Parse ``nvidia-smi --query-gpu=compute_cap`` (es. "8.6") → (8, 6)."""
    try:
        major, minor = text.strip().split(".")
        return int(major), int(minor)
    except (ValueError, AttributeError):
        return None


def get_gpu_info_via_smi() -> Optional[dict]:
    """Ritorna info GPU via ``nvidia-smi`` + ``pynvml``, o ``None`` se non disponibile."""
    info: dict = {}

    # 1) nvidia-smi query rapida (più affidabile su driver recenti)
    smi_query = (
        "nvidia-smi --query-gpu=name,compute_cap,driver_version "
        "--format=csv,noheader,nounits"
    )
    try:
        result = subprocess.run(
            smi_query, shell=True, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            first = result.stdout.strip().splitlines()[0].split(",")
            if len(first) >= 3:
                info["name"] = first[0].strip()
                info["compute_cap"] = parse_compute_cap(first[1])
                info["driver_version"] = first[2].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # 2) pynvml / nvidia-ml-py per arricchire (cuDNN, memorie, ecc.)
        try:
            # Il package su PyPI è "nvidia-ml-py3" (rinominato da "pynvml") ma
            # esporta lo stesso modulo Python `pynvml` per retrocompatibilità.
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            if "name" not in info:
                info["name"] = pynvml.nvmlDeviceGetName(handle).decode("utf-8", errors="replace")
            if "driver_version" not in info:
                info["driver_version"] = pynvml.nvmlSystemGetDriverVersion().decode(
                    "utf-8", errors="replace"
                )
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            info["memory_total_mb"] = int(mem.total / (1024 * 1024))
            pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass

    return info if info.get("compute_cap") else None


def select_wheel_for_gpu(compute_cap: tuple[int, int], driver_str: str) -> tuple[str, str]:
    """Sceglie wheel torch + versione CUDA driver minima richiesta."""
    # Trova la chiave più vicina (potrebbe essere SM non esatto in lista)
    selected = CUDA_WHEEL.get(compute_cap)
    if selected is None:
        # Fallback: prima match ≥ SM
        candidates = sorted([k for k in CUDA_WHEEL if k[0] >= compute_cap[0]])
        if candidates:
            selected = CUDA_WHEEL[candidates[0]]
        else:
            selected = ("cpu", "")
    cuda_tag, min_driver = selected

    # Controlla versione driver
    try:
        driver_parts = driver_str.split(".")
        driver_major = int(driver_parts[0])
    except (ValueError, AttributeError):
        driver_major = 0

    if cuda_tag == "cu118" and driver_major < 11:
        return ("cpu", "0"), "driver CUDA 11+ mancante"
    if cuda_tag == "cu124" and driver_major < 12:
        # CUDA 12.4 richiede driver ≥ 530; scendiamo a cu118
        return ("cu118", "11.8"), f"driver CUDA {driver_str} troppo vecchio per cu124"
    if cuda_tag == "cu126" and driver_major < 12:
        return ("cu124", "12.4"), f"driver CUDA {driver_str} non supporta cu126"

    return selected, ""


# ============================================================
# Wheel download con progress
# ============================================================


def download_with_progress(url: str, dest: Path, state: ProgressState, *, timeout: int = 30) -> bool:
    """Scarica un file mostrando progresso in ``state``.

    Strategia:
    * **HTTP Range**: se ``dest`` esiste già, scarica solo i byte mancanti.
    * **Retry con backoff**: in caso di errore di rete, riprova fino a 5 volte.
    * **Progress granulare**: aggiorna ``state`` ad ogni chunk ricevuto.
    * **Stall detection**: se la velocità resta < 100 KB/s per più di 30s,
      considera lo scaricamento bloccato e rilancia.

    Returns True al successo, False in caso di errore persistente.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        state.error("Modulo requests non disponibile nel bootstrap")
        return False

    chunk_size = 1024 * 256  # 256 KB
    max_retries = 5
    retry_delay = 3.0  # secondi
    stall_timeout = 60.0  # secondi senza progresso → considerare bloccato

    downloaded = dest.stat().st_size if dest.exists() else 0

    # Verifica preliminare della dimensione attesa via HEAD request
    expected_total = 0
    try:
        head = requests.head(url, timeout=timeout, allow_redirects=True)
        if head.status_code == 200:
            cl = head.headers.get("content-length")
            if cl:
                expected_total = int(cl)
    except Exception:
        pass

    # Se il file locale è già completo (>= dimensione attesa), salta download
    if expected_total > 0 and downloaded >= expected_total:
        state.set_phase(
            "download_wheel",
            message=f"Wheel già completo in cache ({downloaded // (1024*1024)} MB)",
        )
        # Marca come "downloaded" al 100% per la fase successiva
        state.update_download(
            downloaded_bytes=expected_total,
            total_bytes=expected_total,
            speed_bps=0.0,
            eta_seconds=0.0,
        )
        return True

    total = 0
    attempt = 0

    while attempt <= max_retries:
        attempt += 1
        try:
            headers = {}
            mode = "ab" if downloaded > 0 else "wb"
            if downloaded > 0:
                headers["Range"] = f"bytes={downloaded}-"
                state.set_phase(
                    "download_wheel",
                    message=f"Ripristino download da {downloaded // (1024*1024)} MB…",
                )

            with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
                r.raise_for_status()
                if r.status_code == 200:
                    # Server ha inviato il file intero, ricominciamo
                    if downloaded > 0:
                        # il server non supporta range: ripartiamo
                        downloaded = 0
                        mode = "wb"
                    content_length = r.headers.get("content-length")
                    total = int(content_length) if content_length else 0
                elif r.status_code == 206:
                    # Partial content: aggiungiamo al file esistente
                    content_range = r.headers.get("content-range", "")
                    # Esempio: "bytes 180879360-2692520301/2692520302"
                    if "/" in content_range:
                        try:
                            total = int(content_range.split("/")[-1])
                        except ValueError:
                            total = 0
                else:
                    r.raise_for_status()

                started = time.time()
                last_chunk_time = time.time()
                last_logged = downloaded
                last_log_time = time.time()

                with dest.open(mode) as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                                        # Watchdog stall: se non arrivano chunk da troppo tempo,
                                        # solleva eccezione per riavviare il download con Range.
                                        now = time.time()
                                        if now - last_chunk_time > stall_timeout:
                                            raise requests.exceptions.Timeout(
                                                f"Stall: nessun dato ricevuto da {int(now - last_chunk_time)}s"
                                            )
                                        if not chunk:
                                            # Keep-alive vuoto: continua ma aggiorna timestamp
                                            continue
                                        f.write(chunk)
                                        downloaded += len(chunk)
                                        last_chunk_time = time.time()

                                        # Log progresso ~ogni 500ms
                                        now = time.time()
                                        if now - last_log_time >= 0.5:
                                            elapsed = max(0.001, now - started)
                                            speed = downloaded / elapsed
                                            eta = (total - downloaded) / speed if total and speed > 0 else 0.0
                                            state.update_download(
                                                downloaded_bytes=downloaded,
                                                total_bytes=total,
                                                speed_bps=speed,
                                                eta_seconds=eta,
                                            )
                                            last_log_time = now
                                            last_logged = downloaded

                # Download completato: scrivi stato finale
                elapsed = max(0.001, time.time() - started)
                speed = downloaded / elapsed
                state.update_download(
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed_bps=speed,
                    eta_seconds=0.0,
                )
                return True

        except requests.exceptions.RequestException as exc:
            state.error(f"Download fallito (tentativo {attempt}/{max_retries}): {exc}")
            if attempt > max_retries:
                return False
            time.sleep(retry_delay * attempt)
            continue
        except Exception as exc:  # noqa: BLE001
            state.error(f"Errore download: {exc}")
            return False

    return False


def install_wheel_inline(wheel_path: Path, state: ProgressState) -> bool:
    """Estrae il wheel torch nella directory ``_internal`` dell'app.

    Poiché PyInstaller non fornisce un ``python.exe`` standalone né pip, l'unico
    modo per rendere ``torch`` disponibile all'app bundled è estrarre il wheel
    (che è un file ZIP standard) direttamente in ``_internal/``. PyInstaller
    aggiunge quella directory a ``sys.path`` all'avvio, quindi ``import torch``
    troverà automaticamente i moduli estratti.

    Returns True al successo, False in caso di errore.
    """
    import zipfile

    # Determina la directory target. Quando il boot viene lanciato con
    # ``<ui_exe>`` come argv[1], l'app exe sta accanto al boot, quindi
    # ``app_exe.parent / _internal`` è la cartella giusta.
    app_exe_path = _get_app_exe_path()
    if app_exe_path is None:
        state.error("Impossibile determinare la directory dell'app per estrarre torch")
        return False

    target_dir = app_exe_path.parent / "_internal"
    if not target_dir.exists():
        state.error(f"Directory _internal non trovata: {target_dir}")
        return False

    state.set_phase("install_wheel", message=f"Estrazione torch in {target_dir.name}/…")

    try:
        # Conta entry per progresso granulare
        with zipfile.ZipFile(wheel_path, "r") as zf:
            entries = zf.namelist()
            total = len(entries)
            extracted = 0
            started = time.time()
            last_log = started

            for entry in entries:
                # Path traversal protection: ignora entry che escono da target
                # (wheel malformate potrebbero tentare ../../etc/passwd)
                out_path = (target_dir / entry).resolve()
                if not str(out_path).startswith(str(target_dir.resolve())):
                    continue

                if entry.endswith("/"):
                    out_path.mkdir(parents=True, exist_ok=True)
                    continue

                out_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(entry) as src, out_path.open("wb") as dst:
                    # Copia a blocchi per file grandi (es. .pyd, .so)
                    while True:
                        chunk = src.read(1024 * 256)
                        if not chunk:
                            break
                        dst.write(chunk)

                extracted += 1

                # Log progresso ogni 500ms
                now = time.time()
                if now - last_log >= 0.5:
                    elapsed = max(0.001, now - started)
                    speed = extracted / elapsed
                    eta = (total - extracted) / speed if speed > 0 else 0.0
                    state.set_phase(
                        "install_wheel",
                        message=f"Estrazione torch: {extracted}/{total} file (ETA {int(eta)}s)…",
                    )
                    last_log = now

        # Verifica che torch sia effettivamente disponibile
        if (target_dir / "torch" / "__init__.py").exists():
            state.set_phase(
                "install_wheel",
                message=f"Estrazione completata ({total} file in {int(time.time() - started)}s)",
            )
            return True
        else:
            state.error("Estrazione completata ma modulo torch/__init__.py mancante")
            return False

    except zipfile.BadZipFile:
        state.error(f"Wheel corrotto: {wheel_path}")
        return False
    except Exception as exc:  # noqa: BLE001
        state.error(f"Errore estrazione: {exc}")
        return False


# Cache del path dell'app exe, impostato in main()
_app_exe_path: Optional[Path] = None


def _get_app_exe_path() -> Optional[Path]:
    return _app_exe_path


def _set_app_exe_path(p: Path) -> None:
    global _app_exe_path
    _app_exe_path = p


# ============================================================
# Verifica cache wheel
# ============================================================


def _wheel_cache_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
    p = Path(local) / "RelicToEpub" / "torch_wheel_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_cached_wheel(tag: str) -> Optional[Path]:
    cache = _wheel_cache_dir()
    matches = sorted(cache.glob(f"torch-*-{tag}-*.whl"))
    return matches[0] if matches else None


def download_wheel_for(tag: str, state: ProgressState) -> Optional[Path]:
    """Scarica il wheel torch per il tag (``cu118``, ``cu124``, ``cu126``, ``cpu``).

    Per ``cpu`` usa l'index ``pytorch.org/whl/cpu``; per i CUDA tag usa
    ``pytorch.org/whl/<tag>``.
    """
    cache = _wheel_cache_dir()

    state.set_phase("select_wheel", message=f"Selezione wheel torch per {tag}…")
    if tag == "cpu":
        url_base = f"{WHEEL_BASE}/cpu"
    else:
        url_base = f"{WHEEL_BASE}/{tag}"

    # Proviamo a interrogare il remote index per il filename più recente.
    # Senza BS4 facciamo un fallback: hard-coded per ``torch==2.4.0``.
    # Lista minimale: per ogni tag una versione testata. Se rotto, ``pip``
    # verrà usato come fallback.
    candidate_urls: list[str] = []
    if tag == "cpu":
        candidate_urls.append(f"{url_base}/torch-{TORCH_VERSION_DEFAULT}%2Bcpu-cp311-cp311-win_amd64.whl")
    else:
        candidate_urls.append(
            f"{url_base}/torch-{TORCH_VERSION_DEFAULT}%2B{tag}-cp311-cp311-win_amd64.whl"
        )

    state.set_phase(
        "download_wheel",
        message=f"Download torch wheel per {tag} ({TORCH_VERSION_DEFAULT})…",
    )

    for url in candidate_urls:
        fname = url.split("/")[-1].replace("%2B", "+")
        dest = cache / fname
        state.update_download(0, 0)  # reset counters
        if download_with_progress(url, dest, state):
            return dest

    return None


# ============================================================
# Main
# ============================================================


def main(argv: list[str]) -> int:
    """Entry-point: riceve ``argv[1]`` = path dell'app exe da lanciare."""
    state = ProgressState()
    state.reset()

    if len(argv) < 2:
        state.error("gpu_bootstrap chiamato senza specificare l'app exe da lanciare")
        return 64

    app_exe = Path(argv[1])
    app_args = argv[2:]
    if not app_exe.exists():
        state.error(f"App launcher non trovato: {app_exe}")
        return 66

    _set_app_exe_path(app_exe)

    state.set_phase("detect_gpu", message="Rilevamento hardware GPU…")

    gpu = get_gpu_info_via_smi()
    if gpu is None:
        # Nessuna GPU NVIDIA / driver assente: lancia l'app direttamente
        state.done("Nessuna GPU NVIDIA rilevata. Avvio applicazione…")
        time.sleep(0.5)  # lascia il tempo allo splash di mostrare "done"
        return _launch_app(app_exe, app_args, torch_tag="cpu")

    name = gpu.get("name", "GPU sconosciuta")
    cc = gpu.get("compute_cap")
    driver = gpu.get("driver_version", "?")
    mem_mb = gpu.get("memory_total_mb", 0)

    state.set_phase(
        "select_wheel",
        message=f"GPU: {name} (SM {cc[0]}.{cc[1]}, driver {driver}, {mem_mb} MB VRAM)",
    )

    selected, reason = select_wheel_for_gpu(cc, driver)
    tag, _min_driver = selected
    if reason:
        state.set_phase("select_wheel", message=f"Nota: {reason}")

    # CPU: niente wheel aggiuntivo
    if tag == "cpu":
        state.done(f"{name} non supportata da CUDA wheel moderno. Avvio in modalità CPU…")
        time.sleep(0.5)
        return _launch_app(app_exe, app_args, torch_tag="cpu")

    # Verifica se torch è già installato con CUDA funzionante
    state.set_phase("verify", message="Verifica installazione torch corrente…")
    if _torch_cuda_ok(cc):
        state.done(f"torch+CUDA già installati correttamente ({name}).")
        time.sleep(0.3)
        return _launch_app(app_exe, app_args, torch_tag=tag)

    # Cerca in cache locale
    state.set_phase("verify", message="Ricerca wheel torch in cache locale…")
    cached = find_cached_wheel(tag)
    if cached is None:
        state.set_phase(
            "download_wheel",
            message=f"Download wheel torch per {tag} (necessario al primo avvio)…",
        )
        cached = download_wheel_for(tag, state)
        if cached is None:
            state.error(f"Impossibile scaricare il wheel torch per {tag}. Avvio in CPU fallback.")
            time.sleep(2.0)
            return _launch_app(app_exe, app_args, torch_tag="cpu")

    # Install
    if not install_wheel_inline(cached, state):
        state.error("Installazione wheel fallita. Avvio in CPU fallback.")
        time.sleep(2.0)
        return _launch_app(app_exe, app_args, torch_tag="cpu")

    state.done(f"Installato torch {tag}. Avvio applicazione…")
    time.sleep(0.5)
    return _launch_app(app_exe, app_args, torch_tag=tag)


def _torch_cuda_ok(cc: tuple[int, int]) -> bool:
    """Verifica se torch è installato e CUDA attivo + compute capability compatibile."""
    app_exe = _get_app_exe_path()
    if app_exe is None:
        return False
    # Aggiungi temporaneamente _internal/ al sys.path per trovare torch
    internal_dir = app_exe.parent / "_internal"
    if not (internal_dir / "torch" / "__init__.py").exists():
        return False
    try:
        # Assicurati che _internal sia nel path prima dell'import
        internal_str = str(internal_dir.resolve())
        if internal_str not in sys.path:
            sys.path.insert(0, internal_str)
        import torch  # type: ignore

        if not torch.cuda.is_available():
            return False
        # Verifica compute capability della GPU 0
        actual_cc = torch.cuda.get_device_capability(0)
        return actual_cc[0] == cc[0]  # almeno stesso SM major
    except Exception:  # noqa: BLE001
        return False


def _launch_app(app_exe: Path, app_args: list[str], *, torch_tag: str) -> int:
    """Lancia l'app vera con env vars appropriate."""
    env = os.environ.copy()
    env["RELICTOEPUB_BOOT_OK"] = "1"
    env["RELICTOEPUB_TORCH_TAG"] = torch_tag
    # PyInstaller bundled apps: assicurati che la working directory sia l'app
    workdir = app_exe.parent
    try:
        return subprocess.call([str(app_exe), *app_args], env=env, cwd=str(workdir))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Errore lancio app: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
