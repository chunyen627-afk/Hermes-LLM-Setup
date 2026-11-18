$ErrorActionPreference='Stop'
if(Get-Process krig-miner -ErrorAction SilentlyContinue){throw 'Stop mining first'}
& 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' "$PSScriptRoot\fan_control.py"
if($LASTEXITCODE -ne 0){throw 'Fan restore failed'}
Enable-ScheduledTask -TaskName MSIAfterburner | Out-Null
Start-ScheduledTask -TaskName MSIAfterburner
Write-Host 'Afterburner restored. Original profile backups are under afterburner-backup-*.'
