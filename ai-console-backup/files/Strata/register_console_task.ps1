# Registers the AI console as a scheduled task that starts at logon (user asked 2026-11-11 that every
# office function comes back by itself after a reboot). With no --mode the console does its default:
# it takes over a running mining session or starts Pearl mining, and it starts the miner status
# service (office_aggregator.py), which in turn keeps office2 / VCU118 mining (keep_mining).
# No repeat trigger: a second launch only brings the open window to the front.
$dir = 'C:\Users\pjunm\Strata'
$py = 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\pythonw.exe'
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\media_control.py`"" -WorkingDirectory $dir
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = 'PT1M'  # let ZeroTier, Google Drive and the GPU driver settle first
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'AIConsole' -Action $action -Trigger $logon `
    -Settings $settings -Principal $principal -Description 'AI console (Strata media_control.py): default Pearl mining + miner status service' -Force |
    Select-Object TaskName, State
