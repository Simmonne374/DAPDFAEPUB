"""Golden tests per ``relictoepub.compile.build_epub``.

Per ogni fixture in :data:`tests.golden_helpers.FIXTURES`:

1. Genera un EPUB in ``tmp_path``.
2. Verifica che sia un EPUB3 valido (:func:`assert_valid_epub3`).
3. Confronta byte-per-byte (normalizzato) contro il golden committato.

Per rigenerare i golden dopo una modifica intenzionale:

    pytest tests/test_compile_golden.py --update-golden

Poi committare i file ``*.epub`` aggiornati.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from golden_helpers import (
    FIXTURES,
    GOLDEN_DIR,
    assert_epub_matches_golden,
    assert_valid_epub3,
    build_golden_epub,
    golden_sha256,
    update_golden,
)

# Skip se pandoc mancante (build_epub lo richiede)
try:
    from relictoepub.compile.build_epub import _check_pandoc
    _check_pandoc()
    has_pandoc = True
except (RuntimeError, OSError, ImportError):
    has_pandoc = False

pytestmark = [
    pytest.mark.skipif(
        not has_pandoc,
        reason="pandoc non installato (richiesto per pypandoc)",
    ),
    pytest.mark.golden,
]


@pytest.mark.parametrize("fixture_id", sorted(FIXTURES.keys()))
def test_golden_epub_structure_is_valid(
    fixture_id: str, tmp_path: Path
) -> None:
    """Ogni EPUB generato da una fixture deve essere un EPUB3 valido."""
    generated = build_golden_epub(fixture_id, tmp_path)
    assert_valid_epub3(generated)


@pytest.mark.parametrize("fixture_id", sorted(FIXTURES.keys()))
def test_golden_epub_matches_committed(
    fixture_id: str,
    tmp_path: Path,
    update_golden_flag: bool,
) -> None:
    """L'EPUB generato deve matchare il golden committato.

    Con ``--update-golden``, il golden viene sovrascritto.
    """
    generated = build_golden_epub(fixture_id, tmp_path)
    golden = GOLDEN_DIR / f"{fixture_id}.epub"

    if update_golden_flag:
        update_golden(generated, golden)
        # In update mode skip the equality check (abbiamo appena sovrascritto)
        pytest.skip(f"Golden aggiornato: {golden}")

    assert_epub_matches_golden(generated, golden)


def test_golden_helpers_sha256_is_deterministic(tmp_path: Path) -> None:
    """Lo SHA256 normalizzato deve essere deterministico fra esecuzioni.

    Puo differire solo se pypandoc ha cambiato il formato di output fra
    versioni (allora rigenerare i golden).
    """
    a = build_golden_epub("01_simple_h1", tmp_path / "a")
    b = build_golden_epub("01_simple_h1", tmp_path / "b")
    assert golden_sha256(a) == golden_sha256(b)


def test_golden_directory_is_complete() -> None:
    """Ogni fixture dichiarata in FIXTURES deve avere un file .md presente."""
    missing = [
        fid for fid, info in FIXTURES.items()
        if not (GOLDEN_DIR / info["md"]).is_file()
    ]
    assert not missing, f"Fixture .md mancanti: {missing}"


def test_golden_total_size_under_200kb() -> None:
    """I golden file EPUB committati devono essere leggeri (<200 KB totale)."""
    epubs = list(GOLDEN_DIR.glob("*.epub"))
    if not epubs:
        pytest.skip("Nessun golden .epub presente (rigenerare con --update-golden)")
    total = sum(p.stat().st_size for p in epubs)
    assert total < 200 * 1024, (
        f"Golden EPUB troppo grandi ({total} bytes totali). "
        f"Lista: {[(p.name, p.stat().st_size) for p in epubs]}"
    )


# Cleanup helper — rimuove file _diff_*.txt lasciati da assert_epub_matches_golden
@pytest.fixture(autouse=True)
def _cleanup_diff_files(request: pytest.FixtureRequest) -> None:
    yield
    tmp_path = getattr(request, "tmp_path", None)
    if tmp_path is None:
        return
    for diff_file in tmp_path.glob("_diff_*.txt"):
        try:
            diff_file.unlink()
        except OSError:
            pass