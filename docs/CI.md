# CI / CD

This project ships a GitHub Actions pipeline that automatically rebuilds
the Windows installer on every merge to ``master``.

## Workflow

File: ``.github/workflows/build-windows.yml``

### Trigger

- ``push`` to ``master`` (any merge of a PR into master)
- ``workflow_dispatch`` (manual run from the Actions tab)

### Pipeline steps

1. Checkout the repository (full history, no LFS).
2. Set up Python 3.11 with pip cache.
3. Install build & runtime dependencies (``pip install -e ".[pkg]"``).
4. Install Inno Setup 6 silently into ``C:\InnoSetup6`` (skipped if already present).
5. Run ``build\build_windows.ps1`` (PyInstaller + Inno Setup).
6. Locate the produced ``RelicToEpub-Setup-*.exe``.
7. Upload it as a workflow artifact (30-day retention).
8. Upload build logs as a separate artifact (7-day retention, even on failure).
9. Create / update a **draft** GitHub release tagged ``build-<commit-sha>``
   with the installer attached (one permanent URL per merge).
10. Update a **rolling** release tagged ``latest`` (asset overwritten each merge).

### What you get

- **Artifact**: from the Actions run page, click the run and download
  ``RelicToEpub-Setup`` (the .exe is ~564 MB; artifact expires after 30 days).
- **Per-commit release**: browse the [Releases page](../../releases) and
  find the ``build-<sha>`` tag for the exact merge you want.
- **Rolling release**: the ``latest`` tag always points to the most
  recent successful build of ``master``.

### Requirements / caveats

- The runner is ``windows-latest`` (4 vCPU, 16 GB RAM).
- Build takes 15-40 minutes (PyInstaller + LZMA2 ultra64 compression).
- Workflow timeout is 240 minutes per job.
- Concurrency is configured so a new push cancels the previous one in flight.
- The installer is **unsigned**. Users may see a SmartScreen warning on first run.
- Bandwidth: ~5 GB downloaded per build (PyTorch CUDA wheel ~1.5 GB,
  HuggingFace model ~3 GB, plus PyInstaller + Inno Setup + pandoc MSI).

### Local reproduction

To reproduce the build locally on Windows:

```powershell
git clone https://github.com/Simmonne374/DAPDFAEPUB
cd DAPDFAEPUB
pip install -e ".[pkg]"
.\build\build_windows.ps1
```

The installer lands in ``Output\RelicToEpub-Setup-<version>.exe``.

### Status badge

Add this badge to the top of the README once the workflow has run at least once:

```markdown
[![Build Windows installer](https://github.com/Simmonne374/DAPDFAEPUB/actions/workflows/build-windows.yml/badge.svg)](https://github.com/Simmonne374/DAPDFAEPUB/actions/workflows/build-windows.yml)
```
