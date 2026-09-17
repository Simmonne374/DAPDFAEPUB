; Inno Setup script per RelicToEpub
;
; Genera un installer Windows (EXE) che:
;   - supporta DUE modalita' di installazione selezionabili nel wizard:
;       * per-user   : %LOCALAPPDATA%\Programs\RelicToEpub (nessun prompt UAC)
;       * per-machine: %ProgramFiles%\RelicToEpub (richiede admin)
;   - crea voci in Start Menu e (opzionalmente) desktop shortcut
;   - lancia silent install dell'MSI di pandoc (solo in modalita' per-machine;
;     in modalita' per-user ci si appoggia al download automatico di pypandoc)
;   - mostra una GUI con status dettagliato durante l'install stessa
;
; Compilare con: ISCC.exe installer.iss
;
; Modalita' di installazione (parametro build-time):
;   ISCC.exe installer.iss                              -> wizard chiede all'utente
;   ISCC.exe /DInstallScope=per-user  installer.iss     -> default: solo per-user
;   ISCC.exe /DInstallScope=per-machine installer.iss   -> default: solo per-machine
; Senza /DInstallScope l'installer mostra la pagina di scelta nel wizard.
;

#define MyAppName "RelicToEpub"
; MyAppVersion viene passata dal workflow CI tramite la CLI di ISCC:
;   ISCC.exe /DMyAppVersion=X.Y.Z build/installer.iss
; Il default "0.1.0" serve solo per build locali fuori dalla pipeline.
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "RelicToEpub contributors"
#define MyAppURL "https://github.com/Simmonne374/DAPDFAEPUB"
#define MyAppExeName "RelicToEpubUI.exe"
#define MyAppCliName "RelicToEpubCLI.exe"
#define MyAppBootName "RelicToEpubBoot.exe"
; Pandoc MSI: nome del file MSI di pandoc che installiamo come dipendenza.
; Sovrascrivibile al build via ISCC: /DPandocMsi=pandoc-3.11-windows-x86_64.msi
; Il default corrisponde al file commiato nel repo root.
#ifndef PandocMsi
  #define PandocMsi "pandoc-3.10-windows-x86_64.msi"
#endif
; InstallScope: vincola il default al wizard.
; Valori accettati: "per-user", "per-machine", "either" (chiede all'utente).
; Default: "either" (wizard mostra la pagina di scelta).
#ifndef InstallScope
  #define InstallScope "either"
#endif

[Setup]
; Identificativo univoco installer (sostituiscini prima di release pubblica)
AppId={{43C83119-4124-4739-8E56-2E41A922ACAC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; AppMutex include lo scope cosi' un'installazione per-user e una
; per-machine non si "pestano" la coda a vicenda. Usato da CurStepChanged
; / IsUpgrade per rilevare installazioni preesistenti in modo affidabile
; (anche se la chiave di registro e' corrotta). Il valore di default
; cambia in base alla scelta dell'utente nel wizard (vedi UpdateMutex in
; InitializeWizard / InstallModePageProc).
AppMutex=RelicToEpub-Setup-Mutex-{#MyAppVersion}
; DefaultDirName dinamico: scelto da GetDefaultDirName() in base al
; radio button della pagina di scelta modalita'. Se l'utente cambia
; radio button, CurPageChanged aggiorna il campo DirEdit.
DefaultDirName={code:GetDefaultDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
InfoBeforeFile=..\README.md
OutputDir=..\Output
; OutputBaseFilename dinamico: il default e' RelicToEpub-Setup-{version};
; build_windows.ps1 puo' passare /DOutputBaseFilename=RelicToEpub-Setup-{version}-peruser
; per generare un installer con suffisso -peruser (utile per firme separate,
; canali di rilascio distinti, package manager enterprise). Se il macro
; non viene passato al build, cade al default qui sotto.
#ifndef OutputBaseFilename
  #define OutputBaseFilename "RelicToEpub-Setup-{#MyAppVersion}"
#endif
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
; Spazio extra richiesto dopo l'install (wheel torch + cache OCR): Windows
; mostrera' "Required: 3.0 GB, Available: X GB" nella pagina SelectDir.
; 3221225472 bytes = 3 GiB.
ExtraDiskSpaceRequired=3221225472
; Privilegi: "lowest" = installa senza elevation se possibile. La pagina
; di scelta modalita' mostra "Installa solo per me (nessun prompt UAC)"
; come opzione di default. Se l'utente sceglie "per tutti gli utenti"
; e sta scrivendo in {autopf}, verra' mostrato il dialog di elevazione
; (PrivilegesRequiredOverridesAllowed=dialog). Per le installazioni
; per-user (target = {localappdata}\Programs) non serve elevation.
PrivilegesRequired=lowest
; "dialog" mostra il prompt UAC solo quando l'utente sceglie la modalita
; per-machine; "commandline" consente /SILENT in CI unattended.
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppBootName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoCopyright=Copyright (c) 2026
; Mostra la descrizione estesa di installazione (visibile nel Pannello di controllo)
AppReadmeFile={#MyAppURL}

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Icone: desktop utente (no richiede admin, non tocca Public Desktop) +
; voci in Start Menu. Niente piu {commondesktop} per evitare access denied.
Name: "desktopicon"; Description: "Crea un'icona sul desktop (utente corrente)"; GroupDescription: "Icone:"; Flags: checkedonce
Name: "startmenu"; Description: "Crea voci in Start Menu"; GroupDescription: "Icone:"; Flags: checkedonce
; Pulizia cache opzionale in uninstall: include wheel torch scaricati e
; modello OCR (~6 GB). Default non selezionato per preservare download su
; reinstallazioni frequenti.
Name: "removecache"; Description: "Rimuovi anche cache e modello OCR (~7 GB in AppData)"; GroupDescription: "Disinstallazione:"; Flags: unchecked
; Aggiorna la PATH utente per includere {app}, cosi pandoc.exe (gia nella
; stessa cartella) diventa disponibile da cmd/PowerShell senza config manuale.
; Disattivato di default: l'MSI di pandoc gestisce la PATH globalmente.
Name: "adduserpath"; Description: "Aggiungi la cartella di installazione alla PATH utente"; GroupDescription: "Avanzate:"; Flags: unchecked

[Files]
; Cartella principale dell'app (UI + CLI + _internal)
Source: "..\dist\RelicToEpub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Cartella del bootstrap (separata perché built con COLLECT name="boot")
Source: "..\dist\boot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; L'MSI di pandoc da installare come dipendenza esterna
Source: "..\{#PandocMsi}"; DestDir: "{tmp}"; Flags: ignoreversion

[Icons]
; Icone nel menu Start (uno per ogni eseguibile + collegamento Disinstalla).
; NON mettiamo direttamente percorsi assoluti: usiamo workingdir+filename
; cosi se l'utente sposta l'installazione lo shortcut continua a funzionare
; finche il file .exe e presente (grazie al workingdir).
Name: "{group}\{#MyAppName} UI"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startmenu
Name: "{group}\{#MyAppName} CLI"; Filename: "{app}\{#MyAppCliName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppCliName}"; Tasks: startmenu
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; WorkingDir: "{app}"; Tasks: startmenu
; Icone DESKTOP per-utente: {userdesktop} = %USERPROFILE%\Desktop.
; NON usiamo {commondesktop} (C:\Users\Public\Desktop) perche:
;   1) richiede privilegi admin (e con PrivilegesRequiredOverridesAllowed=
;      dialog l'utente puo declinare UAC -> IPersistFile::Save failed 0x80070005)
;   2) policy aziendali ACL restrittive su Public Desktop
;   3) shortcut finisce visibile a TUTTI gli utenti del PC (sorpresa indesiderata)
Name: "{userdesktop}\{#MyAppName}.lnk"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userdesktop}\{#MyAppName} (CLI).lnk"; Filename: "{app}\{#MyAppCliName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppCliName}"; Tasks: desktopicon

[Run]
; Pandoc MSI install: SOLO per installazioni per-machine. L'MSI di pandoc
; richiede privilegi admin per essere installato (si registra in HKLM e
; copia file in %ProgramFiles%); un utente standard non potrebbe eseguire
; questa fase. Per le installazioni per-user il bootstrap della app
; (RelicToEpubBoot) si appoggia a pypandoc.download_pandoc(), che scarica
; il binario di pandoc nella cache utente senza richiedere privilegi.
; Vedi build/launchers/gpu_bootstrap.py e docs/INSTALL_WINDOWS.md sez. 2.
Filename: "msiexec.exe"; \
    Parameters: "/i ""{tmp}\{#PandocMsi}"" /qb! ADDLOCAL=ALL REBOOT=ReallySuppress /norestart"; \
    StatusMsg: "Installazione dipendenza esterna: pandoc — attendere prego…"; \
    Check: PandocNeededAndMachineInstall; Flags: waituntilterminated

; Opzionale: aprire la cartella di installazione al termine
Filename: "{app}"; Description: "Apri la cartella di installazione"; Flags: nowait postinstall skipifsilent runmaximized

[Registry]
; Chiavi di disinstallazione esplicite (alcune sono gia auto-create da Inno
; Setup, ma le rendiamo deterministiche indipendentemente dalle opzioni
; predefinite). Root determinata dalla scelta dell'utente nel wizard:
;   - per-machine install -> HKLM (visibilita in Installazione applicazioni
;     per TUTTI gli utenti del PC)
;   - per-user install    -> HKCU (visibilita solo per l'utente corrente;
;     niente UAC per la registrazione, niente privilegio richiesto)
;
; NOTA: il nome della chiave deve terminare con "_is1" per essere
; riconosciuta da "Installazione applicazioni" di Windows 10/11.
;
; In entrambe le modalita' impostiamo EstimatedSize e i flag NoModify/NoRepair
; per uniformita' del pannello di controllo. I campi Publisher e DisplayIcon
; aiutano il riconoscimento anche dopo anni.
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayName";      ValueData: "{#MyAppName} {#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayVersion";   ValueData: "{#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "Publisher";        ValueData: "{#MyAppPublisher}"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "InstallLocation";  ValueData: "{app}"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "UninstallString";  ValueData: "{uninstallexe}"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "QuietUninstallString"; ValueData: """{uninstallexe}"" /SILENT"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayIcon";      ValueData: "{app}\{#MyAppBootName}"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "NoModify";          ValueData: "1"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "NoRepair";          ValueData: "1"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
; Versione stimata in KB (per colonne Pannello di controllo "Dimensione")
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "EstimatedSize";    ValueData: "3145728"; \
    Flags: uninsdeletekey; Check: InstallUsesMachineHive
; Per-user install: tutte le stesse voci ma in HKCU. Cancellate in uninstall
; senza bisogno di privilegi admin.
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayName";      ValueData: "{#MyAppName} {#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayVersion";   ValueData: "{#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "Publisher";        ValueData: "{#MyAppPublisher}"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "InstallLocation";  ValueData: "{app}"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "UninstallString";  ValueData: "{uninstallexe}"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "QuietUninstallString"; ValueData: """{uninstallexe}"" /SILENT"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayIcon";      ValueData: "{app}\{#MyAppBootName}"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "NoModify";          ValueData: "1"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "NoRepair";          ValueData: "1"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive
; Versione stimata in KB (per-user)
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "EstimatedSize";    ValueData: "3145728"; \
    Flags: uninsdeletekey; Check: InstallUsesUserHive

[UninstallRun]
; Chiusura istanze in esecuzione prima della rimozione: se l'utente ha lasciato
; aperta la UI o la CLI, l'uninstall non riesce a cancellare gli .exe in uso.
; taskkill con /F e robusto: ignora processi gia terminati e non blocca UI.
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppCliName}"; Flags: runhidden
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppBootName}"; Flags: runhidden

[UninstallDelete]
; Pulizia esaustiva: copre TUTTO cio che l'installazione ha creato su disco.
; L'ordine non conta: Inno Setup gestisce file mancanti senza errori.
;
; 1) Contenuti della cartella di installazione (eseguibili, MSI pandoc,
;    log, librerie PyInstaller, eventuali residui).
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\*.log"
Type: filesandordirs; Name: "{app}\*.tmp"
Type: filesandordirs; Name: "{app}\*.bak"
Type: filesandordirs; Name: "{app}\*.pyc"
Type: filesandordirs; Name: "{app}\__pycache__"
; "filesandordirs" ricorsivo per la root {app}: rimuove la cartella di
; installazione se vuota dopo aver tolto _internal.
Type: filesandordirs; Name: "{app}"
; 2) Icone desktop per-utente (qualsiasi .lnk / .url residue; il pattern
;    esplicito riduce falsi positivi vs l'uso di {userdesktop}\*).
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"
Type: files; Name: "{userdesktop}\{#MyAppName} (CLI).lnk"
Type: files; Name: "{userdesktop}\{#MyAppName}.url"
Type: files; Name: "{commondesktop}\{#MyAppName}.lnk"
Type: files; Name: "{commondesktop}\{#MyAppName} (CLI).lnk"
; 3) Gruppo Start Menu (eseguibili + uninstaller + collegamenti manuali).
Type: filesandordirs; Name: "{group}"
; 4) Cache wheel torch + modello OCR (~6-7 GB). Solo se l'utente ha
;    spuntato la task "removecache" durante l'install (vedi sezione
;    InitializeUninstallStep): pulizia opzionale, default OFF.
Type: filesandordirs; Name: "{localappdata}\RelicToEpub"; Tasks: removecache

[Code]
// ======================================================================
//  Codice Pascal-script di Inno Setup - install/uninstall robusti
// ======================================================================
//
//  Modalita' di installazione (vedi [Setup]):
//    * per-user    : %LOCALAPPDATA%\Programs\RelicToEpub, HKCU, nessun UAC
//    * per-machine : %ProgramFiles%\RelicToEpub, HKLM, richiede admin
//
//  La scelta e' guidata dalla pagina custom "InstallModePage" che si
//  inserisce subito dopo wpWelcome. Il default puo' essere vincolato al
//  build con /DInstallScope=per-user|per-machine (vedi installer.iss).
// ======================================================================

const
  LOG_DIR_APP   = '{localappdata}\RelicToEpub\logs';
  SETUP_LOG     = 'installer.log';
  UNINSTALL_LOG = 'uninstaller.log';
  // File sentinella che segnala un'installazione IN CORSO. Se al prossimo
  // avvio del setup il file esiste ancora, vuol dire che l'install
  // precedente e' stato interrotto a meta' (es. crash, BSOD, blackout).
  SENTINEL_DIR  = '{localappdata}\RelicToEpub';
  SENTINEL_FILE = 'install.inprogress';
  // Valori ammessi per la scelta modalita'.
  SCOPE_PER_USER    = 'per-user';
  SCOPE_PER_MACHINE = 'per-machine';

var
  LastLogMsg: String;
  // ---- Stato wizard dual-mode ----
  InstallModePage: TWizardPage;
  RadioPerUser: TNewRadioButton;
  RadioPerMachine: TNewRadioButton;
  // Cache della scelta corrente (ricalcolata da GetInstallScope).
  ChosenScope: String;
  // True alla prima attivazione della InstallModePage: usata dal
  // hook OnActivate per inizializzare i radio button al default di
  // build. L'hook OnActivate non riceve un flag FirstTime (vedi tipo
  // Pascal-script TWizardPageNotifyEvent = procedure(Sender: TWizardPage)),
  // quindi simuliamo la semantica con una variabile di modulo.
  InstallModeFirstActivation: Boolean;

// ----------------------------------------------------------------------
// Utility: logging persistente (sopravvive al crash dell'installer)
// ----------------------------------------------------------------------
procedure EnsureLogDir;
var
  Dir: String;
begin
  Dir := ExpandConstant(LOG_DIR_APP);
  if not DirExists(Dir) then
  begin
    try
      CreateDir(Dir);
    except
      // fallback: scrivi solo nel log integrato di Inno
    end;
  end;
end;

procedure LogMsg(const Msg: String);
var
  LogPath: String;
  Txt: String;
begin
  if Msg = LastLogMsg then Exit;
  LastLogMsg := Msg;
  EnsureLogDir;
  if IsUninstaller then
    LogPath := ExpandConstant(LOG_DIR_APP) + '\' + UNINSTALL_LOG
  else
    LogPath := ExpandConstant(LOG_DIR_APP) + '\' + SETUP_LOG;
  Txt := GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '  ' + Msg + #13#10;
  try
    SaveStringToFile(LogPath, Txt, True);
  except
    // logging non critico
  end;
  Log(Msg);
end;

function IfThenStr(const Cond: Boolean; const ThenStr, ElseStr: String): String;
begin
  if Cond then Result := ThenStr else Result := ElseStr;
end;

// ----------------------------------------------------------------------
// Sentinel file: marca l'inizio/fine installazione per rilevare crash
// ----------------------------------------------------------------------
procedure WriteSentinel;
var
  Dir, Path: String;
begin
  Dir := ExpandConstant(SENTINEL_DIR);
  if not DirExists(Dir) then
    CreateDir(Dir);
  Path := Dir + '\' + SENTINEL_FILE;
  // Scriviamo l'AppId cosi' i setup di altre app non interferiscono.
  SaveStringToFile(Path, '{#SetupSetting("AppId")}' + #13#10 +
    GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + #13#10, False);
  LogMsg('Sentinel scritto: ' + Path);
end;

procedure ClearSentinel;
var
  Path: String;
begin
  Path := ExpandConstant(SENTINEL_DIR) + '\' + SENTINEL_FILE;
  if FileExists(Path) then
  begin
    if DeleteFile(Path) then
      LogMsg('Sentinel rimosso: ' + Path)
    else
      LogMsg('Impossibile rimuovere sentinel: ' + Path);
  end;
end;

// ----------------------------------------------------------------------
// Pandoc: true se pandoc NON e nel PATH (serve installarlo)
// ----------------------------------------------------------------------
function PandocNeeded(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if Exec('cmd.exe', '/c where pandoc', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Result := False;
  end;
end;

// ----------------------------------------------------------------------
// Flag di installazione admin (per le sezioni [Registry] condizionali)
// ----------------------------------------------------------------------
function IsAdminInstall(): Boolean;
begin
  Result := IsAdmin;
end;

// ----------------------------------------------------------------------
// Selezione modalita' (per-user vs per-machine)
//
//  La scelta "per-user" / "per-machine" e' catturata dai radio button
//  della pagina InstallModePage (vedi InitializeWizard). Se la pagina
//  non e' ancora stata inizializzata (es. chiamate da CurStepChanged
//  prima di wpWelcome) ricadiamo sul default di build /DInstallScope=...
//  o su "per-user" come fail-safe.
// ----------------------------------------------------------------------
function DefaultInstallScope: String;
begin
  // L'inno-setup {#InstallScope} definisce il default a build-time:
  //   "per-user"  -> default per-user (nessun UAC)
  //   "per-machine"-> default per-machine (legacy, UAC)
  //   "either"    -> wizard mostra la scelta
  // Se arriva un valore sconosciuto, fallback per-user (preferiamo
  // l'install sicura senza UAC).
  if '{#InstallScope}' = 'per-machine' then
    Result := SCOPE_PER_MACHINE
  else if '{#InstallScope}' = 'per-user' then
    Result := SCOPE_PER_USER
  else
    Result := SCOPE_PER_USER;
end;

function GetInstallScope: String;
begin
  // Se la pagina esiste gia' e un radio button e' selezionato, usa quello.
  if Assigned(RadioPerUser) and Assigned(RadioPerMachine) then
  begin
    if RadioPerMachine.Checked then
      Result := SCOPE_PER_MACHINE
    else
      Result := SCOPE_PER_USER;
  end
  else
    Result := DefaultInstallScope;
  // Cache per i punti che non riescono a rieseguire la logica (es. CurStepChanged).
  ChosenScope := Result;
end;

function InstallUsesMachineHive: Boolean;
begin
  Result := GetInstallScope = SCOPE_PER_MACHINE;
end;

function InstallUsesUserHive: Boolean;
begin
  Result := GetInstallScope = SCOPE_PER_USER;
end;

// Restituisce la DefaultDirName per la modalita' scelta. Usato dal
// direttiva [Setup] DefaultDirName={code:GetDefaultDirName}.
function GetDefaultDirName(Param: String): String;
begin
  if GetInstallScope = SCOPE_PER_MACHINE then
    Result := ExpandConstant('{autopf}') + '\{#MyAppName}'
  else
    Result := ExpandConstant('{localappdata}\Programs\{#MyAppName}');
end;

// Pandoc MSI: serve solo in modalita' per-machine (l'MSI richiede admin
// per scrivere in %ProgramFiles% e modificare la PATH di sistema).
function PandocNeededAndMachineInstall: Boolean;
begin
  Result := InstallUsesMachineHive and PandocNeeded;
end;

// ----------------------------------------------------------------------
// Pagina wizard di scelta modalita'
//
//  Inserita tra wpWelcome e wpSelectDir. Contiene due radio button:
//    * "Installa solo per me" (default, nessun UAC)
//    * "Installa per tutti gli utenti" (richiede admin)
//  La scelta viene letta da GetInstallScope() e usata per pilotare
//  DefaultDirName, AppMutex e le sezioni [Registry]/[Run] condizionali.
//
//  La firma DEVE essere procedure(Sender: TWizardPage) perche' cosi'
//  e' dichiarata TWizardPageNotifyEvent in Pascal-script (vedi
//  jrsoftware/issrc Projects/Src/Compiler.ScriptClasses.pas). Il
//  compilatore rifiuta handler con parametri extra: una versione
//  precedente usava (Sender: TObject; FirstTime: Boolean) e NON
//  compilava. La semantica "solo alla prima attivazione" e' ottenuta
//  con la variabile di modulo InstallModeFirstActivation, inizializzata
//  a True in InitializeWizard.
// ----------------------------------------------------------------------
procedure InstallModePageProc(Sender: TWizardPage);
begin
  // Quando la pagina diventa visibile per la prima volta, inizializza
  // i radio button al default di build. Sui ritorni (Back) il flag
  // resta False: lasciamo i radio button nella scelta esplicita
  // dell'utente, che altrimenti verrebbe resettata ad ogni re-entry.
  if InstallModeFirstActivation then
  begin
    InstallModeFirstActivation := False;
    if DefaultInstallScope = SCOPE_PER_MACHINE then
      RadioPerMachine.Checked := True
    else
      RadioPerUser.Checked := True;
  end;
  ChosenScope := GetInstallScope;
end;

procedure RadioPerUserOnClick(Sender: TObject);
begin
  ChosenScope := GetInstallScope;
  if WizardForm.DirEdit <> nil then
    WizardForm.DirEdit.Text := GetDefaultDirName('');
end;

procedure RadioPerMachineOnClick(Sender: TObject);
begin
  ChosenScope := GetInstallScope;
  if WizardForm.DirEdit <> nil then
    WizardForm.DirEdit.Text := GetDefaultDirName('');
end;

// Aggiorna la AppMutex a runtime per evitare che un setup per-user e
// uno per-machine in flight si confondano. Inno Setup accetta AppMutex
// dinamico via {code:...} solo a partire da 6.x: lo riassegnamo qui
// in CurPageChanged usando SetSetupSetting.
procedure UpdateAppMutexForScope;
var
  NewMutex: String;
begin
  NewMutex := 'RelicToEpub-Setup-Mutex-{#MyAppVersion}-' + GetInstallScope;
  SetSetupSetting('AppMutex', NewMutex);
end;

// ----------------------------------------------------------------------
// Wizard initialization: costruisce la InstallModePage
// ----------------------------------------------------------------------
procedure InitializeWizard;
var
  HeadingLabel: TNewStaticText;
  HintLabel: TNewStaticText;
  DescLabel: TNewStaticText;
begin
  // Pagina di scelta modalita' (inserita subito dopo Welcome)
  InstallModePage := CreateCustomPage(
    wpWelcome,
    'Modalita'' di installazione',
    'Scegli se installare RelicToEpub solo per te o per tutti gli utenti del computer.'
  );

  HeadingLabel := TNewStaticText.Create(InstallModePage);
  HeadingLabel.Parent := InstallModePage.Surface;
  HeadingLabel.Caption :=
    'L''installazione tradizionale richiede i privilegi di amministratore ' +
    'e mostra un prompt UAC ogni volta. La nuova modalita'' "per me" evita ' +
    'completamente il prompt UAC e funziona anche in ambienti "locked-down" ' +
    '(aule scolastiche, kiosk, account standard).';
  HeadingLabel.Left := ScaleX(0);
  HeadingLabel.Top := ScaleY(8);
  HeadingLabel.Width := InstallModePage.SurfaceWidth;
  HeadingLabel.Height := ScaleY(60);
  HeadingLabel.WordWrap := True;

  RadioPerUser := TNewRadioButton.Create(InstallModePage);
  RadioPerUser.Parent := InstallModePage.Surface;
  RadioPerUser.Left := ScaleX(8);
  RadioPerUser.Top := ScaleY(80);
  RadioPerUser.Width := InstallModePage.SurfaceWidth - ScaleX(16);
  RadioPerUser.Height := ScaleY(34);
  RadioPerUser.Caption :=
    'Installa solo per me (consigliato, nessun prompt UAC)';
  RadioPerUser.Font.Style := RadioPerUser.Font.Style + [fsBold];
  RadioPerUser.GroupIndex := 1;
  RadioPerUser.Checked := True;
  RadioPerUser.OnClick := @RadioPerUserOnClick;

  DescLabel := TNewStaticText.Create(InstallModePage);
  DescLabel.Parent := InstallModePage.Surface;
  DescLabel.Left := ScaleX(28);
  DescLabel.Top := ScaleY(98);
  DescLabel.Width := InstallModePage.SurfaceWidth - ScaleX(40);
  DescLabel.Height := ScaleY(28);
  DescLabel.Caption :=
    'Installa in %LOCALAPPDATA%\Programs\RelicToEpub, registro HKCU. ' +
    'Visibile in Impostazioni -> App solo per l''utente corrente.';
  DescLabel.WordWrap := True;
  DescLabel.Font.Color := clGrayText;

  RadioPerMachine := TNewRadioButton.Create(InstallModePage);
  RadioPerMachine.Parent := InstallModePage.Surface;
  RadioPerMachine.Left := ScaleX(8);
  RadioPerMachine.Top := ScaleY(140);
  RadioPerMachine.Width := InstallModePage.SurfaceWidth - ScaleX(16);
  RadioPerMachine.Height := ScaleY(34);
  RadioPerMachine.Caption :=
    'Installa per tutti gli utenti (richiede privilegi admin)';
  RadioPerMachine.GroupIndex := 1;
  RadioPerMachine.OnClick := @RadioPerMachineOnClick;

  HintLabel := TNewStaticText.Create(InstallModePage);
  HintLabel.Parent := InstallModePage.Surface;
  HintLabel.Left := ScaleX(28);
  HintLabel.Top := ScaleY(158);
  HintLabel.Width := InstallModePage.SurfaceWidth - ScaleX(40);
  HintLabel.Height := ScaleY(28);
  HintLabel.Caption :=
    'Installa in %ProgramFiles%\RelicToEpub, registro HKLM. Visibile in ' +
    'Impostazioni -> App a tutti gli account del PC. Sara'' mostrato un ' +
    'prompt UAC per confermare.';
  HintLabel.WordWrap := True;
  HintLabel.Font.Color := clGrayText;

  // Quando wpSelectDir (o altre pagine successive) prendono il focus,
  // aggiorna il path suggerito e il mutex in base alla scelta corrente.
  // L'handler InstallModePageProc legge InstallModeFirstActivation per
  // decidere se applicare il default di build (solo alla prima attivazione).
  InstallModeFirstActivation := True;
  InstallModePage.OnActivate := @InstallModePageProc;
end;

// ----------------------------------------------------------------------
// CurPageChanged: aggiorna i campi dipendenti quando l'utente naviga
// nel wizard. In particolare:
//  - NON tocchiamo i radio button qui: la prima inizializzazione e'
//    fatta da InstallModePageProc quando InstallModeFirstActivation
//    e' True, e su re-entry dobbiamo PRESERVARE la scelta esplicita
//    dell'utente (altrimenti tornando indietro con Back la scelta
//    verrebbe resettata al default).
//  - quando si entra in wpSelectDir / wpReady, aggiorna DirEdit
//    e AppMutex in base alla scelta corrente.
// ----------------------------------------------------------------------
procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectDir) or (CurPageID = wpReady) then
  begin
    // Forza DefaultDirName coerente con la scelta corrente
    if WizardForm.DirEdit <> nil then
      WizardForm.DirEdit.Text := GetDefaultDirName('');
    UpdateAppMutexForScope;
  end;
end;

// ----------------------------------------------------------------------
// Ricerca di installazione preesistente tramite registro
// ----------------------------------------------------------------------
function GetPreviousInstallPath: String;
var
  RegKey: String;
begin
  Result := '';
  RegKey := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1';
  if RegQueryStringValue(HKLM, RegKey, 'InstallLocation', Result) then
    Exit;
  if RegQueryStringValue(HKCU, RegKey, 'InstallLocation', Result) then
    Exit;
  // Se non abbiamo letto un path (es. era DisplayName), scarta.
  if (Length(Result) = 0) or (Length(Result) > 260) then
    Result := '';
end;

function IsUpgrade(): Boolean;
var
  Prev: String;
begin
  Prev := GetPreviousInstallPath;
  Result := (Prev <> '') and (CompareText(Prev, ExpandConstant('{app}')) <> 0);
end;

// ----------------------------------------------------------------------
// Hook iniziale: benvenuto + warning su installazioni precedenti / path
// ----------------------------------------------------------------------
function InitializeSetup(): Boolean;
var
  Prev: String;
  SentinelPath: String;
begin
  Result := True;
  LastLogMsg := '';
  LogMsg('=== Avvio setup RelicToEpub v{#MyAppVersion} ===');
  LogMsg('Destinazione: ' + ExpandConstant('{app}'));
  LogMsg('Privilegi: ' + IfThenStr(IsAdminInstall, 'amministratore', 'utente'));
  LogMsg('InstallScope build-time (#define): {#InstallScope}');
  LogMsg('InstallScope scelto a runtime (pre-wizard): ' + DefaultInstallScope);

  // Rileva un'installazione precedente interrotta a meta': se il sentinel
  // file esiste ancora, vuol dire che il setup precedente e' terminato
  // senza completare ssDone (crash, blackout, BSOD). Suggeriamo all'utente
  // di disinstallare la versione precedente prima di proseguire.
  SentinelPath := ExpandConstant(SENTINEL_DIR) + '\' + SENTINEL_FILE;
  if FileExists(SentinelPath) then
  begin
    LogMsg('Rilevato sentinel di installazione precedente non completata: ' + SentinelPath);
    if MsgBox(
      'Un''installazione precedente di RelicToEpub sembra essere stata interrotta prima del completamento.' + #13#10 + #13#10 +
      'Per evitare file corrotti o voci di registro inconsistenti, raccomandiamo di:' + #13#10 +
      '  1) uscire da questo installer' + #13#10 +
      '  2) aprire "Installazione applicazioni" di Windows' + #13#10 +
      '  3) rimuovere qualsiasi voce RelicToEpub presente' + #13#10 +
      '  4) rilanciare questo installer' + #13#10 + #13#10 +
      'Continuare comunque con l''installazione corrente?',
      mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      LogMsg('Setup annullato per sentinel di installazione interrotta');
    end;
  end;

  Prev := GetPreviousInstallPath;
  if (Prev <> '') and (CompareText(Prev, ExpandConstant('{app}')) <> 0) then
  begin
    LogMsg('Trovata installazione precedente in: ' + Prev);
    if MsgBox(
      'E'' stata rilevata un''installazione precedente di RelicToEpub in:' + #13#10 +
      Prev + #13#10 + #13#10 +
      'Se stai aggiornando, ignora questo messaggio.' + #13#10 +
      'Se vuoi disinstallare la versione precedente, fallo ora e rilancia questo installer.',
      mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      LogMsg('Setup annullato (versione precedente in ' + Prev + ')');
    end;
  end;

  // Warning se il path contiene lettere sospette (floppy disk)
  if (Pos('A:\', ExpandConstant('{app}')) = 1) or
     (Pos('B:\', ExpandConstant('{app}')) = 1) then
  begin
    if MsgBox(
      'Stai installando su un percorso sospetto:' + #13#10 +
      ExpandConstant('{app}') + #13#10 + #13#10 +
      'Le unita'' A: e B: sono floppy disk storici e potrebbero non essere scrivibili oggi.' + #13#10 +
      'Consigliamo una cartella su disco fisso. Continuare comunque?',
      mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      LogMsg('Setup annullato (path sospetto: floppy disk)');
    end;
  end;
end;

// ----------------------------------------------------------------------
// Status visivo durante le fasi di install
// ----------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
begin
  case CurStep of
    ssInstall:
      begin
        LogMsg('CurStepChanged: ssInstall (scope=' + GetInstallScope + ')');
        WriteSentinel;
        if WizardForm.StatusLabel <> nil then
          WizardForm.StatusLabel.Caption :=
            'Estrazione componenti applicazione in corso - Attendere prego.';
      end;
    ssPostInstall:
      begin
        LogMsg('CurStepChanged: ssPostInstall (scope=' + GetInstallScope + ')');
        if WizardForm.StatusLabel <> nil then
          if InstallUsesMachineHive then
            WizardForm.StatusLabel.Caption :=
              'Installazione dipendenza esterna (pandoc MSI) - Attendere prego.'
          else
            WizardForm.StatusLabel.Caption :=
              'Configurazione finale (skip pandoc MSI per install per-user).';
      end;
    ssDone:
      begin
        LogMsg('CurStepChanged: ssDone (scope=' + GetInstallScope + ')');
        ClearSentinel;
        if WizardForm.StatusLabel <> nil then
          WizardForm.StatusLabel.Caption :=
            'Configurazione finale (Start Menu, registro) - Quasi terminato.';
      end;
  end;
end;

// ----------------------------------------------------------------------
// Helpers per la pulizia shortcut orfani durante disinstall
// ----------------------------------------------------------------------
function IsOrphanLnk(const LnkPath: String): Boolean;
var
  Base, Stem, AppDir: String;
begin
  Result := False;
  if not FileExists(LnkPath) then Exit;
  Base := ExtractFileName(LnkPath);
  if Pos('RelicToEpub', Base) = 0 then Exit;

  Stem := ChangeFileExt(Base, '');
  AppDir := ExpandConstant('{app}');

  // Se un qualsiasi eseguibile noto esiste ancora nella nostra {app},
  // lo shortcut e ancora valido o verra gestito da Inno [UninstallDelete].
  if FileExists(AppDir + '\{#MyAppExeName}') or
     FileExists(AppDir + '\{#MyAppCliName}') or
     FileExists(AppDir + '\{#MyAppBootName}') then
    Exit;

  // Casi con nome base = nome app: lo shortcut punta agli exe canonici
  if (Stem = '{#MyAppName}') or (Stem = '{#MyAppName} (CLI)') or
     (Stem = '{#MyAppName} UI') or (Stem = '{#MyAppName} CLI') then
    Exit;

  // Nessun eseguibile target trovato -> candidato a orfano.
  Result := True;
end;

procedure CleanupOrphanShortcuts(const RootDir: String);
var
  FindRec: TFindRec;
  Full: String;
  Ext: String;
begin
  if not DirExists(RootDir) then Exit;
  if FindFirst(RootDir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name = '.') or (FindRec.Name = '..') then Continue;
        Full := RootDir + '\' + FindRec.Name;
        if DirExists(Full) then
        begin
          CleanupOrphanShortcuts(Full);
        end
        else
        begin
          Ext := LowerCase(ExtractFileExt(FindRec.Name));
          if (Ext = '.lnk') or (Ext = '.url') then
          begin
            if IsOrphanLnk(Full) then
            begin
              LogMsg('Rimozione shortcut orfano: ' + Full);
              try
                DeleteFile(Full);
              except
                LogMsg('Impossibile cancellare ' + Full);
              end;
            end;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

// ----------------------------------------------------------------------
// Hook disinstallazione: log esplicito e pulizia shortcut orfani DOPO
// che Inno Setup ha finito di cancellare i suoi [UninstallDelete].
// ----------------------------------------------------------------------
procedure InitializeUninstallProgressForm;
begin
  LastLogMsg := '';
  LogMsg('=== Avvio disinstallazione RelicToEpub v{#MyAppVersion} ===');
  if UninstallProgressForm.StatusLabel <> nil then
    UninstallProgressForm.StatusLabel.Caption :=
      'Rimozione file applicazione in corso...';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    LogMsg('CurUninstallStepChanged: usPostUninstall - ricerca shortcut orfani');
    CleanupOrphanShortcuts(ExpandConstant('{app}'));
    CleanupOrphanShortcuts(ExpandConstant('{group}'));
    CleanupOrphanShortcuts(ExpandConstant('{userdesktop}'));
    CleanupOrphanShortcuts(ExpandConstant('{commondesktop}'));
    LogMsg('CurUninstallStepChanged: fine pulizia shortcut orfani');
  end;
end;
