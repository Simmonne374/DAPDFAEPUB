# RelicToEpub 0.1.1 — Note di rilascio

Data: 2026-01-15

Questa release rende l'installer Windows **notevolmente piu robusto**
in tutti i casi limite che la 0.1.0 gestiva male: GPU vecchia,
download fallito, installazione interrotta, cambio versione torch,
spazio disco insufficiente.

---

## Attenzione: AppId e' cambiato

`AppId` Inno Setup e' passato da `{A1B2C3D4-E5F6-7890-ABCD-1234567890AB}`
(placeholder di sviluppo) a `{43C83119-4124-4739-8E56-2E41A922ACAC}` (UUID v4 reale).

**Cosa significa per te, utente:**

- L'installer 0.1.1 verra' visto da Windows come un **prodotto
  separato** rispetto alla 0.1.0. Non verra' proposto l'upgrade.
- Per passare dalla 0.1.0 alla 0.1.1 **devi disinstallare** la 0.1.0
  da Impostazioni → App → RelicToEpub → Disinstalla, e poi installare
  la 0.1.1 da zero.
- La cache wheel torch (`%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\`)
  e il modello OCR (`%LOCALAPPDATA%\RelicToEpub\models\`) **vengono
  conservati** e riutilizzati automaticamente — non devi riscaricare
  ~7 GB.

> Se la 0.1.0 era stata installata in un path non standard e la
> disinstallazione fallisce, vedi `INSTALL_WINDOWS.md` sezione 5
> per la procedura di pulizia manuale (incluso il comando `reg delete`
> con il nuovo GUID).

---

## Novita' principali

### Bootstrap GPU-aware piu' robusto (`gpu_bootstrap.py`)

- **Maxwell SM 5.x supportate** (GTX 750/750 Ti/9xx). La 0.1.0
  rifiutava queste GPU e cadeva su CPU; la 0.1.1 installa il wheel
  CUDA 11.8 (ultimo cu* che supporta Maxwell EOL) — performance
  notevolmente migliori.
- **Fallback automatico a CPU** dopo 3 download consecutivi falliti
  (proxy aziendale, mirror non raggiungibile). La 0.1.0 abortiva con
  un errore generico. Ora l'app si avvia comunque e l'utente vede il
  messaggio "GPU rilevata ma download fallito: installata build CPU"
  nello splash + log diagnostico in `launcher_selfcheck.log`.
- **Confronto path case-insensitive** in `_check_install_path`.
  Su NTFS "C:\Program Files" e "c:\PROGRAM FILES" collidono;
  la 0.1.0 generava falsi warning di "installazione spostata".
- **Cache wheel versionata**: la cache ora vive in
  `torch_wheel_cache\torch-<versione>\`. Un upgrade a torch 2.5 non
  sovrascrive piu' il wheel 2.4 (utile se per qualche motivo serve
  rollback). Le cache di versioni diverse vengono lasciate sul disco
  e loggate in `launcher_selfcheck.log` (auto-purge non distruttivo).

### Installer Inno Setup piu' solido (`installer.iss`)

- **Reale AppId UUID v4** (vedi sezione precedente).
- **Filename MSI pandoc configurabile**: `#define PandocMsi` con
  override ISCC `/DPandocMsi=<file>`. La 0.1.0 aveva l'MSI hardcoded.
- **Warning spazio disco**: `ExtraDiskSpaceRequired=3221225472` (3 GB).
  Windows ora mostra "Required: 3.0 GB, Available: X GB" prima di
  iniziare, evitando fallimenti a meta' installazione.
- **Sentinel installazione interrotta** in
  `%LOCALAPPDATA%\RelicToEpub\install.inprogress`. Se rilanci
  l'installer dopo un crash/power loss, vedi una pagina di warning
  con opzione "Disinstalla e reinstalla" / "Prosegui comunque".

### Test automatici (`tests/`)

Prima copertura pytest per i moduli launcher:

- `test_launcher_progress_state.py` — 14 test (atomic write, defaults,
  corrupt JSON, custom path, monotonic timestamps).
- `test_launcher_bootstrap_helpers.py` — 25 test (parametrized
  decision table per ogni SM, fallback paths, versioned cache).
- `test_launcher_selfcheck.py` — 8 test (validation path,
  registry mismatch con winreg mockato, edge cases).

Totale: **47 test passing** per i moduli che prima erano zero-covered.

Esecuzione: `py -3 -m pytest tests/test_launcher_*.py`.

---

## Bug fix minori

- `_check_install_path` ora usa `Path.resolve()` con confronto
  case-insensitive esplicito (era string-comparison fragile su
  Windows per via di case e junction point).
- `find_cached_wheel` ora usa il pattern `torch-*+<tag>-*.whl`
  (era `-<tag>-`, non matchava i wheel reali).
- `_log_selfcheck` sopravvive a path `LOCALAPPDATA` non scrivibili
  (es. account limitato che tenta di scrivere in System32).

---

## Cosa NON e' cambiato

- Il **formato dei PDF/EPUB in output** e' identico.
- La **UI Gradio** e la **CLI** non hanno modifiche visibili.
- Le **dipendenze Python** sono identiche (PyInstaller produce gli
  stessi `_internal/`).
- Le **performance di torch** sono identiche (stesso wheel installato).

---

## Workaround noti

### Driver NVIDIA troppo vecchi per CUDA 11.8

CUDA 11.8 richiede driver ≥ 452.33. Se hai driver piu' vecchi
(≤ 451.x), il bootstrap cade comunque su cu118 ma potresti vedere
errori a runtime. Aggiorna i driver da
[nvidia.com/drivers](https://www.nvidia.com/drivers).

### Cache wheel di versioni torch diverse

Se in passato hai usato la 0.1.0 (torch 2.4.0) e adesso vuoi provare
un torch diverso, le cache coesistono in sottocartelle separate
(`torch-2.4.0/`, `torch-2.5.0/`, ...). Se lo spazio disco e' un
problema, elimina manualmente la versione che non ti serve:

```cmd
rmdir /s /q "%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\torch-2.4.0"
```

---

## Migrazione rapida dalla 0.1.0

1. **Disinstalla** la 0.1.0 da Impostazioni → App.
2. (Opzionale) **Conserva cache e modello OCR**: durante la
   disinstallazione, **non** spuntare "Rimuovi anche cache e
   modello OCR" se vuoi evitare di riscaricare ~7 GB.
3. **Scarica** `RelicToEpub-Setup-0.1.1.exe` (~3 GB).
4. **Installa** con diritti di amministratore.
5. **Riavvia** la UI: il bootstrap riconosce la cache esistente e
   salta il download di torch (max 3 s).

---

## Prossima release (0.1.2, ipotesi)

- Authenticode code signing (se arrivera' un certificato).
- Installazione per-user (rimozione UAC prompt) — refactor grande,
  richiede design pass separato.
- Telemetria opzionale per tasso di successo install/upgrade
  (in attesa di privacy review).
