"""Update-check foundation per RelicToEpub (issue #29 - seconda fase).

Questo modulo e' il mattone base per notificare l'utente quando e'
disponibile una nuova release su GitHub. NON esegue download
automatici: si limita a confrontare la versione locale con l'ultima
tag di GitHub Releases e a restituire un ``UpdateInfo`` con i
metadati della release.

Architettura (futuro):

  1. UI/CLI chiama ``check_for_update()`` all'avvio (rate-limited:
     una volta ogni 24h, vedi cache TTL in ``UpdateCache``)
  2. Se l'utente accetta, ``fetch_release_assets()`` scarica l'asset
     giusto (``-peruser`` o standard) e ne verifica la firma
     GPG/Authenticode
  3. ``launch_installer()`` esegue l'installer scaricato con
     ``/SILENT /CLOSEAPPLICATIONS`` dopo aver terminato il processo
     corrente

Per ora (0.2.0) e' implementato solo il punto 1. I punti 2-3 sono
stub che sollevano ``NotImplementedError`` per essere estesi in
release future senza rompere la firma di ``check_for_update``.

Uso:

    >>> from scripts.update_check import check_for_update, UpdateInfo
    >>> info = check_for_update(current_version="0.1.1")
    >>> if info.update_available:
    ...     print(f"Nuova release: {info.latest_version} ({info.html_url})")
    ... else:
    ...     print("Sei aggiornato")

CLI:

    python scripts/update_check.py                  # check live
    python scripts/update_check.py --quiet          # solo exit code
    python scripts/update_check.py --offline        # usa solo la cache locale
    python scripts/update_check.py --clear-cache    # cancella la cache TTL
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------
# Configurazione
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

# Repository GitHub di default; sovrascrivibile via env var
# (utile per fork e test mirror aziendali).
DEFAULT_REPO = "Simmonne374/DAPDFAEPUB"
GITHUB_REPO = os.environ.get("RELICTOEPUB_GITHUB_REPO", DEFAULT_REPO)
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"RelicToEpub-update-check (+https://github.com/{GITHUB_REPO})"

# Cache TTL: una query al giorno e' piu' che sufficiente per il caso
# d'uso "notifica utente all'avvio". Le release di RelicToEpub hanno
# cadenza settimanale/mensile, quindi 24h riduce il rate-limit GitHub
# a ~30 richieste/mese/utente (in linea con i limiti gratuiti).
DEFAULT_CACHE_TTL_SECONDS = 24 * 3600

# Network timeout (secondi): se GitHub non risponde, NON bloccare
# l'avvio della app. Il check deve essere "fire and forget".
NETWORK_TIMEOUT_SECONDS = 5

# Cache file: in LOCALAPPDATA per installazioni per-user, in
# %ProgramData% per installazioni per-machine. Fallback a ~/.cache.
def _cache_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "RelicToEpub" / "update_check_cache.json"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "relictoepub" / "update_check_cache.json"
    return Path.home() / ".cache" / "relictoepub" / "update_check_cache.json"


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReleaseAsset:
    """Un singolo asset (binary) allegato a una release GitHub."""
    name: str
    size: int
    browser_download_url: str
    content_type: str = ""


@dataclass(frozen=True)
class UpdateInfo:
    """Risultato di un controllo aggiornamenti.

    Attributes:
        current_version: versione locale (es. "0.1.1").
        latest_version: versione piu' recente disponibile (es. "0.2.0").
        update_available: True se latest_version > current_version.
        is_prerelease: True se la release e' marcata prerelease su GitHub.
        html_url: link alla release page (per il bottone "Vedi release").
        release_notes: body della release (markdown), troncato a 4 KB.
        assets: lista degli asset binari (installer EXE ecc.).
        checked_at: timestamp epoch secondi della query.
        error: None se OK, altrimenti stringa con la causa del fallimento
            (utile per log diagnostici senza esporre stack trace).
    """
    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    is_prerelease: bool = False
    html_url: str = ""
    release_notes: str = ""
    assets: list = field(default_factory=list)
    checked_at: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # dataclasses asdict non gestisce dataclass innestate in list,
        # quindi convertiamo esplicitamente gli asset.
        d["assets"] = [asdict(a) if isinstance(a, ReleaseAsset) else a for a in self.assets]
        return d


# --------------------------------------------------------------------------
# Version parsing (PEP 440 semplificato)
# --------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:([abc]|rc|dev)(\d+)?)?(?:[+-].*)?$")


def parse_version(v: str) -> tuple:
    """Parse una versione X.Y.Z in tupla comparabile.

    Esempi:
        "0.1.1"     -> (0, 1, 1, 0, 0)
        "1.2.3a1"   -> (1, 2, 3, 1, 1)   # 1 < 0 in ordinamento custom
        "v2.0"      -> (2, 0, 0, 0, 0)
        "0.1.1+abc" -> (0, 1, 1, 0, 0)   # local identifier ignorato

    Ritorna None se il formato non e' riconoscibile (stringa vuota,
    "unknown", ecc.) — in quel caso il confronto fallira' e
    ``check_for_update`` logghera' un warning ma non abortira'.
    """
    if not v or not isinstance(v, str):
        return None
    m = _VERSION_RE.match(v.strip())
    if not m:
        return None
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    # pre-release: "a" (alpha) < "b" (beta) < "rc" < stable (no suffix)
    # Mappiamo a int con a=1, b=2, rc=3; "" (finale) = 0 perche' 0 < 1.
    pre_letter = m.group(4) or ""
    pre_num = int(m.group(5) or 0) if m.group(5) else 0
    pre_rank = {"": 0, "a": 1, "b": 2, "rc": 3}.get(pre_letter, 0)
    return (major, minor, patch, pre_rank, pre_num)


def version_is_newer(latest: str, current: str) -> Optional[bool]:
    """Confronta due versioni.

    Ritorna True se latest > current, False se <=, None se non
    parsabili (l'utente deve investigare manualmente).
    """
    l = parse_version(latest)
    c = parse_version(current)
    if l is None or c is None:
        return None
    return l > c


# --------------------------------------------------------------------------
# Cache TTL
# --------------------------------------------------------------------------

class UpdateCache:
    """Cache JSON persistente per evitare query ripetute a GitHub.

    Formato del file::

        {
            "checked_at": 1737654321.0,
            "info": {...}  // UpdateInfo serializzato
        }

    Il file e' scritto atomicamente (scrivi in .tmp + rename) per
    evitare corruzione in caso di crash a meta' write. Su NTFS
    ``os.replace`` e' atomico sulla stessa partizione.
    """

    def __init__(self, path: Optional[Path] = None, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS):
        self.path = path or _cache_path()
        self.ttl_seconds = ttl_seconds

    def is_fresh(self) -> bool:
        """True se la cache esiste ed e' piu' recente di TTL."""
        if not self.path.exists():
            return False
        try:
            age = time.time() - self.path.stat().st_mtime
            return age < self.ttl_seconds
        except OSError:
            return False

    def load(self) -> Optional[UpdateInfo]:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None
        info_dict = raw.get("info")
        if not isinstance(info_dict, dict):
            return None
        assets_raw = info_dict.get("assets") or []
        assets = []
        for a in assets_raw:
            if isinstance(a, dict):
                assets.append(ReleaseAsset(
                    name=str(a.get("name", "")),
                    size=int(a.get("size", 0)),
                    browser_download_url=str(a.get("browser_download_url", "")),
                    content_type=str(a.get("content_type", "")),
                ))
        return UpdateInfo(
            current_version=str(info_dict.get("current_version", "")),
            latest_version=info_dict.get("latest_version"),
            update_available=bool(info_dict.get("update_available", False)),
            is_prerelease=bool(info_dict.get("is_prerelease", False)),
            html_url=str(info_dict.get("html_url", "")),
            release_notes=str(info_dict.get("release_notes", "")),
            assets=assets,
            checked_at=float(info_dict.get("checked_at", 0.0)),
            error=info_dict.get("error"),
        )

    def save(self, info: UpdateInfo) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"checked_at": info.checked_at, "info": info.to_dict()}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            # Cache fallita: ok, richeckeremo al prossimo avvio.
            pass

    def clear(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Network: GitHub API
# --------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = NETWORK_TIMEOUT_SECONDS) -> dict:
    """GET request con UA custom e content negotiation per v3 API."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # GitHub risponde 200 + JSON; le rate-limit responses hanno Retry-After.
        return json.loads(resp.read().decode("utf-8"))


def _parse_release_payload(payload: dict, current_version: str) -> UpdateInfo:
    """Estrae un UpdateInfo dal JSON della API GitHub Releases."""
    tag = (payload.get("tag_name") or "").lstrip("v")
    prerelease = bool(payload.get("prerelease", False))
    html_url = str(payload.get("html_url", ""))
    body = str(payload.get("body", "") or "")
    if len(body) > 4096:
        body = body[:4096] + "\n\n[... troncato, vedi html_url]"
    assets_raw = payload.get("assets") or []
    assets = [
        ReleaseAsset(
            name=str(a.get("name", "")),
            size=int(a.get("size", 0)),
            browser_download_url=str(a.get("browser_download_url", "")),
            content_type=str(a.get("content_type", "")),
        )
        for a in assets_raw
        if isinstance(a, dict)
    ]
    cmp = version_is_newer(tag, current_version)
    update_available = bool(cmp) if cmp is not None else False
    return UpdateInfo(
        current_version=current_version,
        latest_version=tag or None,
        update_available=update_available,
        is_prerelease=prerelease,
        html_url=html_url,
        release_notes=body,
        assets=assets,
        checked_at=time.time(),
        error=None if cmp is not None else f"Impossibile confrontare '{tag}' con '{current_version}'",
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def check_for_update(
    current_version: str,
    *,
    repo: str = GITHUB_REPO,
    cache: Optional[UpdateCache] = None,
    force_refresh: bool = False,
    offline: bool = False,
) -> UpdateInfo:
    """Controlla se esiste una nuova release su GitHub.

    Args:
        current_version: versione locale (es. "0.1.1").
        repo: owner/repo GitHub (default: Simmonne374/DAPDFAEPUB).
        cache: UpdateCache iniettato (default: cache standard).
        force_refresh: True per ignorare TTL e rifare la query.
        offline: True per non fare network e usare SOLO la cache
            locale (ritorna errore se cache vuota).

    Returns:
        UpdateInfo con latest_version, update_available, ecc.
        In caso di errore network/cache, ``info.error`` e' valorizzato
        e ``info.update_available = False`` (l'utente non viene
        notificato di un update inesistente).
    """
    cache = cache or UpdateCache()
    now = time.time()

    # 1) Fast path: cache fresca + non force_refresh → riusa
    if not force_refresh and cache.is_fresh():
        cached = cache.load()
        if cached is not None:
            return cached

    # 2) Offline esplicito o network non disponibile → cache stale
    if offline:
        cached = cache.load()
        if cached is not None:
            return cached
        return UpdateInfo(
            current_version=current_version,
            checked_at=now,
            error="offline=true e cache vuota",
        )

    # 3) Network query
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        payload = _http_get_json(api_url, timeout=NETWORK_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        # 404 = repo senza release; 403 = rate limit. Distinguiamoli.
        msg = f"HTTP {e.code} ({e.reason})"
        if e.code == 404:
            msg = "Nessuna release pubblicata su GitHub"
        elif e.code == 403:
            msg = "Rate limit GitHub raggiunto (riprova piu' tardi)"
        cached = cache.load()
        return (cached or UpdateInfo(current_version=current_version)).__class__(
            current_version=current_version,
            latest_version=(cached.latest_version if cached else None),
            update_available=False,
            html_url=(cached.html_url if cached else ""),
            release_notes=(cached.release_notes if cached else ""),
            assets=(cached.assets if cached else []),
            checked_at=now,
            error=msg,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        cached = cache.load()
        return UpdateInfo(
            current_version=current_version,
            latest_version=(cached.latest_version if cached else None),
            update_available=False,
            html_url=(cached.html_url if cached else ""),
            release_notes=(cached.release_notes if cached else ""),
            assets=(cached.assets if cached else []),
            checked_at=now,
            error=f"network: {e}",
        )

    info = _parse_release_payload(payload, current_version)
    cache.save(info)
    return info


# --------------------------------------------------------------------------
# Future hooks (per release 0.2.x)
# --------------------------------------------------------------------------

def fetch_release_assets(info: UpdateInfo, prefer_peruser: bool = False) -> bytes:
    """Scarica il binary dell'installer.

    NON IMPLEMENTATO in 0.2.0: verra' aggiunto quando il flusso
    download+verify+install sara' stabile. Per ora stub esplicito.
    """
    raise NotImplementedError(
        "fetch_release_assets() verra' implementato in una release "
        "futura. Per aggiornare, scarica manualmente da "
        f"{info.html_url or GITHUB_API_URL}"
    )


def launch_installer(installer_path: Path, silent: bool = True) -> int:
    """Lancia l'installer scaricato.

    NON IMPLEMENTATO in 0.2.0: richiede coordinazione con il processo
    corrente (``/CLOSEAPPLICATIONS``) e gestione errori robusta.
    """
    raise NotImplementedError(
        "launch_installer() verra' implementato in una release futura. "
        "Esegui manualmente: "
        f"{installer_path}"
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_human(info: UpdateInfo, fp) -> None:
    if info.error:
        print(f"⚠️  Update check non riuscito: {info.error}", file=fp)
        return
    print(f"Versione locale:    {info.current_version}")
    print(f"Ultima release:     {info.latest_version or 'sconosciuta'}")
    if info.is_prerelease:
        print(f"                   ⚠️  questa e' una PRERELEASE")
    if info.update_available:
        print()
        print(f"🆕  Aggiornamento disponibile! Vedi: {info.html_url}")
        if info.assets:
            print()
            print("Asset scaricabili:")
            for a in info.assets:
                size_mb = a.size / 1024 / 1024 if a.size else 0
                print(f"  - {a.name}  ({size_mb:.1f} MB)  {a.browser_download_url}")
    else:
        print()
        print("✅  Sei aggiornato.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Controlla se esiste una nuova release di RelicToEpub su GitHub.",
    )
    parser.add_argument(
        "--current-version",
        default=None,
        help="Versione locale (default: letta da pyproject.toml)",
    )
    parser.add_argument("--repo", default=GITHUB_REPO, help="owner/repo GitHub")
    parser.add_argument("--offline", action="store_true", help="usa solo la cache locale")
    parser.add_argument("--force", action="store_true", help="ignora TTL cache")
    parser.add_argument("--clear-cache", action="store_true", help="cancella cache e esci")
    parser.add_argument("--quiet", action="store_true", help="solo exit code (0=aggiornato, 1=update)")
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_args()

    if args.clear_cache:
        UpdateCache().clear()
        if not args.quiet:
            print("Cache cancellata.")
        return 0

    # Determina versione locale
    cv = args.current_version
    if not cv:
        try:
            # Import lazy: evita di richiedere il package in ambienti
            # minimal (es. CI che gira solo il test offline).
            from relictoepub import __version__ as cv
        except ImportError:
            cv = "0.0.0"

    info = check_for_update(
        current_version=cv,
        repo=args.repo,
        force_refresh=args.force,
        offline=args.offline,
    )

    if args.quiet:
        return 1 if info.update_available else 0
    if args.json:
        print(json.dumps(info.to_dict(), indent=2, ensure_ascii=False))
        return 0
    _print_human(info, sys.stdout)
    return 1 if info.update_available else 0


if __name__ == "__main__":
    sys.exit(main())
