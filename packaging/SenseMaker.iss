#define MyAppName "SenseMaker"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "SenseMaker"
#define MyAppExeName "SenseMaker.exe"

[Setup]
AppId={{B18C45E5-D38B-4261-826A-E2196A92332F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=1.1.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename=SenseMaker-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
WizardImageFile=assets\wizard-large.bmp
WizardSmallImageFile=assets\wizard-small.bmp
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
SourceDir=..

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Components]
Name: "core"; Description: "SenseMaker application"; Types: full custom; Flags: fixed

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\SenseMaker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion; Components: core
Source: "PRIVACY.md"; DestDir: "{app}"; Flags: ignoreversion; Components: core
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion; Components: core
Source: "THIRD_PARTY_SOURCE.md"; DestDir: "{app}"; Flags: ignoreversion; Components: core
Source: "SECURITY.md"; DestDir: "{app}"; Flags: ignoreversion; Components: core
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core

; NVIDIA cuBLAS is proprietary and not GPL-compatible, so it is not bundled.
; GPU users install the NVIDIA CUDA runtime separately.

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Privacy Notice"; Filename: "{app}\PRIVACY.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"
