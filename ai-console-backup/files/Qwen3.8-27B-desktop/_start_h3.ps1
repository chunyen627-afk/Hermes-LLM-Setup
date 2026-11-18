param([switch]$Coexist)
$ErrorActionPreference = 'Stop'
$h3Root = 'C:\Users\pjunm\ComfyUI-H3'
$python = Join-Path $h3Root '.venv\Scripts\python.exe'
$models = @(
    'diffusion_models\minimax_h3_fl2va_pruned_int8_convrot.safetensors',
    'text_encoders\qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors',
    'vae\minimax_h3_video_vae_int8_convrot.safetensors',
    'vae\minimax_h3_audio_vae_fp32.safetensors'
)
if (-not (Test-Path -LiteralPath $python)) { throw 'H3 Python environment is not installed.' }
if (-not (Test-Path -LiteralPath (Join-Path $h3Root 'h3-model-manifest.json'))) {
    throw 'H3 model download and verification are not complete yet.'
}
foreach ($model in $models) {
    if (-not (Test-Path -LiteralPath (Join-Path $h3Root "models\$model"))) { throw "Missing H3 model: $model" }
}
$launchMutex = New-Object System.Threading.Mutex($false, 'Local\H3-ComfyUI-Launcher')
try { $locked = $launchMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
if (-not $locked) {
    Write-Host 'The H3 launch window is already open. No second instance was started.'
    $launchMutex.Dispose()
    exit 0
}
try {
$listener = Get-NetTCPConnection -LocalPort 8189 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
    if (-not $process.CommandLine.Contains("$h3Root\main.py")) { throw 'Port 8189 belongs to another program.' }
    Write-Host 'H3 is already running. Keep its original window open.'
    Start-Process 'http://127.0.0.1:8189/'
    exit 0
}
if ($Coexist) {
    $llm = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if ($llm) {
        $llmProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($llm[0].OwningProcess)"
        if ($llmProcess.Name -ne 'llama-server.exe' -or $llmProcess.CommandLine -notlike '*--device CUDA0,CUDA2*') {
            throw '27B is not in the dedicated two-GPU coexist mode.'
        }
    }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot '.manual-gpu-mode'), 'Coexist')
    $cudaDevices = '1'
} else {
    & (Join-Path $PSScriptRoot '_switch_gpu_mode.ps1') -Mode H3
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $cudaDevices = '1,2,0'
}
Set-Location -LiteralPath $h3Root
$env:XFORMERS_DISABLED = '1'
$env:PYTHONUNBUFFERED = '1'
Write-Host "H3 visible physical GPUs: $cudaDevices. Primary GPU: RTX 3060." -ForegroundColor Cyan
Write-Host 'Open http://127.0.0.1:8189/ . Closing this window stops H3 and releases GPU memory.'
$ErrorActionPreference = 'Continue'
& cmd.exe /d /c "$python -u $h3Root\main.py --listen 127.0.0.1 --port 8189 --cuda-device $cudaDevices --enable-dynamic-vram --reserve-vram 1 --use-pytorch-cross-attention --auto-launch 2>&1" | Tee-Object -FilePath "$h3Root\h3-console.log"
exit $LASTEXITCODE
} finally {
    $launchMutex.ReleaseMutex()
    $launchMutex.Dispose()
}
