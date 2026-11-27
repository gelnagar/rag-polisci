# PoliSci RAG - install / repair / update (Windows PowerShell 5.1+)
#
# Run it without downloading anything by pressing Win+R and pasting:
#   powershell -ExecutionPolicy Bypass -c "iex ((New-Object Net.WebClient).DownloadString('https://github.com/gelnagar/rag-polisci/releases/latest/download/install.ps1'))"
#
# It replaces only the program files. Downloaded AI models, imported books, digests,
# reviews and settings live in %LOCALAPPDATA%\PoliSciRAG and are never touched.
# The app runs on the official, digitally signed Python runtime from python.org
# (no self-made .exe), which avoids the antivirus false positives of packaged apps.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Repo = 'gelnagar/rag-polisci'
$Base = "https://github.com/$Repo/releases/latest/download"
$Dir  = Join-Path $env:LOCALAPPDATA 'Programs\PoliSciRAG'
$Src  = $env:PSRAG_SOURCE_DIR          # optional: folder with the zips (offline / CI test)
$NoLaunch = [bool]$env:PSRAG_NO_LAUNCH
$AppId = '{BBD4F897-DC1F-49B8-B962-884D11654F0D}_is1'

function Say($msg, $color = 'Cyan') { Write-Host $msg -ForegroundColor $color }

function Get-Payload($name, $out) {
    if ($Src) { Copy-Item (Join-Path $Src $name) $out -Force; return }
    $url = "$Base/$name"
    Say "  downloading $name ..." 'Gray'
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -fsSL --retry 5 --retry-delay 3 -o $out $url
        if ($LASTEXITCODE -eq 0) { return }
    }
    (New-Object Net.WebClient).DownloadFile($url, $out)
}

function Expand-Zip($zip, $dest) {
    New-Item -ItemType Directory -Force $dest | Out-Null
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($tar) {
        & tar.exe -xf $zip -C $dest
        if ($LASTEXITCODE -eq 0) { return }
    }
    Expand-Archive $zip -DestinationPath $dest -Force
}

function Stop-App {
    $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    }
    if ($procs) {
        Say '  closing the running app ...' 'Gray'
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

function New-Shortcut($path, $target, $arguments, $workdir, $icon) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = $target
    $s.Arguments = $arguments
    $s.WorkingDirectory = $workdir
    if ($icon) { $s.IconLocation = $icon }
    $s.Description = 'PoliSci RAG'
    $s.Save()
}

try {
    Say ''
    Say '=== PoliSci RAG: install / repair / update ===' 'Yellow'
    Say "Program folder: $Dir"
    Say 'Your models, books and settings are kept.'
    Say ''

    $tmp = Join-Path $env:TEMP ('psrag-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    New-Item -ItemType Directory -Force $Dir | Out-Null

    Say '[1/5] Downloading the app ...'
    Get-Payload 'PoliSciRAG-app-win64.zip' (Join-Path $tmp 'app.zip')

    Say '[2/5] Stopping any running copy ...'
    Stop-App

    # Keep an existing AI engine (llama.cpp) so it is not downloaded again.
    $keptEngine = $null
    foreach ($old in @((Join-Path $Dir 'app\llama'), (Join-Path $Dir 'llama'))) {
        if (Test-Path (Join-Path $old 'cpu\llama-server.exe')) {
            $keptEngine = Join-Path $tmp 'llama'
            Move-Item $old $keptEngine
            break
        }
    }

    Say '[3/5] Replacing program files ...'
    Get-ChildItem $Dir -Force | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    Expand-Zip (Join-Path $tmp 'app.zip') $Dir

    Say '[4/5] Checking the AI engine ...'
    $llama = Join-Path $Dir 'app\llama'
    if ($keptEngine) { Move-Item $keptEngine $llama }
    if (-not (Test-Path (Join-Path $llama 'cpu\llama-server.exe'))) {
        Get-Payload 'PoliSciRAG-engine-win64.zip' (Join-Path $tmp 'engine.zip')
        Expand-Zip (Join-Path $tmp 'engine.zip') $llama
    } else { Say '  existing engine kept' 'Gray' }

    $py  = Join-Path $Dir 'python\python.exe'
    $pyw = Join-Path $Dir 'python\pythonw.exe'
    $launcher = Join-Path $Dir 'app\launcher.py'
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $check = (& $py -c "import polisci_rag, fastapi, pymupdf, numpy; print('ok')" 2>&1 | Out-String)
    $ErrorActionPreference = $prevEap
    if ($check -notmatch 'ok') { throw "The app's Python runtime failed its self-check: $check" }

    Say '[5/5] Creating shortcuts ...'
    $icon = Join-Path $Dir 'app\app.ico'
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'PoliSci RAG'
    New-Item -ItemType Directory -Force $menu | Out-Null
    Get-ChildItem $menu -Filter *.lnk -ErrorAction SilentlyContinue | Remove-Item -Force
    New-Shortcut (Join-Path $menu 'PoliSci RAG.lnk') $pyw "`"$launcher`"" (Join-Path $Dir 'app') $icon
    $desk = [Environment]::GetFolderPath('Desktop')
    New-Shortcut (Join-Path $desk 'PoliSci RAG.lnk') $pyw "`"$launcher`"" (Join-Path $Dir 'app') $icon
    $unps = Join-Path $Dir 'app\uninstall.ps1'
    New-Shortcut (Join-Path $menu 'Uninstall PoliSci RAG.lnk') 'powershell.exe' `
        "-NoProfile -ExecutionPolicy Bypass -File `"$unps`"" $Dir $null

    # Apps & features entry (replaces the one from the old setup program).
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
    New-Item -Path $key -Force | Out-Null
    $ver = (& $py -c "import polisci_rag; print(polisci_rag.__version__)").Trim()
    Set-ItemProperty $key DisplayName 'PoliSci RAG'
    Set-ItemProperty $key DisplayVersion $ver
    Set-ItemProperty $key Publisher 'PoliSci RAG'
    Set-ItemProperty $key DisplayIcon $icon
    Set-ItemProperty $key InstallLocation $Dir
    Set-ItemProperty $key UninstallString "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$unps`""
    Set-ItemProperty $key NoModify 1 -Type DWord
    Set-ItemProperty $key NoRepair 1 -Type DWord
    Remove-ItemProperty $key QuietUninstallString -ErrorAction SilentlyContinue

    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Say ''
    Say "Done! PoliSci RAG $ver is installed." 'Green'
    if (-not $NoLaunch) {
        Say 'Starting it now - it opens in your web browser.' 'Green'
        Start-Process $pyw -ArgumentList "`"$launcher`"" -WorkingDirectory (Join-Path $Dir 'app')
    }
    Start-Sleep -Seconds 4
}
catch {
    Say ''
    Say "Something went wrong: $($_.Exception.Message)" 'Red'
    Say 'Please send a screenshot of this window. Your books and models were not touched.' 'Red'
    if (-not $NoLaunch) { Read-Host 'Press Enter to close' | Out-Null }
    throw
}
