# PoliSci RAG uninstaller (program files; optionally also models & library)
$ErrorActionPreference = 'Continue'
$Dir  = Join-Path $env:LOCALAPPDATA 'Programs\PoliSciRAG'
$Data = Join-Path $env:LOCALAPPDATA 'PoliSciRAG'
Write-Host 'Uninstalling PoliSci RAG ...' -ForegroundColor Yellow
Get-Process -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'PoliSci RAG') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PoliSci RAG.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{BBD4F897-DC1F-49B8-B962-884D11654F0D}_is1' -Recurse -Force -ErrorAction SilentlyContinue
$ans = Read-Host "Also delete your AI models (5.3 GB), imported books, reviews and settings in $Data ? (y/N)"
if ($ans -match '^[yY]') { Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue; Write-Host 'Data deleted.' }
else { Write-Host "Your data was kept in $Data" }
Set-Location $env:TEMP
Remove-Item $Dir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host 'PoliSci RAG has been removed.' -ForegroundColor Green
Start-Sleep -Seconds 3
