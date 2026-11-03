#define AppVersion GetEnv('CAMERA_VERSION')
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{AF207B35-0352-4EB0-B6D1-FE1C977567E7}
AppName=VRC Kameracontroller
AppVersion={#AppVersion}
AppPublisher=Berghausa
AppPublisherURL=https://github.com/Berghausa/VRC-Kameracontroller
DefaultDirName={localappdata}\Programs\VRC-Kameracontroller
DefaultGroupName=VRC Kameracontroller
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=VRC-Kameracontroller-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\VRC-Kameracontroller.exe
CloseApplications=yes
CloseApplicationsFilter=VRC-Kameracontroller.exe
RestartApplications=no
SetupMutex=VRC-Kameracontroller-Setup
[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; Flags: unchecked
[Files]
Source: "..\dist\VRC-Kameracontroller.exe"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\VRC Kameracontroller"; Filename: "{app}\VRC-Kameracontroller.exe"
Name: "{autodesktop}\VRC Kameracontroller"; Filename: "{app}\VRC-Kameracontroller.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\VRC-Kameracontroller.exe"; Description: "VRC Kameracontroller starten"; Flags: nowait postinstall skipifsilent
