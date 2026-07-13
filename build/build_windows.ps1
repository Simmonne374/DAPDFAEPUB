# build_windows.ps1 — pipeline di build per l'installer Windows.
#
# Fasi:
#  1. Genera build/icon.ico se manca
#  2. Esegue PyInstaller per il bootstrap GPU-aware → dist/boot
#  3. Esegue PyInstaller per l'app vera (UI + CLI) → dist/RelicToEpub/
#  4. Copia il bootstrap in dist/RelicToEpub/ (sarà l'entry-point)
#  5. Lancia Inno Setup Compiler (ISCC.exe) → RelicToEpub-Setup-<ver>.exe
#
# Requisiti sulla macchina di build:
#  - Python 3.11 installato e raggiungibile come `py` (Microsoft Store) o `python`
#  - Pacchetti installati: pip install -e ".[dev,pkg]"
#  - Inno Setup 6 installato in "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
#
# Variabili d'ambiente opzionali:
#  - PYTHON_BIN  : percorso esplicito di python.exe (default: py -3.11)
#  - ISCC        : percorso esplicito di ISCC.exe
#  - SKIP_INSTALLER : se "1", salta il passo Inno Setup (utile in CI)

[CmdletBinding()]
param(
    [switch]$Clean = $false
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

Write-Host "=== Build RelicToEpub ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"

# --- 0. Determina python ---
$py = $env:PYTHON_BIN
if (-not $py) {
    foreach ($pyVersion in @('3.13', '3.12', '3.11', '3.10')) {
        try {
            $candidate = (& py "-$pyVersion" -c "import sys; print(sys.executable)" 2>$null).Trim()
            if ($candidate -and (Test-Path $candidate)) { $py = $candidate; break }
        } catch {}
    }
}
if (-not $py -or -not (Test-Path $py)) {
    try { $py = (& python -c "import sys; print(sys.executable)" 2>$null).Trim() }
    catch {}
}
if (-not $py -or -not (Test-Path $py)) {
    throw "Impossibile trovare python.exe. Imposta PYTHON_BIN o installa Python 3.11+."
}
Write-Host "Python: $py"

# --- 1. Genera icona ---
$iconPath = Join-Path $ScriptDir "icon.ico"
if (-not (Test-Path $iconPath)) {
    Write-Host "Generazione icona..." -ForegroundColor Yellow
    & (Join-Path $ScriptDir "make_icon.ps1")
}

# --- 2. Pulizia opzionale ---
if ($Clean) {
    Write-Host "Pulizia cartelle dist/build/..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ProjectRoot "dist")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ScriptDir "__pycache__")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ScriptDir "_work_boot")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ScriptDir "_work_app")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ProjectRoot "Output")
}

# --- 3. Build del bootstrap GPU-aware ---
Write-Host ""
Write-Host ">>> [1/4] Building RelicToEpubBoot.exe (GPU bootstrap)..." -ForegroundColor Magenta
Push-Location $ScriptDir
try {
    & $py -m PyInstaller --noconfirm --distpath (Join-Path $ProjectRoot "dist") --workpath (Join-Path $ScriptDir "_work_boot") relictoepub_boot.spec 2>&1 | Tee-Object -FilePath (Join-Path $ProjectRoot "build_boot.log")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller bootstrap fallito (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# --- 4. Build dell'app principale (UI + CLI) ---
Write-Host ""
Write-Host ">>> [2/4] Building RelicToEpub.exe (UI + CLI)..." -ForegroundColor Magenta
Push-Location $ScriptDir
try {
    & $py -m PyInstaller --noconfirm --distpath (Join-Path $ProjectRoot "dist") --workpath (Join-Path $ScriptDir "_work_app") relictoepub.spec 2>&1 | Tee-Object -FilePath (Join-Path $ProjectRoot "build_app.log")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller app fallito (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# --- 5. Il bootstrap è già in dist/boot/ — Inno Setup lo include via
#        direttiva [Files] dedicata (vedi installer.iss). Niente copia.

$appDir = Join-Path $ProjectRoot "dist\RelicToEpub"
$bootDir = Join-Path $ProjectRoot "dist\boot"

if (-not (Test-Path $appDir)) {
    throw "Cartella $appDir non trovata — controlla build_app.log"
}
if (-not (Test-Path $bootDir)) {
    throw "Cartella $bootDir non trovata — controlla build_boot.log"
}

if (-not (Test-Path $appDir)) {
    throw "Cartella $appDir non trovata — controlla build_app.log"
}

# --- 6. Copia pandoc MSI accanto alla app per bundle ---
$pandocSrc = Join-Path $ProjectRoot "pandoc-3.10-windows-x86_64.msi"
$pandocDest = Join-Path $appDir "pandoc-3.10-windows-x86_64.msi"
if ((Test-Path $pandocSrc) -and (-not (Test-Path $pandocDest))) {
    Write-Host "Copiando pandoc MSI per bundling..."
    Copy-Item $pandocSrc $pandocDest
}

# --- 7. Build installer Inno Setup ---
if ($env:SKIP_INSTALLER -eq "1") {
    Write-Host ""
    Write-Host ">>> [4/4] SKIP_INSTALLER=1, salto Inno Setup" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host ">>> [4/4] Building Inno Setup installer..." -ForegroundColor Magenta
    $iscc = $env:ISCC
    if (-not $iscc) {
        $candidates = @(
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                "C:\Program Files\Inno Setup 6\ISCC.exe",
                "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
                "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
                "C:\Program Files\Inno Setup 7\ISCC.exe"
            )
            foreach ($c in $candidates) { if (Test-Path $c) { $iscc = $c; break } }
        }
    if (-not $iscc -or -not (Test-Path $iscc)) {
        Write-Host "ISCC.exe non trovato — installa Inno Setup 6 oppure imposta ISCC. Step saltato." -ForegroundColor Yellow
    } else {
        $issPath = Join-Path $ScriptDir "installer.iss"
        if (-not (Test-Path $issPath)) {
            Write-Host "installer.iss non trovato in $ScriptDir — step saltato." -ForegroundColor Yellow
        } else {
            & $iscc $issPath
            if ($LASTEXITCODE -ne 0) { throw "Inno Setup fallito (exit $LASTEXITCODE)" }
        }
    }
}

Write-Host ""
Write-Host "=== Build completato ===" -ForegroundColor Green
Write-Host "Output installer: $ProjectRoot\Output\RelicToEpub-Setup-*.exe (se Inno Setup presente)"
Write-Host "Output app pura:  $appDir (per test locale)"
