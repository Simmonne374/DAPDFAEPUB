; Inno Setup script per RelicToEpub
;
; Genera un installer Windows (EXE) che:
;   - installa in Program Files\RelicToEpub
;   - crea voci in Start Menu e (opzionalmente) desktop shortcut
;   - lancia silent install dell'MSI di pandoc con progress visibile
;   - mostra una GUI con status dettagliato durante l'install stessa
;
; Compilare con: ISCC.exe installer.iss
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

[Setup]
; Identificativo univoco installer (sostituiscini prima di release pubblica)
AppId={{43C83119-4124-4739-8E56-2E41A922ACAC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; AppMutex usato da CurStepChanged/IsUpgrade per rilevare installazioni
; preesistenti in modo affidabile (anche se la chiave di registro e corrotta).
AppMutex=RelicToEpub-Setup-Mutex-{#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
InfoBeforeFile=..\README.md
OutputDir=..\Output
OutputBaseFilename=RelicToEpub-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
; Spazio extra richiesto dopo l'install (wheel torch + cache OCR): Windows
; mostrera' "Required: 3.0 GB, Available: X GB" nella pagina SelectDir.
; 3221225472 bytes = 3 GiB.
ExtraDiskSpaceRequired=3221225472
PrivilegesRequired=admin
; "dialog" mostra il prompt UAC; "commandline" consente /SILENT in CI unattended.
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
; Pandoc silent install — con messaggio di progresso visibile all'utente
Filename: "msiexec.exe"; \
    Parameters: "/i ""{tmp}\{#PandocMsi}"" /qb! ADDLOCAL=ALL REBOOT=ReallySuppress /norestart"; \
    StatusMsg: "Installazione dipendenza esterna: pandoc 3.10 — attendere prego…"; \
    Check: PandocNeeded; Flags: waituntilterminated

; Opzionale: aprire la cartella di installazione al termine
Filename: "{app}"; Description: "Apri la cartella di installazione"; Flags: nowait postinstall skipifsilent runmaximized

[Registry]
; Chiavi di disinstallazione esplicite (alcune sono gia auto-create da Inno
; Setup, ma le rendiamo deterministiche indipendentemente dalle opzioni
; predefinite). Root: HKLM se admin (per visibilita globale), HKCU come
; fallback se installato senza privilegi elevati.
;
; NOTA: il nome della chiave deve terminare con "_is1" per essere
; riconosciuta da "Installazione applicazioni" di Windows 10/11.
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayName";      ValueData: "{#MyAppName} {#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayVersion";   ValueData: "{#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "Publisher";        ValueData: "{#MyAppPublisher}"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "InstallLocation";  ValueData: "{app}"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "UninstallString";  ValueData: "{uninstallexe}"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "QuietUninstallString"; ValueData: """{uninstallexe}"" /SILENT"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayIcon";      ValueData: "{app}\{#MyAppBootName}"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "NoModify";          ValueData: "1"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "NoRepair";          ValueData: "1"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
; Versione stimata in KB (per colonne Pannello di controllo "Dimensione")
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: dword;  ValueName: "EstimatedSize";    ValueData: "3145728"; \
    Flags: uninsdeletekey; Check: IsAdminInstall
; Fallback HKCU se non-admin (la chiave HKCU verra poi rimossa durante uninstall
; senza bisogno di privilegi admin).
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayName";      ValueData: "{#MyAppName} {#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayVersion";   ValueData: "{#MyAppVersion}"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "Publisher";        ValueData: "{#MyAppPublisher}"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "InstallLocation";  ValueData: "{app}"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "UninstallString";  ValueData: "{uninstallexe}"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "QuietUninstallString"; ValueData: """{uninstallexe}"" /SILENT"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting('AppId')}_is1"; \
    ValueType: string; ValueName: "DisplayIcon";      ValueData: "{app}\{#MyAppBootName}"; \
    Flags: uninsdeletekey; Check: not IsAdminInstall

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

const
  LOG_DIR_APP   = '{localappdata}\RelicToEpub\logs';
  SETUP_LOG     = 'installer.log';
  UNINSTALL_LOG = 'uninstaller.log';
  // File sentinella che segnala un'installazione IN CORSO. Se al prossimo
  // avvio del setup il file esiste ancora, vuol dire che l'install
  // precedente e' stato interrotto a meta' (es. crash, BSOD, blackout).
  SENTINEL_DIR  = '{localappdata}\RelicToEpub';
  SENTINEL_FILE = 'install.inprogress';

var
  LastLogMsg: String;

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
        LogMsg('CurStepChanged: ssInstall');
        WriteSentinel;
        if WizardForm.StatusLabel <> nil then
          WizardForm.StatusLabel.Caption :=
            'Estrazione componenti applicazione in corso - Attendere prego.';
      end;
    ssPostInstall:
      begin
        LogMsg('CurStepChanged: ssPostInstall');
        if WizardForm.StatusLabel <> nil then
          WizardForm.StatusLabel.Caption :=
            'Installazione dipendenza esterna (pandoc) - Attendere prego.';
      end;
    ssDone:
      begin
        LogMsg('CurStepChanged: ssDone');
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
