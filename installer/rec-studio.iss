; Instalador de Rec Studio (A4) sobre el bundle onedir de `aurora build`.
;
; Compilar:  ISCC.exe /DMyAppVersion=1.0.0 /DMyAppVersion4=1.0.0.0 installer\rec-studio.iss
; o, mejor, `uv run python tools/check_installer.py` (lee la versión del paquete y prueba la
; instalación silenciosa con el smoke del exe).
;
; Instalación por usuario (`PrivilegesRequired=lowest`): no pide UAC y no necesita administrador.
; `{autopf}` resuelve a %LOCALAPPDATA%\Programs cuando el modo no es admin.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppVersion4
  #define MyAppVersion4 "1.0.0.0"
#endif

#define MyAppName "Rec Studio"
#define MyAppPublisher "Raidel"
#define MyAppURL "https://github.com/YoshiLoL0526/screen-recorder"
#define MyAppExeName "Rec Studio.exe"

[Setup]
AppId={{6127D551-7308-42F6-A67E-75EE9BEACB90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion4}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Instalador de {#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=RecStudio-{#MyAppVersion}-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Rec Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
