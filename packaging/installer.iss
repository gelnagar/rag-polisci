; Inno Setup script - compiled in CI:  iscc /DAppVersion=0.2.0 packaging\installer.iss
; Packs build\PoliSciRAG (signed python.org runtime + app source + llama.cpp engine).
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{BBD4F897-DC1F-49B8-B962-884D11654F0D}
AppName=PoliSci RAG
AppVersion={#AppVersion}
AppPublisher=PoliSci RAG
DefaultDirName={localappdata}\Programs\PoliSciRAG
DefaultGroupName=PoliSci RAG
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=PoliSciRAG-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\app\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
CloseApplications=force
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; remove files of version 0.1.0 (PyInstaller build)
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\PoliSciRAG.exe"
Type: filesandordirs; Name: "{app}\llama"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app\polisci_rag"

[Files]
Source: "..\build\PoliSciRAG\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PoliSci RAG"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\app.ico"
Name: "{group}\Uninstall PoliSci RAG"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PoliSci RAG"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; Description: "Launch PoliSci RAG (first launch downloads the 5.3 GB AI models)"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process | Where-Object {{ $_.Path -like '{app}\*' } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Messages]
FinishedLabel=PoliSci RAG is installed. On first launch it downloads its AI models (about 5.3 GB) - keep the computer online. Your library and models are stored in your AppData\Local\PoliSciRAG folder and are kept if you uninstall.
