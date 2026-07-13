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
#define MyAppVersion "0.1.0"
#define MyAppPublisher "RelicToEpub contributors"
#define MyAppURL "https://github.com/example/relictoepub"
#define MyAppExeName "RelicToEpubUI.exe"
#define MyAppCliName "RelicToEpubCLI.exe"
#define MyAppBootName "RelicToEpubBoot.exe"

[Setup]
; Identificativo univoco installer (sostituiscini prima di release pubblica)
AppId={{A1B2C3D4-E5F6-7890-ABCD-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
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
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppBootName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoCopyright=Copyright (c) 2026

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un'icona sul desktop"; GroupDescription: "Icone:"
Name: "startmenu"; Description: "Crea voci in Start Menu"; GroupDescription: "Icone:"

[Files]
; Cartella principale dell'app (UI + CLI + _internal)
Source: "..\dist\RelicToEpub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Cartella del bootstrap (separata perché built con COLLECT name="boot")
Source: "..\dist\boot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; L'MSI di pandoc da installare come dipendenza esterna
Source: "..\pandoc-3.10-windows-x86_64.msi"; DestDir: "{tmp}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName} UI"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenu
Name: "{group}\{#MyAppName} CLI"; Filename: "{app}\{#MyAppCliName}"; Tasks: startmenu
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; Tasks: startmenu
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{commondesktop}\{#MyAppName} (CLI)"; Filename: "{app}\{#MyAppCliName}"; Tasks: desktopicon

[Run]
; Pandoc silent install — con messaggio di progresso visibile all'utente
Filename: "msiexec.exe"; \
    Parameters: "/i ""{tmp}\pandoc-3.10-windows-x86_64.msi"" /qb! ADDLOCAL=ALL REBOOT=ReallySuppress /norestart"; \
    StatusMsg: "Installazione dipendenza esterna: pandoc 3.10 — attendere prego…"; \
    Check: PandocNeeded; Flags: waituntilterminated

; Opzionale: aprire la cartella di installazione al termine
Filename: "{app}"; Description: "Apri la cartella di installazione"; Flags: nowait postinstall skipifsilent runmaximized

[UninstallRun]
; (vuoto per ora)

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\*.log"

[Code]
// ======================================================================
//  Codice Pascal-script di Inno Setup — feedback continuo all'utente
// ======================================================================

// Determina se pandoc è già installato nel sistema (skip se sì).
// Inno Setup usa due [Run] separate per la condizione.
function PandocNeeded(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // Verifica rapida: pandoc nel PATH?
  if Exec('cmd.exe', '/c where pandoc', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Result := False;
  end;
end;

// Hook chiamato a ogni passo dell'installazione.
// Usiamo WizardForm.StatusLabel nativo di Inno Setup per mostrare testo.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if WizardForm.StatusLabel <> nil then
  begin
    case CurStep of
      ssInstall:
        WizardForm.StatusLabel.Caption :=
          'Estrazione componenti applicazione in corso — Attendere prego.';
      ssPostInstall:
        WizardForm.StatusLabel.Caption :=
          'Installazione dipendenza esterna (pandoc) — Attendere prego.';
      ssDone:
        WizardForm.StatusLabel.Caption :=
          'Configurazione finale (Start Menu, registro) — Quasi terminato.';
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;
