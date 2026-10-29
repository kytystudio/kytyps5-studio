; Build with: "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss   (after build.ps1)
[Setup]
AppId={{6B1D2A52-6C0E-4C6B-9B0C-4B5A1E7D2F10}
AppName=KytyPS5 Studio
AppVersion=1.2.0
AppPublisher=KytyPS5 Studio
DefaultDirName={autopf}\KytyPS5 Studio
DefaultGroupName=KytyPS5 Studio
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer
OutputBaseFilename=KytyPS5Studio-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\KytyPS5Studio.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\KytyPS5Studio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "LEGAL.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\KytyPS5 Studio"; Filename: "{app}\KytyPS5Studio.exe"
Name: "{autodesktop}\KytyPS5 Studio"; Filename: "{app}\KytyPS5Studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KytyPS5Studio.exe"; Description: "Launch KytyPS5 Studio"; Flags: nowait postinstall skipifsilent
