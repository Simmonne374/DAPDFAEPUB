"""Test per ``scripts/update_check.py`` (fondamenta auto-update).

Copertura:

* ``parse_version`` / ``version_is_newer``: parsing PEP 440 semplificato.
* ``UpdateCache``: TTL, serializzazione, clear, sopravvivenza a path
  non scrivibili.
* ``check_for_update``: fast-path (cache fresca), offline, network
  error (404/403/URLError), success path con mock del JSON GitHub.
* Stub espliciti per ``fetch_release_assets`` e ``launch_installer``:
  devono sollevare ``NotImplementedError``.

I test di network usano ``monkeypatch`` per intercettare
``urllib.request.urlopen`` e ``UpdateCache`` con un path temporaneo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Aggiungi scripts/ al path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import update_check as uc


# =====================================================================
# parse_version
# =====================================================================

@pytest.mark.parametrize("text,expected", [
    ("0.1.1",       (0, 1, 1, 4, 0)),    # finale: pre_rank=4 (PEP 440)
    ("1.0",         (1, 0, 0, 4, 0)),
    ("v2.0",        (2, 0, 0, 4, 0)),
    ("v2.0.0",      (2, 0, 0, 4, 0)),
    ("1.2.3a1",     (1, 2, 3, 1, 1)),
    ("1.2.3b2",     (1, 2, 3, 2, 2)),
    ("1.2.3rc1",    (1, 2, 3, 3, 1)),
    ("0.1.1+abc",   (0, 1, 1, 4, 0)),    # local identifier ignorato
    ("",            None),
    ("unknown",     None),
    (None,          None),
])
def test_parse_version(text, expected) -> None:
    assert uc.parse_version(text) == expected


def test_parse_version_garbage_returns_none() -> None:
    assert uc.parse_version("hello world") is None
    # "123.456" e' valida: major=123, minor=456, finale (pre_rank=4)
    assert uc.parse_version("123.456") == (123, 456, 0, 4, 0)
    # Invece, "1.2.3.4" non matcha il pattern X.Y.Z
    assert uc.parse_version("1.2.3.4") is None
    assert uc.parse_version("not.a.version") is None


# =====================================================================
# version_is_newer
# =====================================================================

@pytest.mark.parametrize("latest,current,expected", [
    ("0.2.0", "0.1.1", True),
    ("0.1.1", "0.1.1", False),
    ("0.1.0", "0.1.1", False),
    ("1.0.0", "0.9.9", True),
    ("0.1.2", "0.1.1", True),
    ("1.0.0rc1", "1.0.0b5", True),   # rc > beta (PEP 440)
    ("1.0.0b5", "1.0.0a1", True),    # beta > alpha (PEP 440)
    ("2.0.0", "1.99.99", True),
    ("invalid", "0.1.1", None),      # latest non parsabile
    ("0.1.1", "invalid", None),      # current non parsabile
    # PEP 440: il rilascio finale e' SEMPRE il piu' alto per la stessa
    # tripletta X.Y.Z. Bug storico: pre_rank=0 per finale + pre_rank=1
    # per alpha portava "1.0.0a1 > 1.0.0". Fisso in questa versione.
    ("1.0.0",   "1.0.0a1",  True),   # finale > alpha
    ("1.0.0",   "1.0.0b1",  True),   # finale > beta
    ("1.0.0",   "1.0.0rc1", True),   # finale > rc
    ("1.0.0a1", "1.0.0",    False),  # alpha < finale
    ("1.0.0b1", "1.0.0",    False),
    ("1.0.0rc1", "1.0.0",    False),
    ("1.0.0rc1", "1.0.0rc2", False), # rc1 < rc2 (numero progressivo)
    ("1.0.0rc3", "1.0.0rc2", True),
])
def test_version_is_newer(latest, current, expected) -> None:
    assert uc.version_is_newer(latest, current) == expected


# =====================================================================
# UpdateCache
# =====================================================================


@pytest.fixture()
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Usa un path temporaneo per la cache."""
    p = tmp_path / "cache.json"
    return p


def test_update_cache_not_fresh_if_missing(cache_dir: Path) -> None:
    c = uc.UpdateCache(path=cache_dir, ttl_seconds=3600)
    assert not c.is_fresh()


def test_update_cache_load_returns_none_when_missing(cache_dir: Path) -> None:
    c = uc.UpdateCache(path=cache_dir)
    assert c.load() is None


def test_update_cache_save_then_load(cache_dir: Path) -> None:
    c = uc.UpdateCache(path=cache_dir)
    info = uc.UpdateInfo(
        current_version="0.1.1",
        latest_version="0.2.0",
        update_available=True,
        html_url="https://example.com/release",
        release_notes="body",
        checked_at=12345.0,
    )
    c.save(info)
    assert cache_dir.exists(), "Cache file should be created"
    loaded = c.load()
    assert loaded is not None
    assert loaded.latest_version == "0.2.0"
    assert loaded.update_available is True
    assert loaded.html_url == "https://example.com/release"
    assert loaded.checked_at == 12345.0


def test_update_cache_save_roundtrip_assets(cache_dir: Path) -> None:
    c = uc.UpdateCache(path=cache_dir)
    info = uc.UpdateInfo(
        current_version="0.1.1",
        latest_version="0.2.0",
        update_available=True,
        assets=[
            uc.ReleaseAsset(name="setup.exe", size=100, browser_download_url="x", content_type="exe"),
            uc.ReleaseAsset(name="setup-peruser.exe", size=100, browser_download_url="y", content_type="exe"),
        ],
    )
    c.save(info)
    loaded = c.load()
    assert loaded is not None
    assert len(loaded.assets) == 2
    assert loaded.assets[0].name == "setup.exe"
    assert loaded.assets[1].name == "setup-peruser.exe"


def test_update_cache_save_atomic_writes(cache_dir: Path) -> None:
    """La cache usa .tmp + os.replace (scrittura atomica)."""
    c = uc.UpdateCache(path=cache_dir)
    info = uc.UpdateInfo(current_version="0.1.1", latest_version="0.2.0")
    c.save(info)
    # Nessun file .tmp deve restare
    leftover = list(cache_dir.parent.glob("*.tmp"))
    assert not any(p.exists() for p in leftover), (
        "Atomic write fallita: tmp file residuo"
    )


def test_update_cache_clear(cache_dir: Path) -> None:
    c = uc.UpdateCache(path=cache_dir)
    info = uc.UpdateInfo(current_version="0.1.1", latest_version="0.2.0")
    c.save(info)
    assert cache_dir.exists()
    c.clear()
    assert not cache_dir.exists()


def test_update_cache_clear_when_missing(cache_dir: Path) -> None:
    c = uc.UpdateCache(path=cache_dir)
    # Non deve sollevare eccezioni
    c.clear()


def test_update_cache_is_fresh_respects_ttl(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Una cache "vecchia" non deve essere considerata fresh."""
    import time
    c = uc.UpdateCache(path=cache_dir, ttl_seconds=10)
    info = uc.UpdateInfo(current_version="0.1.1", latest_version="0.2.0", checked_at=time.time())
    c.save(info)
    # appena scritta: fresca
    assert c.is_fresh()
    # Forza mtime indietro nel tempo (11s fa) per simulare cache vecchia
    import os
    old = time.time() - 11
    os.utime(cache_dir, (old, old))
    assert not c.is_fresh()


def test_update_cache_survives_corrupt_json(cache_dir: Path) -> None:
    """Una cache corrotta deve essere trattata come assente (non crash)."""
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.write_text("{ not json", encoding="utf-8")
    c = uc.UpdateCache(path=cache_dir)
    assert c.load() is None
    # Non deve propagare l'eccezione al chiamante


# =====================================================================
# check_for_update - network paths (mocked)
# =====================================================================


def _make_fake_response(payload: dict) -> object:
    """Costruisce un fake urllib response context manager."""
    import io
    body = json.dumps(payload).encode("utf-8")

    class _Resp:
        def __init__(self):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self._body

    return _Resp()


def test_check_for_update_uses_cache_when_fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cache fresca + non force_refresh: NON deve chiamare la rete."""
    cache_path = tmp_path / "cache.json"
    cache = uc.UpdateCache(path=cache_path, ttl_seconds=3600)
    cached_info = uc.UpdateInfo(
        current_version="0.1.1",
        latest_version="0.2.0",
        update_available=True,
        checked_at=99999.0,
    )
    cache.save(cached_info)

    def fail_urlopen(*a, **kw):
        raise AssertionError("Network should not be called when cache is fresh")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    info = uc.check_for_update(current_version="0.1.1", cache=cache)
    assert info.latest_version == "0.2.0"
    assert info.update_available is True


def test_check_for_update_offline_returns_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """offline=True deve usare la cache anche se vecchia, senza rete."""
    cache_path = tmp_path / "cache.json"
    cache = uc.UpdateCache(path=cache_path, ttl_seconds=3600)
    cached_info = uc.UpdateInfo(
        current_version="0.1.1",
        latest_version="0.2.0",
        update_available=True,
        checked_at=0.0,
    )
    cache.save(cached_info)

    def fail_urlopen(*a, **kw):
        raise AssertionError("offline=True must not hit network")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    info = uc.check_for_update(current_version="0.1.1", cache=cache, offline=True)
    assert info.latest_version == "0.2.0"


def test_check_for_update_offline_no_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """offline=True + cache vuota -> errore esplicito, non crash."""
    cache = uc.UpdateCache(path=tmp_path / "missing.json")

    def fail_urlopen(*a, **kw):
        raise AssertionError("offline=True must not hit network")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    info = uc.check_for_update(current_version="0.1.1", cache=cache, offline=True)
    assert info.update_available is False
    assert info.error is not None
    assert "offline" in info.error.lower()


def test_check_for_update_success_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Network 200 + JSON valido -> UpdateInfo popolato, cache aggiornata."""
    cache = uc.UpdateCache(path=tmp_path / "cache.json", ttl_seconds=1)
    payload = {
        "tag_name": "v0.3.0",
        "prerelease": False,
        "html_url": "https://github.com/foo/bar/releases/tag/v0.3.0",
        "body": "## Highlights\n- New feature",
        "assets": [
            {"name": "setup.exe", "size": 3145728, "browser_download_url": "u1",
             "content_type": "application/octet-stream"},
            {"name": "setup-peruser.exe", "size": 3145728, "browser_download_url": "u2",
             "content_type": "application/octet-stream"},
        ],
    }

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _make_fake_response(payload))

    info = uc.check_for_update(current_version="0.1.1", cache=cache, force_refresh=True)
    assert info.latest_version == "0.3.0"
    assert info.update_available is True
    assert info.is_prerelease is False
    assert info.html_url == "https://github.com/foo/bar/releases/tag/v0.3.0"
    assert len(info.assets) == 2
    # Cache deve essere stata aggiornata
    assert cache.load() is not None


def test_check_for_update_404_no_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """HTTP 404: il repo non ha release pubblicate."""
    cache = uc.UpdateCache(path=tmp_path / "cache.json", ttl_seconds=1)

    import urllib.error
    err = urllib.error.HTTPError(
        url="https://api.github.com/...", code=404, msg="Not Found",
        hdrs={}, fp=None,
    )

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(err))

    info = uc.check_for_update(current_version="0.1.1", cache=cache, force_refresh=True)
    assert info.update_available is False
    assert info.error is not None
    assert "Nessuna release" in info.error


def test_check_for_update_rate_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """HTTP 403: rate limit GitHub raggiunto."""
    cache = uc.UpdateCache(path=tmp_path / "cache.json", ttl_seconds=1)

    import urllib.error
    err = urllib.error.HTTPError(
        url="https://api.github.com/...", code=403, msg="Forbidden",
        hdrs={"Retry-After": "60"}, fp=None,
    )

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(err))

    info = uc.check_for_update(current_version="0.1.1", cache=cache, force_refresh=True)
    assert info.update_available is False
    assert "Rate limit" in (info.error or "")


def test_check_for_update_network_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Network down: ritorna errore ma NON crasha."""
    cache = uc.UpdateCache(path=tmp_path / "cache.json", ttl_seconds=1)

    import urllib.error
    err = urllib.error.URLError("DNS failure")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(err))

    info = uc.check_for_update(current_version="0.1.1", cache=cache, force_refresh=True)
    assert info.update_available is False
    assert info.error is not None
    assert "DNS" in info.error or "network" in info.error.lower()


def test_check_for_update_falls_back_to_cached_on_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Se la query fallisce ma la cache esiste (vecchia), usa la cache come best-effort."""
    cache = uc.UpdateCache(path=tmp_path / "cache.json", ttl_seconds=1)
    # Pre-popola la cache
    cached = uc.UpdateInfo(
        current_version="0.1.1",
        latest_version="0.2.0",
        update_available=True,
        html_url="old",
    )
    cache.save(cached)
    # Forza TTL scaduto
    import os, time
    old = time.time() - 3600
    os.utime(cache.path, (old, old))

    import urllib.error
    err = urllib.error.URLError("offline")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(err))

    info = uc.check_for_update(current_version="0.1.1", cache=cache, force_refresh=False)
    # Deve riusare la cache anche se vecchia, perche' e' meglio di niente
    assert info.latest_version == "0.2.0"
    assert info.error is not None  # errore marcato


def test_check_for_update_release_notes_truncated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Body >4KB deve essere troncato a 4KB."""
    cache = uc.UpdateCache(path=tmp_path / "cache.json", ttl_seconds=1)
    huge_body = "x" * (8 * 1024)  # 8 KB
    payload = {
        "tag_name": "v0.2.0",
        "prerelease": False,
        "html_url": "https://example.com",
        "body": huge_body,
        "assets": [],
    }
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _make_fake_response(payload))

    info = uc.check_for_update(current_version="0.1.1", cache=cache, force_refresh=True)
    assert len(info.release_notes) <= 4096 + 100  # margine per messaggio di troncamento
    assert "[... troncato" in info.release_notes


# =====================================================================
# Stub NotImplementedError
# =====================================================================


def test_fetch_release_assets_raises_not_implemented() -> None:
    info = uc.UpdateInfo(current_version="0.1.1", latest_version="0.2.0")
    with pytest.raises(NotImplementedError):
        uc.fetch_release_assets(info)


def test_launch_installer_raises_not_implemented(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError):
        uc.launch_installer(tmp_path / "setup.exe")


# =====================================================================
# ReleaseAsset / UpdateInfo dataclass smoke
# =====================================================================


def test_release_asset_dataclass_basic() -> None:
    a = uc.ReleaseAsset(name="x.exe", size=100, browser_download_url="u")
    assert a.name == "x.exe"
    assert a.size == 100
    assert a.browser_download_url == "u"
    assert a.content_type == ""  # default


def test_update_info_defaults() -> None:
    info = uc.UpdateInfo(current_version="0.1.1")
    assert info.latest_version is None
    assert info.update_available is False
    assert info.is_prerelease is False
    assert info.html_url == ""
    assert info.release_notes == ""
    assert info.assets == []
    assert info.checked_at == 0.0
    assert info.error is None


def test_update_info_to_dict() -> None:
    info = uc.UpdateInfo(
        current_version="0.1.1",
        latest_version="0.2.0",
        assets=[uc.ReleaseAsset(name="setup.exe", size=1, browser_download_url="u")],
    )
    d = info.to_dict()
    assert d["current_version"] == "0.1.1"
    assert d["latest_version"] == "0.2.0"
    assert isinstance(d["assets"], list)
    assert d["assets"][0]["name"] == "setup.exe"


def test_update_info_frozen() -> None:
    """UpdateInfo e' frozen=True: i campi non sono mutabili."""
    info = uc.UpdateInfo(current_version="0.1.1")
    with pytest.raises((AttributeError, Exception)):
        info.current_version = "0.2.0"  # type: ignore[misc]


# =====================================================================
# CLI smoke (exit code)
# =====================================================================


def test_cli_quiet_returns_0_when_up_to_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI con --quiet + cache che dice 'no update' deve ritornare 0."""
    cache_path = tmp_path / "cache.json"
    cache = uc.UpdateCache(path=cache_path, ttl_seconds=3600)
    cache.save(uc.UpdateInfo(current_version="0.1.1", latest_version="0.1.1", update_available=False))

    # Mock argv + urlopen (per evitare network) + cache path
    monkeypatch.setattr("sys.argv", ["update_check.py", "--current-version=0.1.1", "--quiet"])
    monkeypatch.setattr(uc, "_cache_path", lambda: cache_path)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("quiet + cache fresca non deve fare network")
    ))

    rc = uc.main()
    assert rc == 0


def test_cli_quiet_returns_1_when_update_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI con --quiet + cache che dice 'update available' deve ritornare 1."""
    cache_path = tmp_path / "cache.json"
    cache = uc.UpdateCache(path=cache_path, ttl_seconds=3600)
    cache.save(uc.UpdateInfo(current_version="0.1.1", latest_version="0.2.0", update_available=True))

    monkeypatch.setattr("sys.argv", ["update_check.py", "--current-version=0.1.1", "--quiet"])
    monkeypatch.setattr(uc, "_cache_path", lambda: cache_path)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("quiet + cache fresca non deve fare network")
    ))

    rc = uc.main()
    assert rc == 1


def test_cli_clear_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--clear-cache rimuove il file e ritorna 0."""
    cache_path = tmp_path / "cache.json"
    cache = uc.UpdateCache(path=cache_path)
    cache.save(uc.UpdateInfo(current_version="0.1.1", latest_version="0.2.0"))
    assert cache_path.exists()

    monkeypatch.setattr("sys.argv", ["update_check.py", "--clear-cache", "--quiet"])
    monkeypatch.setattr(uc, "_cache_path", lambda: cache_path)

    rc = uc.main()
    assert rc == 0
    assert not cache_path.exists()
