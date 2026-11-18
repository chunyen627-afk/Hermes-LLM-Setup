$ErrorActionPreference = 'Stop'
$results = foreach ($gpu in 0,1,2) {
    & nvidia-smi -i $gpu -pl 100 2>&1
    if ($LASTEXITCODE -ne 0) { throw "GPU $gpu power limit failed" }
}
$results | Set-Content -LiteralPath "$PSScriptRoot\power-apply.log"
& nvidia-smi --query-gpu=index,uuid,power.limit --format=csv | Add-Content -LiteralPath "$PSScriptRoot\power-apply.log"
