$ErrorActionPreference='Stop'
Start-Transcript -Path "$PSScriptRoot\thermal-update.log" -Force
try {
    $miners=@(Get-CimInstance Win32_Process -Filter "Name='krig-miner.exe'")
    if($miners.Count -gt 1){throw 'Multiple miners; abort'}
    if($miners.Count -eq 1){
        if($miners[0].ExecutablePath -ne "$PSScriptRoot\krig-1.5.12\krig-miner.exe"){throw 'Unexpected miner path'}
        $guard=Get-CimInstance Win32_Process -Filter "ProcessId=$($miners[0].ParentProcessId)"
        if($guard.CommandLine -notmatch 'Pearl-mining\\guarded_miner.py'){throw 'Unexpected guard'}
        $console=Get-CimInstance Win32_Process -Filter "ProcessId=$($guard.ParentProcessId)"
        Stop-Process -Id $miners[0].ProcessId
        for($attempt=0;$attempt -lt 10;$attempt++){
            if(-not(Get-Process -Id $guard.ProcessId -ErrorAction SilentlyContinue)){break}
            Start-Sleep -Seconds 1
        }
        if(Get-Process -Id $guard.ProcessId -ErrorAction SilentlyContinue){throw 'Old guard did not exit'}
        Start-Sleep -Seconds 3
        if($console.CommandLine -match 'RUN-MINING.ps1' -and $console.ProcessId -ne $PID){Stop-Process -Id $console.ProcessId}
    }
    & 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' -c "import sys,time; sys.path.insert(0,r'C:\Users\pjunm\Pearl-mining'); from thermal_control import Thermal; from guarded_miner import telemetry; t=Thermal(); rows=telemetry(); [r.update(temperature=76) for r in rows]; print('Synthetic 76C event, actual GPUs remain cool:',t.update(rows)); time.sleep(3); actual=telemetry(); print('Readback:',actual); assert all(r['limit']<=t.settings[i][0]+1 and r['core']<=t.settings[i][1]+30 for i,r in enumerate(actual))"
    if($LASTEXITCODE -ne 0){throw 'Hardware derating readback failed'}
} finally { Stop-Transcript }
& "$PSScriptRoot\RUN-MINING.ps1"
