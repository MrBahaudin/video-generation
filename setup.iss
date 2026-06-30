[Setup]
AppName=Zakariya Automator
AppVersion=1.2.0
AppVerName=Zakariya Automator v1.2.0
AppPublisher=Zakariya Automator
DefaultDirName={autopf}\Zakariya Automator
DefaultGroupName=Zakariya Automator
OutputBaseFilename=ZakariyaAutomator_v1.2.0_Setup
OutputDir=dist
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\ZakariyaAutomator.exe
WizardStyle=modern
DisableProgramGroupPage=yes
PrivilegesRequired=admin
CloseApplications=force
CloseApplicationsFilter=ZakariyaAutomator.exe

[Tasks]
Name: "desktopicon"; Description: "Create Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "dist\ZakariyaAutomator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Zakariya Automator"; Filename: "{app}\ZakariyaAutomator.exe"
Name: "{group}\Uninstall Zakariya Automator"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Zakariya Automator"; Filename: "{app}\ZakariyaAutomator.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ZakariyaAutomator.exe"; Description: "Launch Zakariya Automator"; Flags: nowait postinstall skipifsilent
