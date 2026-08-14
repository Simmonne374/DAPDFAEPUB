"""Genera il changelog Markdown tra due tag git.

Raggruppa i commit per categoria Conventional Commits e li rende in
una forma leggibile per il body della GitHub Release.

Usage:
    python build/scripts/render_changelog.py PREV_TAG NEW_TAG
    python build/scripts/render_changelog.py v0.1.0 v0.1.1

Output: testo Markdown su stdout.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Forza stdout a UTF-8 (Windows apre la console in cp1252 di default).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?P<scope1>\([^)]*\))?(?P<bang>!)?:\s*(?P<subject>.+)$"
)
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:\s", re.MULTILINE | re.IGNORECASE)
# Riconosce il prefisso emoji (e.g. "🐛 ", "✨ ", "🎨 ") tipico di gitmoji.
EMOJI_PREFIX_RE = re.compile(
    r"^[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u27BF]+\s*"
)

CATEGORY_ORDER = [
    ("Features", "feature"),
    ("Bug Fixes", "fix"),
    ("Performance", "perf"),
    ("Reverts", "revert"),
    ("Maintenance", "maintenance"),
]

# Tipi che finiscono in "Maintenance" (in ordine alfabetico nel gruppo).
MAINTENANCE_TYPES = {"chore", "docs", "refactor", "test", "build", "ci", "style"}


@dataclass
class Commit:
    short: str
    full: str
    subject: str
    body: str
    author: str
    category: str  # "feature" | "fix" | "perf" | "revert" | "maintenance"
    breaking: bool
    source: str  # "merge" | "commit"

    @property
    def headline(self) -> str:
        stripped = EMOJI_PREFIX_RE.sub("", self.subject or "").strip()
        m = CONVENTIONAL_RE.match(stripped)
        if not m:
            return self.subject
        ctype = m.group("type") or ""
        bang = "!" if m.group("bang") else ""
        scope = m.group("scope1") or ""
        body = m.group("subject") or ""
        return f"{ctype}{scope}{bang}: {body}".strip()


def _run_git(*args: str, cwd: Path) -> str:
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


def _classify(subject: str, body: str) -> tuple[str, bool]:
    # Strip di un eventuale prefisso emoji (gitmoji) prima del parser.
    stripped = EMOJI_PREFIX_RE.sub("", subject or "").strip()
    m = CONVENTIONAL_RE.match(stripped)
    ctype = m.group("type").lower() if m and m.group("type") else ""
    bang = bool(m and m.group("bang"))
    breaking = bang or bool(BREAKING_FOOTER_RE.search(body or ""))
    if ctype == "feat":
        return ("feature", breaking)
    if ctype == "fix":
        return ("fix", breaking)
    if ctype == "perf":
        return ("perf", breaking)
    if ctype == "revert":
        return ("revert", breaking)
    if ctype in MAINTENANCE_TYPES:
        return ("maintenance", breaking)
    # Commit senza prefisso conventional: lo mostriamo in maintenance.
    return ("maintenance", breaking)


def _list_commits(repo: Path, prev: str | None, current: str) -> list[Commit]:
    rng = f"{prev}..{current}" if prev else current
    sep = "---END-COMMIT---"
    fmt = f"%H%n%h%n%s%n%an%n%b%n{sep}"
    raw = _run_git("log", rng, f"--pretty=format:{fmt}", cwd=repo)
    if not raw:
        return []
    commits: list[Commit] = []
    for chunk in raw.split(sep):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.splitlines()
        if len(lines) < 4:
            continue
        full = lines[0]
        short = lines[1]
        subject = lines[2]
        author = lines[3]
        body = "\n".join(lines[4:]).strip()
        category, breaking = _classify(subject, body)
        source = "merge" if subject.lower().startswith("merge ") else "commit"
        # I merge commit non aggiungono valore al changelog release.
        if source == "merge":
            continue
        commits.append(
            Commit(
                short=short,
                full=full,
                subject=subject,
                body=body,
                author=author,
                category=category,
                breaking=breaking,
                source=source,
            )
        )
    return commits


def _format_commit_line(c: Commit) -> str:
    short = c.short
    headline = c.headline
    line = f"- {headline} ({short})"
    if c.breaking:
        line += " ⚠️ BREAKING"
    return line


def render(repo: Path, prev: str | None, current: str) -> str:
    commits = _list_commits(repo, prev, current)
    if not commits:
        return "_Nessun commit significativo in questo intervallo._"

    buckets: dict[str, list[Commit]] = {
        "feature": [],
        "fix": [],
        "perf": [],
        "revert": [],
        "maintenance": [],
    }
    for c in commits:
        buckets[c.category].append(c)

    sections: list[tuple[str, list[Commit]]] = []
    for title, key in CATEGORY_ORDER:
        if buckets[key]:
            sections.append((title, buckets[key]))

    breaking = [c for c in commits if c.breaking]
    contributors = sorted({c.author for c in commits if c.author})

    out: list[str] = []
    out.append(f"## {current}")
    if prev:
        out.append(f"_Diff completo: `{prev}...{current}`_")
    out.append("")

    if breaking:
        out.append("### ⚠️ Breaking changes")
        out.extend(_format_commit_line(c) for c in breaking)
        out.append("")

    for title, items in sections:
        out.append(f"### {title}")
        out.extend(_format_commit_line(c) for c in items)
        out.append("")

    out.append("---")
    if contributors:
        out.append("_Contributors:_ " + ", ".join(f"@{a}" for a in contributors))
    else:
        out.append("_Nessun contributor._")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prev", nargs="?", help="Tag o SHA precedente (opzionale)")
    parser.add_argument("current", help="Tag o SHA corrente")
    args = parser.parse_args(argv)
    repo = Path.cwd()
    text = render(repo, args.prev, args.current)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())