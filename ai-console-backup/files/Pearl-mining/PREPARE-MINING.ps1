param([switch]$SkipConsoleStop)
$ErrorActionPreference = 'Stop'
try {
    if (Get-Process -Name 'krig-miner' -ErrorAction SilentlyContinue) {
        throw 'Mining is already running. Close its window before starting another session.'
    }
    Write-Host 'Stopping Local AI Console services (Hermes desktop stays open)...'
    if (-not $SkipConsoleStop) {
        & 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe' 'C:\Users\pjunm\Strata\media_control.py' --action Stop
        if ($LASTEXITCODE -ne 0) { throw 'AI Console stop request failed.' }
    }
    $deadline = (Get-Date).AddSeconds(60)
    $stable = 0
    do {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -in @(8001,8188,8189,5000) })
        $raw = & nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits
        if ($LASTEXITCODE -ne 0) { throw 'GPU telemetry unavailable; mining blocked.' }
        $rows = @($raw | ConvertFrom-Csv -Header 'index','memory')
        if ($rows.Count -ne 3) { throw 'Expected three GPUs; mining blocked.' }
        # 3 GiB, not 2 (2026-11-10): the display GPU (index 2) sits at 2.2-2.5 GiB with the desktop,
        # browsers and Electron apps alone, so mining could not start; a model or ComfyUI left
        # behind holds far more than 3 GiB, which is what this gate is for.
        $busy = @($rows | Where-Object { [double]$_.memory -gt 3072 })
        Write-Host ('GPU memory MiB: ' + (($rows | ForEach-Object { "GPU$($_.index)=$($_.memory.Trim())" }) -join ', '))
        if ($listeners.Count -eq 0 -and $busy.Count -eq 0) { $stable++ } else { $stable=0 }
        if ($stable -ge 3) {
            Write-Host 'AI ports closed; all three GPUs below 3 GiB for three checks. Starting mining.'
            exit 0
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'GPU release timed out. Close remaining GPU apps or standalone AI servers and retry. Mining was NOT started.'
} catch {
    Write-Host ('NOT STARTED: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
