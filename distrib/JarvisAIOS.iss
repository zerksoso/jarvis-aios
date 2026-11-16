; Script Inno Setup 6 pour l'installeur Windows par utilisateur de Jarvis AIOS.
; Passer la version via : ISCC.exe /DAppVersion=X.Y.Z distrib\JarvisAIOS.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7E4B2F9A-8C3D-4A1E-9B6F-2D5A1C8E4F01}
AppName=Jarvis AIOS
AppVersion={#AppVersion}
AppVerName=Jarvis AIOS {#AppVersion}
AppPublisher=Contributeurs de Jarvis AIOS
DefaultDirName={localappdata}\Programs\JarvisAIOS
DefaultGroupName=Jarvis AIOS
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=..\dist-release
OutputBaseFilename=JarvisAIOS-Setup-{#AppVersion}
SetupIconFile=..\assets\jarvis.ico
UninstallDisplayIcon={app}\Jarvis.exe
UninstallDisplayName=Jarvis AIOS
LicenseFile=..\LICENSE
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Lancer Jarvis AIOS automatiquement au démarrage de Windows"; GroupDescription: "Démarrage :"; Flags: unchecked

[Files]
Source: "..\build\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\Jarvis.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Jarvis AIOS"; Filename: "{app}\Jarvis.exe"; IconFilename: "{app}\Jarvis.exe"
Name: "{group}\Arrêter Jarvis AIOS"; Filename: "{app}\Jarvis.exe"; Parameters: "--arreter"; IconFilename: "{app}\Jarvis.exe"
Name: "{group}\{cm:UninstallProgram,Jarvis AIOS}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Jarvis AIOS"; Filename: "{app}\Jarvis.exe"; IconFilename: "{app}\Jarvis.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "JarvisAIOS"; ValueData: """{app}\Jarvis.exe"" --silencieux"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\Jarvis.exe"; Description: "{cm:LaunchProgram,Jarvis AIOS}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Jarvis.exe"; Parameters: "--silencieux"; Flags: nowait runhidden; Check: WizardSilent

[UninstallRun]
Filename: "{app}\Jarvis.exe"; Parameters: "--arreter"; Flags: runhidden waituntilterminated; RunOnceId: "ArreterJarvis"

[UninstallDelete]
; Fichiers générés après l'installation (caches Python, journaux de l'overlay, dossiers de mise à jour). Les données utilisateur sont ailleurs.
Type: filesandordirs; Name: "{app}"

[Code]
var
  SupprimerDonneesUtilisateur: Boolean;

function ObtenirDossierDonnees(): String;
begin
  Result := AddBackslash(GetEnv('USERPROFILE')) + 'JarvisAIOS';
end;

function InitializeUninstall(): Boolean;
var
  DataDir: String;
begin
  SupprimerDonneesUtilisateur := False;
  Result := True;
  DataDir := ObtenirDossierDonnees();

  if UninstallSilent() or (not DirExists(DataDir)) then
  begin
    Exit;
  end;

  if SuppressibleMsgBox(
    'Jarvis AIOS va être désinstallé.' + #13#10 + #13#10 +
    'Voulez-vous AUSSI supprimer vos données personnelles (second cerveau, agents, historique) ?' + #13#10 +
    DataDir + #13#10 + #13#10 +
    'Oui : tout supprimer (irréversible).  Non : conserver vos données.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
  begin
    SupprimerDonneesUtilisateur := True;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if (CurUninstallStep = usPostUninstall) and SupprimerDonneesUtilisateur then
  begin
    DataDir := ObtenirDossierDonnees();
    if DirExists(DataDir) then
    begin
      DelTree(DataDir, True, True, True);
    end;
  end;
end;
