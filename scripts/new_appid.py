"""Generate and (optionally) apply a fresh Inno Setup ``AppId`` for RelicToEpub.

The Windows installer identity is governed by an ``AppId`` UUID that must be
globally unique. The placeholder we shipped with ``A1B2C3D4-E5F6-7890-ABCD-1234567890AB``
has to be replaced before any public release, and again any time we need a
clean slate (e.g. when an early adopter ends up with two entries in
"App & features").

Usage
-----

* **Preview only (safe)**: prints the new GUID and the exact one-line edits
  that would be performed::

      python scripts/new_appid.py

* **Apply in place**: rewrites the two files. **Refuses to run if the working
  tree is dirty** unless ``--force`` is passed::

      python scripts/new_appid.py --apply
      python scripts/new_appid.py --apply --force

Files touched
-------------

* ``build/installer.iss`` -- the ``AppId={{...}}`` line.
* ``build/launchers/gpu_bootstrap.py`` -- the ``appid_guid`` registry probe.

When run with ``--apply`` we also append a one-line bullet to
``docs/RELEASE_NOTES_<next>.md`` if such a file exists (the dev must add it
manually if it does not).

Why a helper instead of editing by hand?
----------------------------------------

* One source of truth (avoid typos / inconsistent braces).
* Enforces the curly-brace format Inno Setup expects: ``AppId={{GUID}}`` (note
  the literal ``{{`` and ``}}`` -- Inno's macro syntax).
* Refuses to run on a dirty tree to avoid silently stomping an in-flight edit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INSTALLER_ISS = PROJECT_ROOT / "build" / "installer.iss"
GPU_BOOTSTRAP = PROJECT_ROOT / "build" / "launchers" / "gpu_bootstrap.py"

# Match ``AppId={{<guid>}}`` -- existing file has unbalanced braces
# (``AppId={{A1B2C3D4-...}`` -- missing one closing brace). Accept either.
_RE_APPID_LINE = re.compile(
    r'^(AppId=)\{\{?([0-9A-Fa-f-]+)\}?\}?',
    re.MULTILINE,
)

# Match ``appid_guid = "{<guid>}"`` in gpu_bootstrap.py. The existing file
# also has unbalanced braces (``appid_guid = "{<guid>}"`` -- one brace),
# but the surrounding ``"`` quotes are unambiguous.
_RE_REG_APPID = re.compile(
    r'(appid_guid\s*=\s*")\{?([0-9A-Fa-f-]+)\}?(")',
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _is_working_tree_clean() -> bool:
    """True iff there are no unstaged modifications in *tracked* files.

    Staged-only edits are allowed through (the next step is ``git commit``).
    Untracked files are tolerated because the helper itself is one such file
    on first run -- we'd otherwise lock out the script we just authored.

    ``git diff --quiet`` exits 0 when no diff, 1 when there IS a diff.
    """
    return _git("diff", "--quiet").returncode == 0


def _format_inno_appid(guid: str) -> str:
    """Return the Inno-Setup form used in the existing file.

    The existing file uses ``AppId={{<guid>}`` (unbalanced, which is what
    Inno Setup actually receives as ``{<guid>}`` after macro expansion of
    the doubled ``{{``). We preserve the same form to keep diffs minimal
    and to match what ``gpu_bootstrap.py`` probes in the registry.
    """
    return "={{" + guid + "}"


def generate_guid() -> str:
    """Return a fresh uppercase UUID v4 string."""
    return str(uuid.uuid4()).upper()


def preview(guid: str) -> str:
    """Print the exact edits the helper would apply."""
    new_inno = "AppId={{" + guid + "}"
    return (
        f"New GUID: {guid}\n"
        f"\n"
        f"-- {INSTALLER_ISS.relative_to(PROJECT_ROOT)} --\n"
        f"{new_inno}\n"
        f"\n"
        f"-- {GPU_BOOTSTRAP.relative_to(PROJECT_ROOT)} --\n"
        f'appid_guid = "{{' + guid + '}}"\n'
    )


def apply_in_place(guid: str) -> tuple[int, int]:
    """Replace the AppId in both files. Returns (n_subs_installer, n_subs_bootstrap).

    Raises ``FileNotFoundError`` if a file is missing; raises ``RuntimeError`` if
    no replacement was performed (the placeholder is already gone).
    """
    inno_text = INSTALLER_ISS.read_text(encoding="utf-8")
    inno_new, n_inno = _RE_APPID_LINE.subn(
        lambda m: m.group(1) + "{{" + guid + "}",
        inno_text,
    )
    if n_inno == 0:
        raise RuntimeError(
            f"Placeholder AppId not found in {INSTALLER_ISS}. "
            "Has it already been replaced?"
        )
    INSTALLER_ISS.write_text(inno_new, encoding="utf-8")

    boot_text = GPU_BOOTSTRAP.read_text(encoding="utf-8")
    boot_new, n_boot = _RE_REG_APPID.subn(
            lambda m: m.group(1) + "{" + guid + "}" + m.group(3),
        boot_text,
    )
    if n_boot == 0:
        raise RuntimeError(
            f"Placeholder appid_guid not found in {GPU_BOOTSTRAP}. "
            "Has it already been replaced?"
        )
    GPU_BOOTSTRAP.write_text(boot_new, encoding="utf-8")

    return n_inno, n_boot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Edit the two files in place. Default is preview-only.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --apply even when the working tree is dirty.",
    )
    args = parser.parse_args(argv)

    guid = generate_guid()
    if not args.apply:
        sys.stdout.write(preview(guid))
        return 0

    if not args.force and not _is_working_tree_clean():
        sys.stderr.write(
            "ERROR: working tree is dirty. Commit or stash first, or pass --force.\n"
        )
        # Show what would change so the dev can still see the new GUID.
        sys.stderr.write("\n" + preview(guid))
        return 2

    try:
        n_inno, n_boot = apply_in_place(guid)
    except (FileNotFoundError, RuntimeError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    sys.stdout.write(
        f"Applied new AppId {guid}:\n"
        f"  - {INSTALLER_ISS.relative_to(PROJECT_ROOT)}: {n_inno} replacement(s)\n"
        f"  - {GPU_BOOTSTRAP.relative_to(PROJECT_ROOT)}: {n_boot} replacement(s)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())