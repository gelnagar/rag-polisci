; Inno Setup script - compiled in CI:  iscc /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.1.0"
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
UninstallDisplayIcon={app}\PoliSciRAG.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\PoliSciRAG\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PoliSci RAG"; Filename: "{app}\PoliSciRAG.exe"
Name: "{group}\Uninstall PoliSci RAG"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PoliSci RAG"; Filename: "{app}\PoliSciRAG.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PoliSciRAG.exe"; Description: "Launch PoliSci RAG (first launch downloads the 5.3 GB AI models)"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM llama-server.exe /T"; Flags: runhidden; RunOnceId: "KillEngine"

[Messages]
FinishedLabel=PoliSci RAG is installed. On first launch it downloads its AI models (about 5.3 GB) - keep the computer online. Your library and models are stored in your AppData\Local\PoliSciRAG folder and are kept if you uninstall.
