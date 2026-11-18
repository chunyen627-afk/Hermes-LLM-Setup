$ErrorActionPreference='Stop'
Start-Transcript -Path "$PSScriptRoot\verify-fan-cleanup.log" -Force
try {
    $miners=@(Get-CimInstance Win32_Process -Filter "Name='krig-miner.exe'")
    if($miners.Count -ne 1){throw 'Expected one miner'}
    $guard=Get-CimInstance Win32_Process -Filter "ProcessId=$($miners[0].ParentProcessId)"
    if($guard.CommandLine -notmatch 'Pearl-mining\\guarded_miner.py'){throw 'Guard identity mismatch'}
    Stop-Process -Id $guard.ProcessId
    Start-Sleep -Seconds 4
    if(Get-Process -Id $miners[0].ProcessId -ErrorAction SilentlyContinue){throw 'Child miner survived'}
    & 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' -c "import pynvml as n; n.nvmlInit(); policies=[n.nvmlDeviceGetFanControlPolicy_v2(n.nvmlDeviceGetHandleByIndex(i),f) for i in range(3) for f in range(2)]; print('Fan policies:',policies); assert all(p==0 for p in policies), 'Auto restore failed'"
    if($LASTEXITCODE -ne 0){throw 'Fan restore verification failed'}
    Write-Host 'PASS: guard exit stopped miner and restored all six fan policies.'
} finally { Stop-Transcript }
