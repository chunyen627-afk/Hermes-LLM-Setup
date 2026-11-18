# Registers the thermometer gateway as a scheduled task: starts at logon, is restarted
# by Task Scheduler if launching fails, and is started again by a 1-minute repeat
# whenever it is not running (crash, kill). IgnoreNew keeps a single instance.
$dir = 'C:\Users\pjunm\dev\thermo-gateway'
$py = 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\pythonw.exe'
$action = New-ScheduledTaskAction -Execute $py `
    -Argument "`"$dir\thermo_gateway.py`" --config `"$dir\thermo-gateway.json`"" -WorkingDirectory $dir
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'ThermoGateway' -Action $action -Trigger @($logon, $repeat) `
    -Settings $settings -Principal $principal -Description 'Bluetooth thermometer gateway (thermo-ios pc-gateway)' -Force |
    Select-Object TaskName, State
