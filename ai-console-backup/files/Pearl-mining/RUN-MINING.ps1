$ErrorActionPreference='Stop'
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\PREPARE-MINING.ps1"
    if($LASTEXITCODE -ne 0){throw 'Preflight failed'}
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\SET-LOW-CLOCKS.ps1"
    if($LASTEXITCODE -ne 0){throw 'GPU settings failed'}
    & 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' "$PSScriptRoot\guarded_miner.py"
} catch { Write-Host $_ -ForegroundColor Red }
finally {
    Write-Host 'Mining stopped. Use RESTORE-AI.bat before running AI.'
    Read-Host 'Press Enter to close'
}
