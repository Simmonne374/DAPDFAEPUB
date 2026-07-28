"""Aggiorna pyproject.toml a una nuova versione (single source of truth) e
apre una pull request contenente solo il bump.

Funzionamento:
- Calcola la nuova versione (default: l'argomento ``--version`` oppure la
  lettura via ``build/scripts/bump_version.py``).
- Crea un branch ``chore/bump-vX.Y.Z`` da ``master``.
- Sostituisce la riga ``version = "..."`` in pyproject.toml.
- Commit + push del branch.
- Apre una PR verso ``master`` usando ``gh``.

Il workflow ``release-bump.yml`` invoca questo script su
``release: published`` per riallineare la versione committed dopo ogni
release, mantenendo il workflow di build su ``master`` deterministico.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PYPROJECT_RE = re.compile(
    r'(?P<prefix>^version\s*=\s*["\'])(?P<old>[^"\']+)(["\'])',
    re.MULTILINE,
)


def _run(cmd: list[str], cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        sys.stderr.write(f"$ {' '.join(cmd)}\n{proc.stderr}\n")
        raise SystemExit(proc.returncode)
    return proc.stdout.strip()


def _compute_version(repo: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    helper = repo / "build" / "scripts" / "bump_version.py"
    if not helper.exists():
        return "0.0.0+unknown"
    return _run([sys.executable, str(helper)], cwd=repo).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggiorna pyproject.toml e apre PR di bump versione.")
    parser.add_argument("--version", help="Nuova versione (es. 0.2.0). Default: bump automatico.")
    parser.add_argument("--base", default="master", help="Branch base (default: master).")
    parser.add_argument("--dry-run", action="store_true", help="Non push, non aprire PR.")
    args = parser.parse_args()

    repo = Path.cwd()
    new_version = _compute_version(repo, args.version)
    branch = f"chore/bump-v{new_version}"

    # Configurazione git minima per ambienti CI
    actor = os.environ.get("GITHUB_ACTOR") or "github-actions[bot]"
    email = os.environ.get("GITHUB_ACTOR_EMAIL") or "github-actions[bot]@users.noreply.github.com"
    _run(["git", "config", "user.name", actor], cwd=repo, check=False)
    _run(["git", "config", "user.email", email], cwd=repo, check=False)

    pyproject = repo / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    new_text, n = PYPROJECT_RE.subn(
        lambda m: f"{m.group('prefix')}{new_version}{m.group(3)}", text, count=1
    )
    if n == 0:
        sys.stderr.write("Nessuna riga 'version = ...' trovata in pyproject.toml\n")
        return 1
    pyproject.write_text(new_text, encoding="utf-8")

    # Verifica se ci sono modifiche effettive
    status = _run(["git", "status", "--short", "pyproject.toml"], cwd=repo)
    if not status.strip():
        print("Versione corrente gia aggiornata: nessuna modifica da committare.")
        return 0

    if args.dry_run:
        print(f"DRY-RUN: branch={branch} version={new_version}")
        return 0

    _run(["git", "checkout", "-B", branch, args.base], cwd=repo)
    _run(["git", "add", "pyproject.toml"], cwd=repo)
    _run(
        [
            "git",
            "commit",
            "-m",
            f"chore(release): bump version to {new_version}",
        ],
        cwd=repo,
    )

    # Push: richiede che il token GITHUB_TOKEN abbia permessi di push.
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.stderr.write("GITHUB_TOKEN non impostato: impossibile pushare.\n")
        return 2

    push_url = f"https://x-access-token:{token}@github.com/{os.environ.get('GITHUB_REPOSITORY', '')}.git"
    _run(["git", "push", push_url, branch, "--force"], cwd=repo)

    # Apri PR via gh CLI
    title = f"chore(release): bump version to {new_version}"
    body = (
        f"Automated version bump after release.\n\n"
        f"- New version: `{new_version}`\n"
        f"- Source: `pyproject.toml`\n\n"
        f"This PR is opened by the `release-bump` workflow and should be "
        f"merged to advance the version on master before the next release."
    )
    _run(
        ["gh", "pr", "create", "--base", args.base, "--head", branch, "--title", title, "--body", body],
        cwd=repo,
    )
    print(f"PR aperta per bump {new_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
