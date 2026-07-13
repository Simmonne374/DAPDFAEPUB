# Build del pacchetto Windows

Questa guida spiega come rigenerare l'installer `RelicToEpub-Setup-0.1.0.exe` da sorgenti.

---

## 1. Toolchain necessaria

| Strumento | Versione | Note |
|-----------|----------|------|
| **Python** | 3.11.x | https://python.org (su Windows usare installer 64 bit) |
| **PyInstaller** | ≥ 6.10 | installato automaticamente dallo script |
| **Inno Setup 6** | ultima stabile | https://jrsoftware.org/isinfo.php — `ISCC.exe` deve essere in `PATH` o in `C:\Program Files (x86)\Inno Setup 6\` |
| **PowerShell** | 5.1+ | già presente in Windows 10/11 |

Facoltativi:

- **Git Bash** — non richiesto (lo script è `.ps1`)
- **Certificato Authenticode** (.pfx) — per firmare l'eseguibile

---

## 2. Preparazione ambiente

```cmd
:: Clona il repo
git clone https://github.com/example/relictoepub.git
cd relictoepub

:: Crea e attiva virtualenv (Python 3.11)
py -3.11 -m venv .venv
.venv\Scripts\activate

:: Installa dipendenze runtime + build
pip install -e ".[pkg]"
```

Verifica installazione:

```cmd
python -c "import PyInstaller; print(PyInstaller.__version__)"
where ISCC.exe
```

Entrambi devono restituire output valido.

---

## 3. Build pipeline

Lo script `build/build_windows.ps1` esegue tutto in automatico:

```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

Cosa fa:

1. Verifica `python` (con fallback a `py -3.11`)
2. Verifica che `PyInstaller` sia installato (`pip install -e .[pkg]` se manca)
3. **Pulisce** `build/` e `dist/`
4. **Build del bootstrap**: `pyinstaller build/relictoepub_boot.spec` → `dist/RelicToEpub/`
5. **Build dell'app**: `pyinstaller build/relictoepub.spec` → due EXE (`RelicToEpubUI.exe` + `RelicToEpubCLI.exe`) nella stessa `_internal/`
6. **Copia** `RelicToEpubBoot.exe` accanto agli altri due
7. **Lancia ISCC**: `ISCC.exe build/installer.iss` → `Output\RelicToEpub-Setup-0.1.0.exe`

### Output finale

```
Output\
└── RelicToEpub-Setup-0.1.0.exe    (circa 3 GB)

dist\
└── RelicToEpub\
    ├── RelicToEpubBoot.exe        (bootstrap GPU-aware, ~30 MB)
    ├── RelicToEpubUI.exe          (~80 MB)
    ├── RelicToEpubCLI.exe         (~80 MB)
    ├── _internal\                 (Python + dipendenze, no torch)
    ├── icon.ico
    └── ... (DLL, .pyd, ecc.)
```

---

## 4. Struttura dei file di build

| File | Ruolo |
|------|-------|
| `build/relictoepub_boot.spec` | PyInstaller spec per il bootstrap GPU-aware (piccolo, solo pynvml + tkinter + requests) |
| `build/relictoepub.spec` | PyInstaller spec per app UI + CLI (un COLLECT condiviso, no torch) |
| `build/hooks/hook-relictoepub.py` | Hook PyInstaller: hidden imports per unlimited_ocr, gradio_app, ecc. |
| `build/launchers/gpu_bootstrap.py` | Logica di detect GPU + download wheel torch |
| `build/launchers/gpu_splash.py` | UI Tkinter per il bootstrap (progress visibile) |
| `build/launchers/progress_state.py` | IPC JSON tra gpu_bootstrap e gpu_splash |
| `build/launchers/launch_ui_launcher.py` | Wrapper UI: redirige stdout, logga, lancia Gradio |
| `build/launchers/launch_cli_launcher.py` | Wrapper CLI: redirige stdout, propaga exit code |
| `build/icon.ico` | Icona app (16/32/48/64/128/256 px) |
| `build/make_icon.ps1` | Rigenera `icon.ico` da zero via System.Drawing |
| `build/installer.iss` | Script Inno Setup con status page esplicite |
| `build/build_windows.ps1` | Pipeline completa |

---

## 5. Decisioni di design rilevanti

### torch NON è bundleato nell'installer
Il wheel torch viene **scaricato a runtime** dal bootstrap, in base alla GPU rilevata. Vantaggi:

- Installer più snello (~3 GB invece di ~6 GB)
- Un unico installer per tutti i tipi di GPU (Pascal/Ampere/Hopper/Blackwell + CPU)
- Cache wheel persistente in `%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\`

Trade-off: primo avvio richiede internet (download ~1.5 GB). Documentato in `INSTALL_WINDOWS.md`.

### PyInstaller `--onedir` invece di `--onefile`
- `--onefile`: estrae ~3 GB in `%TEMP%` a ogni avvio (10-30 s di attesa ogni volta)
- `--onedir`: avvio <2 s, ma l'installer distribuisce una cartella (cosa che Inno Setup fa comunque)

### due EXE dallo stesso COLLECT
`relictoepub.spec` produce `RelicToEpubUI.exe` e `RelicToEpubCLI.exe` condividendo un singolo `_internal/`. Questo evita di duplicare ~80 MB di Python + deps per ogni entry point.

### Wisth progress visibile (no silent ops)
- Inno Setup: `CurStepChanged` aggiorna `WizardForm.StatusLabel.Caption` con fasi esplicite
- Bootstrap: `state.json` IPC con progress bar + velocità + ETA nel splash Tkinter
- Modello OCR: `gr.Progress(track_tqdm=True)` con byte counter live

Ogni operazione >2 s ha un feedback visibile. Mai silent stalls.

---

## 6. Personalizzazione

### Cambiare versione
Modifica `__version__` in `src/relictoepub/__init__.py` e `MyAppVersion` in `build/installer.iss`. Il nome dell'installer cambia automaticamente.

### Cambiare l'icona
```powershell
powershell -ExecutionPolicy Bypass -File build\make_icon.ps1
```

Lo script rigenera `build/icon.ico` (multi-resolutione 16-256 px). Sostituisci con il tuo .ico mantenendo le risoluzioni standard Windows.

### Aggiungere un nuovo entry point
1. Crea `build/launchers/launch_xxx_launcher.py`
2. Aggiungi un blocco `EXE(...)` in `build/relictoepub.spec`
3. Aggiungi `[Icons]` in `build/installer.iss`

---

## 7. Code signing (opzionale)

Per evitare SmartScreen warnings, firma gli eseguibili:

```powershell
$pfx = "C:\path\to\cert.pfx"
$password = Read-Host -AsSecureString

Set-AuthenticodeSignature -FilePath "dist\RelicToEpub\RelicToEpubUI.exe" -Certificate (Get-PfxCertificate -FilePath $pfx -Password $password)
Set-AuthenticodeSignature -FilePath "dist\RelicToEpub\RelicToEpubCLI.exe" -Certificate (Get-PfxCertificate -FilePath $pfx -Password $password)
Set-AuthenticodeSignature -FilePath "dist\RelicToEpub\RelicToEpubBoot.exe" -Certificate (Get-PfxCertificate -FilePath $pfx -Password $password)

:: Per firmare l'installer, usa signtool.exe (in Windows SDK)
& "C:\Program Files (x86)\Windows Kits\10\bin\<ver>\x64\signtool.exe" sign /fd SHA256 /a /tr http://timestamp.digicert.com Output\RelicToEpub-Setup-0.1.0.exe
```

> **Nota**: il certificato Authenticode per firmare **.exe** costa ~200 USD/anno (CertCentral, DigiCert, ecc.). Alternativa gratuita: usa il certificato **self-signed** (non rimuove warning, ma lo attenua).

---

## 8. Testing

### Smoke test del bootstrap
```cmd
cd dist\RelicToEpub
RelicToEpubBoot.exe
```
Deve:
- Mostrare splash Tkinter
- Rilevare/no GPU in <3 s
- Se GPU: scaricare + installare wheel torch
- Poi lanciare `RelicToEpubUI.exe`

### Smoke test CLI
```cmd
RelicToEpubCLI.exe --help
```

### Test su macchina senza GPU NVIDIA
Il bootstrap deve:
- Mostrare splash per 2-3 s
- Saltare il download wheel (usa CPU)
- Lanciare comunque l'app

### Test offline (no internet)
Il bootstrap deve:
- Rilevare che non c'è cache wheel
- Provare il download → fallire
- Mostrare messaggio chiaro all'utente (no crash silenzioso)

### Test su GPU non supportata
Es. SM 5.x (Maxwell, GTX 9xx):
- Bootstrap deve rilevare compute capability fuori range
- Fallire in modo controllato con messaggio "GPU non supportata, fallback CPU"
- Installare torch CPU-only

---

## 9. Pulizia build

```powershell
Remove-Item -Recurse -Force build, dist, Output
```

Oppure usa il flag `--clean`:
```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1 -Clean
```

---

## 10. CI/CD (idea)

Per build automatico su GitHub Actions / AppVeyor:

```yaml
- name: Setup Python
  uses: actions/setup-python@v5
  with:
    python-version: '3.11'

- name: Install Inno Setup
  run: choco install innosetup -y

- name: Build
  run: powershell -File build\build_windows.ps1

- name: Upload artifact
  uses: actions/upload-artifact@v4
  with:
    name: relictoepub-setup
    path: Output\RelicToEpub-Setup-0.1.0.exe
```

> **Tempo atteso**: ~10-15 min (PyInstaller è lento, specialmente la prima volta che estrae tutte le deps).

---

## 11. Troubleshooting

### "PyInstaller ImportError: hidden import 'unlimited_ocr' not found"
Aggiungi a `build/hooks/hook-relictoepub.py`:

```python
hiddenimports += ['unlimited_ocr', 'unlimited_ocr.transformer']
```

### "torch._C not found" al primo avvio
Il bootstrap non ha scaricato/il wheel corretto. Controlla:
- `get-childitem $env:LOCALAPPDATA\RelicToEpub\torch_wheel_cache`
- `%LOCALAPPDATA%\RelicToEpub\logs\gpu_bootstrap.log`

### "RecursionError: maximum recursion depth exceeded" durante build
PyInstaller ha bisogno di più stack. Aggiungi in cima al .spec:
```python
import sys
sys.setrecursionlimit(10000)
```

### Inno Setup non trovato
Aggiungi `C:\Program Files (x86)\Inno Setup 6` al PATH, oppure passa il path completo:
```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
```
