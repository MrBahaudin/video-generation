; Zakariya Automator — Inno Setup Script v1.4.0
; Creates a proper Windows installer (.exe)
; Includes: App + ffmpeg + Playwright Chromium (no internet needed)

#define MyAppName "Zakariya Automator"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "MrBahaudin"
#define MyAppExeName "ZakariyaAutomator.exe"
#define MyAppDir "dist\ZakariyaAutomator"

[Setup]
AppId={{B3A1F2C4-7D9E-4B2A-9F3C-1E8D7A6B5C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=ZakariyaAutomator-v{#MyAppVersion}-Setup
SetupIconFile=app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; Minimum Windows 10
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Main app files (EXE + all DLLs + Python libs)
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; ffmpeg (watermark removal)
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion

; Playwright Chromium browser (bundled — works offline on any PC)
Source: "{#MyAppDir}\playwright_browsers\*"; DestDir: "{app}\playwright_browsers"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
