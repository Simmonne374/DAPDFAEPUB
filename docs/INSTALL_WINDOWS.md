# Installazione su Windows

Questa guida spiega come installare **RelicToEpub** su Windows tramite l'installer EXE.

---

## 1. Download

Scarica l'installer `RelicToEpub-Setup-0.1.1.exe` (~3 GB). È un singolo file che contiene:

- L'intera applicazione (Python 3.11 + dipendenze, **escluso** torch)
- L'MSI di **pandoc 3.10** (verrà installato in automatico come dipendenza; il filename dell'MSI è configurabile via `/DPandocMsi=` al momento del build, vedi `BUILD.md`)
- Il bootstrap GPU-aware che installerà la build torch corretta al primo avvio

> **Requisito di sistema**: Windows 10 o 11, architettura **x64**. Servono almeno **3 GB liberi** durante l'installazione (l'installer avvisa se lo spazio è insufficiente) e diritti di amministratore per installare pandoc.

---

## 2. Installazione

1. Doppio click su `RelicToEpub-Setup-0.1.1.exe`.
2. Se Windows SmartScreen lo blocca: **"Altre informazioni → Esegui comunque"**. L'installer non è firmato digitalmente in questa versione.
3. Scegli:
   - La **cartella di installazione** (default: `C:\Program Files\RelicToEpub`)
   - Se creare un'icona **sul desktop**
   - Se creare voci **in Start Menu** (default: sì)
4. Clicca **Installa**.
5. **Cosa vedi durante l'installazione**:
   - Una barra di progresso principale avanza durante l'estrazione dei file
   - Sotto, una **status label** mostra le fasi:
     - "Estrazione componenti applicazione in corso — Attendere prego"
     - "Installazione dipendenza esterna (pandoc) — Attendere prego"
     - "Configurazione finale (Start Menu, registro) — Quasi terminato"
6. Al termine potrai scegliere se aprire la cartella di installazione.
7. Da Start Menu (o desktop) troverai:
   - **RelicToEpub UI** — interfaccia Gradio (apre il browser predefinito)
   - **RelicToEpub (CLI)** — utility terminale one-shot

> ⚠️ **Importante**: **non chiudere** l'installer pensando sia bloccato. Ogni fase mostra cosa sta succedendo. La fase 2 (pandoc MSI) richiede 10-30 s.

---

## 3. Primo avvio (GPU bootstrap)

Al primo avvio della **UI** (o CLI) parte un piccolo programma intermedio (`RelicToEpubBoot.exe`, ~30 MB) che:

1. **Rileva la GPU** del sistema tramite `nvidia-smi` e `pynvml`
2. **Sceglie la build torch corretta** per il tuo hardware:
   | GPU | Compute capability | CUDA wheel | Dimensione |
   |-----|-------------------|------------|------------|
   | NVIDIA Maxwell (GTX 7xx-9xx) | 5.0/5.2/5.3 | CUDA 11.8 | ~1.5 GB |
   | NVIDIA GTX 10xx (Pascal), Tesla V (Volta) | 6.x, 7.x | CUDA 11.8 | ~1.5 GB |
   | RTX 20xx (Turing), RTX 30xx (Ampere), A100 | 7.5, 8.x, 9.x | CUDA 12.4 | ~1.5 GB |
   | RTX 50xx, B100 (Blackwell) | 10.x, 12.x | CUDA 12.6 | ~1.5 GB |
   | Altra / Nessuna NVIDIA GPU | — | CPU fallback | ~200 MB |
3. **Scarica e installa** il wheel torch da `download.pytorch.org`
4. Poi lancia `RelicToEpubUI.exe` (Gradio) o `RelicToEpubCLI.exe`

### Cosa vedrai durante il bootstrap

Una finestra Tkinter con titolo **"RelicToEpub — primo avvio in corso"** mostra:

- Label fase corrente ("Rilevamento hardware…", "Download torch wheel…", "Installazione torch…")
- Barra progress con **byte scaricati / totali**
- Velocità in **MB/s** ed **ETA stimato**

Se per qualche motivo il download si ferma >10s, il label diventa **"L'operazione sta procedendo, attendere prego"** (è solo un messaggio anti-falso-allarme, NON un errore).

### Tempi attesi

| Hardware | Primo avvio | Successivi |
|----------|-------------|------------|
| PC con GPU NVIDIA RTX serie 30/40 | 30-90 s | < 3 s |
| PC con GPU NVIDIA Maxwell (GTX 9xx) | 30-60 s | < 3 s |
| PC con GPU NVIDIA GTX 16/10 | 45-90 s | < 3 s |
| PC senza GPU NVIDIA | 5-10 s | < 3 s |
| Connessione lenta (5 Mbps) | fino a 5 min | < 3 s |

La cache wheel persiste in `%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\torch-<versione>\`
(una sottocartella per ogni versione di torch: gli avvii successivi sono
istantanei e un cambio di versione non sovrascrive la cache precedente).

---

## 4. Download modello Unlimited-OCR

Anche dopo l'installazione di torch, serve **scaricare il modello di OCR** (~6 GB) da Hugging Face. Verrà chiesto nella **tab "Modello"** dell'interfaccia Gradio:

1. Apri **RelicToEpub UI**
2. Vai alla scheda **Modello**
3. Clicca **"Scarica modello ora"**
4. Apparirà una barra di progresso con:
   - File attualmente in download
   - Byte scaricati / totali
   - Velocità (MB/s) ed ETA

> Se hai un token HuggingFace privato (per modelli gated), impostalo come variabile d'ambiente `HF_TOKEN` prima di lanciare l'app.

---

## 5. Disinstallazione

Da **Impostazioni → App → RelicToEpub → Disinstalla**, oppure da Start Menu → **Disinstalla RelicToEpub**.

Vengono rimossi automaticamente:

- Tutti i file in `Program Files\RelicToEpub` (eseguibili, `_internal\` con Python
  + dipendenze, MSI di pandoc, log, file temporanei e la cartella di
  installazione stessa se vuota)
- Icone sul **desktop dell'utente corrente**
- Voci nel menu Start (programma, CLI, voce "Disinstalla")
- **Chiavi di registro** di disinstallazione (HKLM/HKCU)
- **Shortcut orfani residui** da installazioni precedenti (es. link rimasti
  in Start Menu che puntavano a unita rimovibili ora scollegate —
  vedi sezione 6 per dettagli)

Opzionalmente, **durante l'installazione iniziale** puoi spuntare la voce
**"Rimuovi anche cache e modello OCR (~7 GB in AppData)"**. Quando attiva,
la disinstallazione elimina anche:

- Cache wheel torch (`%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\`)
- Modello OCR scaricato (`%LOCALAPPDATA%\RelicToEpub\models\`)
- Log diagnostici (`%LOCALAPPDATA%\RelicToEpub\logs\`)

> **Nota**: la cache wheel (~1.5 GB) e il modello OCR (~6 GB) sono
> riutilizzati se reinstalli l'app. Rimuovili solo se vuoi liberare
> spazio disco in modo aggressivo.

**Non** viene rimosso:

- **pandoc** — resta installato a livello di sistema tramite il suo MSI,
  perche puo servire ad altre applicazioni. Per disinstallarlo:
  ```cmd
  msiexec /x {97D20B66-CE32-4AAB-83A9-674A1B6F1C8F}
  ```

### Procedura manuale di "pulizia profonda"

Se dopo la disinstallazione trovi ancora residui (es. per via di un
install corrotto), esegui:

```cmd
:: Cartella installazione (solo se diversa da quella che abbiamo appena cancellato)
rmdir /s /q "%ProgramFiles%\RelicToEpub"

:: Cache, log, modello (se hai saltato il task "removecache")
rmdir /s /q "%LOCALAPPDATA%\RelicToEpub"

:: Start Menu (in rari casi non si svuota automaticamente)
rmdir /s /q "%AppData%\Microsoft\Windows\Start Menu\Programs\RelicToEpub"

:: Icone desktop se rimaste
del /q "%USERPROFILE%\Desktop\RelicToEpub.lnk"
del /q "%USERPROFILE%\Desktop\RelicToEpub (CLI).lnk"

:: Registro (se l'uninstall ha fallito a meta).
:: L'AppId e' un UUID generato; quello mostrato qui corrisponde alla 0.1.1.
:: Per la versione attuale consulta build/installer.iss.
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{43C83119-4124-4739-8E56-2E41A922ACAC}_is1" /f
reg delete "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{43C83119-4124-4739-8E56-2E41A922ACAC}_is1" /f
```

### Log diagnostici della disinstallazione

Ogni run di installazione **e** disinstallazione scrive un log in
`%LOCALAPPDATA%\RelicToEpub\logs\`:

| File | Contenuto |
|------|-----------|
| `installer.log` | Installazioni (con warning su path sospetti, registry) |
| `uninstaller.log` | Disinstallazioni (con elenco shortcut orfani rimossi) |

---

## 5b. Ripristino dopo un'installazione interrotta

A partire dalla **0.1.1**, l'installer scrive un sentinel
(`%LOCALAPPDATA%\RelicToEpub\install.inprogress`) all'inizio della
fase di installazione e lo rimuove al termine con successo. Se un
tentativo precedente e' stato **interrotto** (power loss, crash,
chiusura forzata della finestra), il sentinel resta sul disco.

Al riavvio dell'installer, se il sentinel viene rilevato:

1. Viene mostrata una pagina di warning: *"Rilevata installazione
   precedente interrotta — si raccomanda di disinstallare la versione
   esistente prima di reinstallare."*
2. Sono offerti due pulsanti: **Disinstalla e reinstalla** (esegue
   lo script di uninstall e poi rilancia l'install) e **Prosegui
   comunque** (per utenti esperti che sanno cosa stanno facendo).
3. L'esito (skip / disinstalla+reinstalla) viene loggato in
   `%LOCALAPPDATA%\RelicToEpub\logs\installer.log` con il valore
   del sentinel letto (AppId + timestamp ISO).

Per rimuovere manualmente il sentinel senza reinstallare:

```cmd
del /q "%LOCALAPPDATA%\RelicToEpub\install.inprogress"
```

---

## 6. Risoluzione problemi

### "IPersistFile::Save failed; codice 0x80070005. Accesso negato."
Questo errore capitava nelle versioni 0.1.0 quando l'installer tentava di
creare un'icona sul **Desktop Pubblico** (`C:\Users\Public\Desktop`).
Dalla 0.1.1 in poi:

- Le icone desktop puntano al **desktop dell'utente corrente**
  (`%USERPROFILE%\Desktop` = `{userdesktop}`), che non richiede
  privilegi di amministratore
- L'installer verifica la scrivibilita della destinazione e, seppure
  in circostanze estreme non riesca a creare lo shortcut, lo **salta**
  silenziosamente senza abortire l'installazione

Se incontri ancora questo errore con una versione recente:

1. Verifica di star eseguendo l'ultima release (vedi [Releases](../../releases))
2. Prova a disabilitare temporaneamente l'antivirus: alcuni software
   di protezione iniettano hook in `IPersistFile::Save` che confondono
   l'installer
3. Apri una segnalazione allegando il log
   `%LOCALAPPDATA%\RelicToEpub\logs\installer.log`

### "Impossibile eseguire il file: A:\RelicToEpub — CreateProcess: 5. Accesso negato."
Questo errore tipicamente compare provando a cliccare un'icona di Start
Menu che punta a un percorso **inesistente** (es. una chiavetta USB
montata un tempo come `A:`, ora scollegata). Lo shortcut e un **residuo
orfano** di una installazione precedente.

Dalla versione 0.1.1:

- La disinstallazione esegue una **scansione automatica** di tutti i
  collegamenti `.lnk` / `.url` nelle directory `{group}`, `{userdesktop}`
  e `{commondesktop}`, e rimuove quelli "orfani" (cioe che puntano a
  eseguibili RelicToEpub non piu presenti su disco)
- Quando provi a lanciare l'app da uno shortcut orfano, ora vedrai
  una **finestra diagnostica** ("L'applicazione non riesce ad avviarsi")
  con istruzioni chiare per ripristinare, invece del messaggio generico
  di Windows

Per riparare manualmente:

1. Apri `Impostazioni di Windows → App → RelicToEpub → Disinstalla`
2. Rilancia l'ultimo installer RelicToEpub-Setup scaricato dal sito
   ufficiale
3. Se il problema persiste, elimina manualmente le icone orfane:
   ```cmd
   del /q "%USERPROFILE%\Desktop\RelicToEpub.lnk"
   rmdir /s /q "%AppData%\Microsoft\Windows\Start Menu\Programs\RelicToEpub"
   ```
   e reinstalla

### "msiexec error 2502/2503" durante l'installazione di pandoc
L'utente non ha diritti di amministratore. L'installer di Inno Setup richiede privilegi elevati per installare pandoc, quindi rilancialo come amministratore (click destro → Esegui come amministratore).

### "Compatibilità CUDA: GPU rilevata ma driver vecchio"
Aggiorna i driver NVIDIA da [nvidia.com/drivers](https://www.nvidia.com/drivers). Per SM ≥ 6.x serve almeno driver **450+** (CUDA 11.x compatibile).

### "torch.cuda.is_available() returns False dopo il bootstrap"
1. Apri un prompt nella cartella di installazione
2. Lancia `RelicToEpubCLI.exe --selftest`
3. Se l'output mostra `CUDA available: False`, controlla che:
   - I driver NVIDIA siano aggiornati
   - Il wheel torch scaricato corrisponda al tuo hardware (vedi log in `%LOCALAPPDATA%\RelicToEpub\logs\gpu_bootstrap.log`)

### "GPU rilevata ma installata build CPU" / fallback automatico
Dalla **0.1.1**, se il download del wheel CUDA fallisce 3 volte
consecutive (rete assente, proxy aziendale, mirror non raggiungibile),
il bootstrap **cade automaticamente** sulla build CPU e l'app resta
usabile. Lo stato viene aggiornato in splash con il messaggio
"GPU rilevata ma download fallito: installata build CPU".

Cosa fare:
1. Apri `%LOCALAPPDATA%\RelicToEpub\logs\launcher_selfcheck.log` e
   cerca la riga `event: "download_failure"`: ti dice quale URL e'
   fallito e perche'.
2. Se sei dietro a un proxy o firewall aziendale, configura
   `HTTPS_PROXY` come variabile d'ambiente e riavvia l'app.
3. Se il problema persiste, scarica manualmente il wheel da
   `https://download.pytorch.org/whl/cu118` (o `cu124`, `cu126` in
   base alla tua GPU) e piazzalo in
   `%LOCALAPPDATA%\RelicToEpub\torch_wheel_cache\torch-<versione>\`.

### Cache wheel torch / modello OCR non rimossi dopo disinstallazione
Questo e il **comportamento corretto di default**: cache e modello sono
riutilizzati se reinstalli l'app, evitando di riscaricare 7 GB.
Per rimuoverli, alla (re)installazione spunta la voce
**"Rimuovi anche cache e modello OCR"** — la disinstallazione successiva
pulira anche queste directory.

Oppure rimuovili manualmente:

```cmd
rmdir /s /q "%LOCALAPPDATA%\RelicToEpub"
```

### SmartScreen / Antivirus bloccano l'eseguibile
PyInstaller è occasionalmente flaggato come sospetto. Soluzioni:
- Aggiungi un'eccezione in Defender / Windows Security
- Firma digitalmente l'eseguibile (richiede certificato Authenticode, ~200 USD/anno — vedi `BUILD.md`)

---

## 7. Posizione dei log

Tutti i log sono in `%LOCALAPPDATA%\RelicToEpub\logs\`:

| File | Contenuto |
|------|-----------|
| `gpu_bootstrap.log` | Rilevamento GPU, download wheel, errori installazione torch |
| `launcher_selfcheck.log` | Diagnostica strutturata del bootstrap (validazione path, mismatch registry, fallimenti di download con `event: "download_failure"`) |
| `ui_launcher.log` | Output Gradio, eccezioni runtime |
| `cli_launcher.log` | Conversioni eseguite da CLI |

Per condividere un bug: allega il file di log rilevante. **Non include dati utente** (i PDF non vengono mai loggati per intero).
