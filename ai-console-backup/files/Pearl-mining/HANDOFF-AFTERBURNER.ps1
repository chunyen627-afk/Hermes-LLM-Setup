$ErrorActionPreference='Stop'
Start-Transcript -Path "$PSScriptRoot\handoff-afterburner.log" -Force
try {
    if(Get-Process krig-miner -ErrorAction SilentlyContinue){throw 'Stop mining before handoff'}
    $backup=Join-Path $PSScriptRoot ('afterburner-backup-'+(Get-Date -Format yyyyMMdd-HHmmss))
    New-Item -ItemType Directory -Path $backup | Out-Null
    Copy-Item -LiteralPath 'C:\Program Files (x86)\MSI Afterburner\Profiles' -Destination $backup -Recurse
    Export-ScheduledTask -TaskName MSIAfterburner | Set-Content -LiteralPath (Join-Path $backup 'MSIAfterburner-task.xml') -Encoding UTF8
    Stop-Process -Name MSIAfterburner -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName MSIAfterburner | Out-Null
    & 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' "$PSScriptRoot\fan_probe.py"
    if($LASTEXITCODE -ne 0){throw 'Fan takeover probe failed; keep mining stopped'}
} finally { Stop-Transcript }
