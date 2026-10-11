; Inno Setup script for the Vox Lupine installer.
; Build with tools\build_installer.ps1 (it runs PyInstaller first, then this).

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif
#define MyAppName "Vox Lupine"
#define MyAppExe "Vox Lupine.exe"

[Setup]
; Keep this AppId the same in every version so upgrades replace the old install.
AppId={{AB697A75-ED9A-46C4-8C85-9336379AF0EF}
AppName={#MyAppName}
AppVersion={#MyVersion}
AppVerName={#MyAppName} {#MyVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Installs for the current user without admin rights (users can choose all users).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=LICENSE
SetupIconFile=assets\vox-lupine.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
OutputDir=dist
OutputBaseFilename=VoxLupine-Setup-{#MyVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "build\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "https://vb-audio.com/Cable/"; Description: "Open the VB-Cable download page (needed once; skip if you have it)"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: postinstall nowait skipifsilent unchecked

; Settings and presets in %APPDATA%\Vox Lupine are kept on uninstall on purpose.
