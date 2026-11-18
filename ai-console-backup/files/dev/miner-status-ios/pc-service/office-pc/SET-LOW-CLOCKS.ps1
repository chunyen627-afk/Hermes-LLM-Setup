$ErrorActionPreference = 'Stop'
Start-Transcript -Path "$PSScriptRoot\low-clocks-apply.log" -Force
try {
    Stop-Process -Name MSIAfterburner -ErrorAction SilentlyContinue
    Get-ScheduledTask -TaskName MSIAfterburner -ErrorAction SilentlyContinue | Disable-ScheduledTask | Out-Null
    foreach ($gpu in 0,1,2) {
        $limits = @(110,100,125)
        $cores = @(1500,1350,1800)
        & nvidia-smi -i $gpu -pl $limits[$gpu]
        if ($LASTEXITCODE -ne 0) { throw 'Power limit failed' }
        $core = "$($cores[$gpu]),$($cores[$gpu])"
        & nvidia-smi -i $gpu -lgc $core
        if ($LASTEXITCODE -ne 0) { throw 'Core clock lock failed' }
        # 2026-10-29: memory 5001 MHz on the 3070 (GPU0) leaves the 110 W cap to the core: 28.0 -> 34.8
        # TH/s. The sandwiched 3060 (GPU1) keeps its hashrate and runs 14 W / 5 C cooler. The outer 3060
        # (GPU2) lost 15 % at 5001, so it stays at the default. RESTORE-AI-POWER.ps1 resets all of them.
        $memory = @(5001,5001,0)
        if ($memory[$gpu]) { & nvidia-smi -i $gpu -lmc "$($memory[$gpu]),$($memory[$gpu])" }
        else { & nvidia-smi -i $gpu -rmc }
        if ($LASTEXITCODE -ne 0) { throw 'Memory clock setting failed' }
    }
    Start-Sleep -Seconds 3
} finally { Stop-Transcript }
