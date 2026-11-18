$ErrorActionPreference = 'Stop'
Start-Transcript -Path "$PSScriptRoot\low-clocks-apply.log" -Force
try {
    foreach ($gpu in 0,1,2) {
        & nvidia-smi -i $gpu -pl 100
        if ($LASTEXITCODE -ne 0) { throw 'Power limit failed' }
        $core = if ($gpu -eq 1) { '750,750' } else { '900,900' }
        & nvidia-smi -i $gpu -lgc $core
        if ($LASTEXITCODE -ne 0) { throw 'Core clock lock failed' }
        & nvidia-smi -i $gpu -rmc
        if ($LASTEXITCODE -ne 0) { throw 'Memory clock unlock failed' }
    }
} finally { Stop-Transcript }
