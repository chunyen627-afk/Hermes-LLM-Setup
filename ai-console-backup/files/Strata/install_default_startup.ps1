$ErrorActionPreference = 'Stop'
$folder = [Environment]::GetFolderPath('Startup')
if (-not $folder.StartsWith('C:\Users\pjunm\AppData\Roaming\Microsoft\Windows\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected Startup folder' }
# Retained entry point: the user now requires manual startup only.
$path = Join-Path $folder '模型＋靜態生圖.lnk'
$target = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$arguments = '"' + (Join-Path $PSScriptRoot 'media_control.py') + '" --mode Image'
if (Test-Path -LiteralPath $path) {
    $ws = New-Object -ComObject WScript.Shell
    $link = $ws.CreateShortcut($path)
    if ($link.TargetPath -ne $target -or $link.Arguments -ne $arguments) { throw 'Unexpected Startup entry; preserved' }
    $backup = Join-Path $env:USERPROFILE '.cache\hermes-strata-backups\manual-control-shortcuts'
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Copy-Item -LiteralPath $path -Destination (Join-Path $backup 'startup-image.lnk') -Force
    Remove-Item -LiteralPath $path
}
if (Test-Path -LiteralPath $path) { throw 'Startup entry still present' }
$evidence = [ordered]@{enabled=$false;profile=$null;path=$path;manual_selection=$true;verified='Startup shortcut absent; actual reboot not performed';time=(Get-Date).ToString('o')}
$evidence | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'default-startup-verification.json') -Encoding utf8
Write-Host 'Automatic startup disabled. Open the desktop control panel and select a mode manually.'
