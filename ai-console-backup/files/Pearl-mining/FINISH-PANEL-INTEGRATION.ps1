$ErrorActionPreference='Stop'
$expected=Join-Path $PSScriptRoot 'krig-1.5.12\krig-miner.exe'
foreach($p in @(Get-CimInstance Win32_Process -Filter "Name='krig-miner.exe'")) {
    if($p.ExecutablePath -ne $expected){throw 'Unexpected miner; refusing to stop'}
    Stop-Process -Id $p.ProcessId -ErrorAction Stop
}
Start-Sleep -Seconds 5
& "$PSScriptRoot\RESTORE-AI-POWER.ps1"
if($LASTEXITCODE -ne 0){throw 'Restore failed'}
'Stopped mining; restored AI power and automatic fans.' | Set-Content "$PSScriptRoot\panel-integration-ready.txt"
