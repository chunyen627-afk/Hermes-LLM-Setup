# Registers MinerStatusService: at logon and every minute after, keep_status_service.py starts the
# miner status service (office_aggregator.py) if it is not running (user asked 2026-11-11 that every
# office function comes back by itself after a reboot). pythonw: no window flashes each minute.
$dir = 'C:\Users\pjunm\dev\miner-status-ios\pc-service'
$py = 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\pythonw.exe'
$action = New-ScheduledTaskAction -Execute $py -Argument "-I `"$dir\keep_status_service.py`"" -WorkingDirectory $dir
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = 'PT1M'
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'MinerStatusService' -Action $action -Trigger @($logon, $repeat) `
    -Settings $settings -Principal $principal -Description 'Keeps the miner status service (office_aggregator.py) running' -Force |
    Select-Object TaskName, State
