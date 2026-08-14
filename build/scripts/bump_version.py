"""Calcola la prossima versione semantica di RelicToEpub.

Logica:
1. Legge tutti i tag `vX.Y.Z` (escluso `build-*` e `latest`) ordinati
   semanticamente; il piu alto e la "base version".
2. Legge i commit dal base (escluso) a HEAD e li classifica secondo
   Conventional Commits:
     - `feat!:` / `BREAKING CHANGE:` -> major
     - `feat:` -> minor
     - `fix:` / `perf:` -> patch
     - `chore:` / `docs:` / `refactor:` / `test:` / `build:` / `ci:` /
       `style:` / `revert:` -> patch
   Il livello di bump e il massimo tra quelli incontrati.
3. Override manuale via `--version X.Y.Z` o `--bump patch|minor|major`
   (vincono sempre rispetto al calcolo automatico).

Output: stampa la prossima versione su stdout, una sola riga.

Usage:
    python -m build.scripts.bump_version [--version X.Y.Z] [--bump patch|minor|major]
    python build/scripts/bump_version.py [...]

Exit code:
    0 sempre (fail-soft); la stringa "0.0.0+unknown" indica un problema.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Forza stdout a UTF-8 (Windows apre la console in cp1252 di default).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.\-]+)?$")
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?P<scope1>\([^)]*\))?(?P<bang>!)?:\s",
)
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:\s", re.MULTILINE | re.IGNORECASE)
EMOJI_PREFIX_RE = re.compile(
    r"^[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u27BF]+\s*"
)


def _run_git(*args: str, cwd: Path) -> str:
    """Esegue git in subprocess e ritorna stdout (mai errore sollevato)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ""
    return proc.stdout.strip()


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    m = SEMVER_RE.match(tag)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _latest_semver_tag(repo: Path) -> tuple[str, tuple[int, int, int]] | None:
    """Ritorna (tag, (major, minor, patch)) del tag semver piu alto."""
    tags_raw = _run_git("tag", "--list", cwd=repo)
    if not tags_raw:
        return None
    best: tuple[tuple[int, int, int], str] | None = None
    for line in tags_raw.splitlines():
        tag = line.strip()
        if not tag or tag.startswith("build-") or tag in {"latest"}:
            continue
        parsed = _parse_semver(tag)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, tag)
    if best is None:
        return None
    return best[1], best[0]


def _classify_bump(commit_subject: str, commit_body: str) -> str:
    """Ritorna 'major' | 'minor' | 'patch' | '' per un singolo commit."""
    subject = (commit_subject or "").strip()
    if not subject:
        return ""
    subject = EMOJI_PREFIX_RE.sub("", subject).strip()
    m = CONVENTIONAL_RE.match(subject)
    ctype = m.group("type").lower() if m and m.group("type") else ""
    bang = bool(m and m.group("bang"))
    breaking = bang or bool(BREAKING_FOOTER_RE.search(commit_body or ""))
    if breaking:
        return "major"
    if ctype == "feat":
        return "minor"
    if ctype in {"fix", "perf"}:
        return "patch"
    if ctype in {
        "chore",
        "docs",
        "refactor",
        "test",
        "build",
        "ci",
        "style",
        "revert",
    }:
        return "patch"
    # Nessuna marcatura: bump minimo (patch) per non perdere release.
    return "patch"


def _bump(version: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def _commits_since(repo: Path, since: str | None) -> list[tuple[str, str]]:
    """Ritorna lista di (subject, body) per ogni commit dal tag `since`."""
    rng = f"{since}..HEAD" if since else "HEAD"
    sep = "---END-COMMIT---"
    fmt = f"%s{sep}%b"
    out = _run_git("log", rng, f"--pretty=format:{fmt}", cwd=repo)
    if not out:
        return []
    commits: list[tuple[str, str]] = []
    for chunk in out.split(sep):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Separiamo subject (prima riga) dal body (resto).
        if "\n" in chunk:
            subject, body = chunk.split("\n", 1)
        else:
            subject, body = chunk, ""
        commits.append((subject.strip(), body.strip()))
    return commits


def _format_version(v: tuple[int, int, int]) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Calcola la prossima versione semver di RelicToEpub.")
    parser.add_argument("--version", help="Versione esplicita X.Y.Z (vincer su tutto).")
    parser.add_argument("--bump", choices=["major", "minor", "patch"], help="Forza tipo di bump.")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Stampa solo la versione calcolata (default: lo fa, tenuto per chiarezza).",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve()
    # Risali alla root del repo: cerca .git (directory nei checkout normali,
    # file nei worktree). Fermati a 6 livelli per non risalire oltre.
    for _ in range(8):
        dot = repo / ".git"
        if dot.exists():
            break
        if repo.parent == repo:
            break
        repo = repo.parent
    if not (repo / ".git").exists():
        repo = Path.cwd()

    if args.version:
        parsed = _parse_semver(args.version)
        if parsed is None:
            print("0.0.0+unknown", flush=True)
            return 1
        print(_format_version(parsed), flush=True)
        return 0

    base = _latest_semver_tag(repo)
    if base is None:
        # Nessun tag semver: il prossimo parte da 0.1.0.
        current = (0, 1, 0)
        since = None
        log_note = "nessun tag semver trovato"
    else:
        current = base[1]
        since = base[0]
        log_note = f"base={base[0]}"

    if args.bump:
        level = args.bump
        log_note += f" (override={level})"
    else:
        level = ""
        level_rank = {"patch": 1, "minor": 2, "major": 3}
        for sub, body in _commits_since(repo, since):
            b = _classify_bump(sub, body)
            if b and (not level or level_rank[b] > level_rank[level]):
                level = b
        if not level:
            level = "patch"
        log_note += f" calculated={level}"

    next_version = _bump(current, level)
    print(_format_version(next_version), flush=True)
    print(f"# {log_note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
