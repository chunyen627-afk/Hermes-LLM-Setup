$ErrorActionPreference = 'Stop'
& 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' "$PSScriptRoot\fan_control.py"
if($LASTEXITCODE -ne 0){throw 'Fan automatic policy restore failed'}
# Run as administrator after stopping the miner. Original limits recorded before mining.
$limits = @('192','110.50','136')
for ($gpu=0; $gpu -lt 3; $gpu++) {
    & nvidia-smi -i $gpu -rgc
    & nvidia-smi -i $gpu -rmc
    & nvidia-smi -i $gpu -pl $limits[$gpu]
    if ($LASTEXITCODE -ne 0) { throw "GPU $gpu power restore failed" }
}
& nvidia-smi --query-gpu=index,uuid,power.limit --format=csv
