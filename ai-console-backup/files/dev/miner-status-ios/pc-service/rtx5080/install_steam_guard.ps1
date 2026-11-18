# Registers the logon task "SteamCommitGuard" (this user, not elevated) and starts it now.
# Remove: Unregister-ScheduledTask SteamCommitGuard -Confirm:$false
$script = Join-Path $PSScriptRoot 'steam_commit_guard.ps1'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
# Windows Terminal is the default console here and ignores -WindowStyle Hidden (an empty
# PowerShell tab stayed open, 2026-11-01); a headless conhost never shows a window.
$conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
$action = New-ScheduledTaskAction -Execute $conhost -Argument "--headless `"$ps`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Stop-ScheduledTask -TaskName 'SteamCommitGuard' -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName 'SteamCommitGuard' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'SteamCommitGuard'
Start-Sleep -Seconds 5
'task state: ' + (Get-ScheduledTask SteamCommitGuard).State
