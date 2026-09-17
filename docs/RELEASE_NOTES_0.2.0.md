# RelicToEpub 0.2.0 — Note di rilascio

Data: 2026-01-22

Questa release aggiunge il **supporto dual-mode dell'installer Windows**:
l'utente puo' scegliere tra installazione **per-user (senza UAC)** e
**per-machine (admin)**, direttamente dal wizard. Si chiude
l'issue #29 (per-user installer).

---

## Novita' principali

### Installer dual-mode (issue #29)

L'installer ora offre due modalita' di installazione, selezionabili
nel wizard:

| Modalità | Path | Privilegi | Registro | Pandoc MSI |
|----------|------|-----------|----------|------------|
| **per-user** (consigliata) | `%LOCALAPPDATA%\Programs\RelicToEpub` | nessuno | HKCU | skippato (pypandoc) |
| **per-machine** (legacy) | `%ProgramFiles%\RelicToEpub` | admin | HKLM | installato |

Implementazione tecnica:

- `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=dialog`
  consentono all'installer di partire **senza UAC** e mostrarlo solo
  quando l'utente sceglie effettivamente per-machine.
- `DefaultDirName={code:GetDefaultDirName}` aggiorna dinamicamente il
  path suggerito in base alla scelta.
- `[Registry]` ha clausole `Check: InstallUsesMachineHive` /
  `Check: InstallUsesUserHive` per scrivere le chiavi di disinstall
  nella giusta hive (HKLM vs HKCU).
- `[Run]` per pandoc MSI ha `Check: PandocNeededAndMachineInstall` —
  salta il `msiexec.exe` per install per-user (l'MSI richiede admin).
- `AppMutex` viene aggiornato a runtime (`SetSetupSetting`) per
  includere lo scope, evitando collisioni tra installazioni per-user e
  per-machine in flight.
- Nuova pagina wizard `InstallModePage` (inserita dopo wpWelcome) con
  due `TNewRadioButton` ("Installa solo per me" / "Installa per tutti
  gli utenti") e descrizioni di aiuto.

### Varianti dedicate dell'installer

E' possibile buildare varianti specializzate passando
`/DInstallScope=<value>` a ISCC:

| Valore | Output | Wizard mostra scelta? |
|--------|--------|------------------------|
| `per-user` | `RelicToEpub-Setup-X.Y.Z-peruser.exe` | no (default per-user) |
| `per-machine` | `RelicToEpub-Setup-X.Y.Z.exe` | no (default per-machine) |
| `either` (default) | `RelicToEpub-Setup-X.Y.Z.exe` | si |

Vedi `BUILD.md` sezione 4c per i dettagli di compilazione.

### Aggiornamenti alla pipeline di build

- `build/build_windows.ps1` accetta la variabile d'ambiente
  `MYAPP_INSTALLSCOPE` e la passa a ISCC come `/DInstallScope=...`.
- Quando `MYAPP_INSTALLSCOPE=per-user`, l'EXE prodotto ha suffisso
  `-peruser` (firmabile separatamente, utile per canali di rilascio
  distinti).
- `installer.iss` ha un nuovo `#define InstallScope` con default
  `"either"` (wizard chiede all'utente).

---

## Cosa cambia per l'utente finale

### Caso tipico: utente con account standard

- **Prima (0.1.x)**: doppio click sull'installer → prompt UAC
  "Vuoi autorizzare questa app ad apportare modifiche al dispositivo?"
  → l'utente preme "No" → installazione abortita.
- **Adesso (0.2.0)**: doppio click → wizard mostra pagina "Modalità
  di installazione" → seleziona "Installa solo per me" (default) →
  nessun prompt UAC → installazione completa in `%LOCALAPPDATA%`.

### Caso tipico: amministratore di rete / laboratorio

- L'installazione per-user e' ideale per PC kiosk, aule scolastiche,
  account standard con policy UAC restrittive, RDP da utente senza
  diritti admin.
- Se serve la variante per-machine (es. deployment centralizzato),
  e' ancora disponibile: nella pagina di scelta del wizard, seleziona
  "Installa per tutti gli utenti".

### Casi in cui pandoc MSI veniva skippato

L'installazione per-user salta `msiexec.exe` per pandoc perche'
l'MSI richiede admin. Il bootstrap `RelicToEpubBoot.exe` scarica
automaticamente pandoc in `%LOCALAPPDATA%\RelicToEpub\pandoc\`
usando `pypandoc.download_pandoc()`. La conversione PDF→EPUB non
richiede privilegi in nessun momento.

Se il download fallisce (rete aziendale), la UI mostra un messaggio
chiaro e propone workaround (download manuale + copia in
`%LOCALAPPDATA%\RelicToEpub\pandoc\`, oppure reinstallare la
variante per-machine). Vedi `INSTALL_WINDOWS.md` sezione 6.

---

## Compatibilita' e migrazione

### Da 0.1.x a 0.2.0

L'`AppId` NON e' cambiato (resta `{43C83119-4124-4739-8E56-2E41A922ACAC}`).
La 0.2.0 verra' vista da Windows come **aggiornamento della 0.1.x**
gia' installata (non c'e' bisogno di disinstallare preventivamente).

Tuttavia: se vuoi passare da un'installazione per-machine 0.1.x a
una installazione per-user 0.2.0 (o viceversa), **devi disinstallare
prima** la versione precedente per evitare conflitti di AppMutex e
voci doppie in "App e funzionalita".

### Cache torch / modello OCR

La cache wheel torch (`%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\`)
e il modello OCR (`%LOCALAPPDATA%\RelicToEpub\models\`) sono
indipendenti dallo scope di installazione e vengono condivisi
automaticamente se esistono.

### Pandoc

L'installazione per-user non installa pandoc globalmente. Se usi
anche altre applicazioni che si appoggiano al pandoc globale,
installa manualmente da https://pandoc.org o usa una variante
per-machine.

---

## Bug fix

- L'installer per-user non fallisce piu' con `IPersistFile::Save
  failed 0x80070005` perche' tutte le icone desktop puntano a
  `{userdesktop}` (scrivibile senza admin), e il path di installazione
  e' in `%LOCALAPPDATA%\Programs` (scrivibile senza admin).
- La cache wheel torch e il modello OCR non sono mai in `%ProgramFiles%`
  (l'utente non potrebbe scriverci), quindi l'app non rompe se
  installata per-user.

---

## Cosa NON e' cambiato

- Il **formato dei PDF/EPUB in output** e' identico.
- La **UI Gradio** e la **CLI** non hanno modifiche visibili.
- Le **dipendenze Python** sono identiche (PyInstaller produce gli
  stessi `_internal/`).
- Le **performance di torch** sono identiche (stesso wheel installato).
- L'`AppId` UUID resta `{43C83119-4124-4739-8E56-2E41A922ACAC}`.

---

## Testing

Nuovo file `tests/test_installer_iss.py` con ~20 test che validano
la struttura del file `installer.iss`:

- Header comment descrive il supporto dual-mode
- `[Setup]` ha `PrivilegesRequired=lowest` e `DefaultDirName={code:GetDefaultDirName}`
- `[Registry]` ha voci simmetriche HKLM/HKCU con `Check: InstallUsesMachineHive`/`InstallUsesUserHive`
- `[Run]` pandoc MSI ha `Check: PandocNeededAndMachineInstall`
- `[Code]` contiene `GetDefaultDirName`, `InstallUsesMachineHive`,
  `InstallUsesUserHive`, `InstallModePage`, `InitializeWizard`,
  `CurPageChanged`, `UpdateAppMutexForScope`
- `DefaultInstallScope` rispetta il valore di `{#InstallScope}`
- `#ifndef InstallScope` con default `"either"`
- `OutputBaseFilename` rispetta il macro override

Esecuzione: `py -3 -m pytest tests/test_installer_iss.py`.

---

## Prossima release (0.2.1, ipotesi)

- Authenticode code signing (se arrivera' un certificato).
- Aggiornamenti automatici via `scripts/update_check.py` (fondamenta
  introdotte in questa release; UI notification ancora da implementare).
- Telemetria opzionale per tasso di successo install/upgrade (in attesa
  di privacy review).
- Migrazione automatica per-user → per-machine (e viceversa) senza
  disinstallazione manuale.
