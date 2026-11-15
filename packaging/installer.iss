#define AppName "AI Shorts Studio"
#define AppVersion "0.1.1"
[Setup]
AppId={{347EA3F0-4920-4F49-B96A-361972E97386}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\AI Shorts Studio
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=AIShortsStudio-0.1.1-win64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\AIShortsStudio.exe
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "..\dist\AIShortsStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\AIShortsStudio.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\AIShortsStudio.exe"
[Run]
Filename: "{app}\AIShortsStudio.exe"; Description: "Запустить AI Shorts Studio"; Flags: nowait postinstall skipifsilent
; No deletion rule for user data: uninstall preserves projects and settings.
