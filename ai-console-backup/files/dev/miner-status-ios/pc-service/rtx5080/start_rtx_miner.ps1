# Start 5080 mining (used by maintenance scripts): through the PearlAutoStart supervisor, which cools the GPU
# first and keeps mining until a stop leaves HOLD. Without the task, launch session.py directly as before.
$root = 'C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining'
Remove-Item (Join-Path $root 'HOLD'), (Join-Path $root 'STOP_REQUEST') -Force -ErrorAction SilentlyContinue
if (Get-ScheduledTask -TaskName 'PearlAutoStart' -ErrorAction SilentlyContinue) {
    Start-ScheduledTask -TaskName 'PearlAutoStart'
    "task: $((Get-ScheduledTask -TaskName 'PearlAutoStart').State)"
    exit
}
$python = Join-Path $root '.venv\Scripts\python.exe'
$session = Join-Path $root 'session.py'
$commandLine = '"' + $python + '" "' + $session + '"'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $commandLine; CurrentDirectory = $root}
"create: $($result.ReturnValue) pid $($result.ProcessId)"
