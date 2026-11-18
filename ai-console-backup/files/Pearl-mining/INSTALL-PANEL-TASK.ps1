# One-time setup, run as administrator: the scheduled task the AI console uses to start mining (and to
# restore AI GPU settings) without a UAC prompt, so the phone app can start this PC like the RTX 5080.
# The task takes no arguments; panel_task.py only acts on a fresh request from the running AI console.
# Remove it with: Unregister-ScheduledTask -TaskName PearlPanelBridge -Confirm:$false
$ErrorActionPreference = 'Stop'
$python = 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe'
$folder = 'C:\Users\pjunm\Pearl-mining'
$command = "& '$python' '$folder\panel_task.py'"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -Command `"$command`"" -WorkingDirectory $folder
$principal = New-ScheduledTaskPrincipal -UserId 'OFFICE_OPEN\pjunm' -LogonType Interactive -RunLevel Highest
# Mining runs for days: no time limit; one at a time; never stopped for battery.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'PearlPanelBridge' -Action $action -Principal $principal -Settings $settings -Description 'AI console: start Pearl mining / restore AI GPU settings without a UAC prompt (Pearl-mining\panel_task.py)' -Force | Out-Null
$task = Get-ScheduledTask -TaskName 'PearlPanelBridge'
"registered: $($task.TaskName) runlevel $($task.Principal.RunLevel) user $($task.Principal.UserId)"
