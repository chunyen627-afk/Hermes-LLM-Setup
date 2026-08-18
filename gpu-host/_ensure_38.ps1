param([int]$Port = 8001, [int]$Ctx = 163840,
      [ValidateSet("off","low","high")][string]$Think = "low",
      [ValidateSet("chat","fw")][string]$Mode = "chat")

# $Mode 決定取樣參數：
#   chat = 通用（temp 0.7，回答較自然多樣）
#   fw   = 韌體/技術（temp 0.3 + 低 top-p，數字與暫存器名不易飄，回答更確定）
switch ($Mode) {
  "chat" { $sampleArgs = @("--temp","0.7","--top-p","0.80","--top-k","20","--min-p","0.0","--presence-penalty","1.5") }
  "fw"   { $sampleArgs = @("--temp","0.3","--top-p","0.70","--top-k","20","--min-p","0.05","--presence-penalty","0.0","--repeat-penalty","1.05") }
}

# $Think 決定思考深度：
#   off  = 完全不思考（閒聊、簡單問答最快）
#   low  = 適度思考（預設，寫 code / 一般任務）
#   high = 深度思考（STM32 韌體、硬體設計、除錯這類複雜推理）
switch ($Think) {
  "off"  { $reasonArgs = @("--reasoning","off","--reasoning-budget","0") }
  "low"  { $reasonArgs = @("--reasoning","on","--reasoning-effort","low","--reasoning-budget","2048") }
  "high" { $reasonArgs = @("--reasoning","on","--reasoning-effort","medium","--reasoning-budget","4096") }
}

# Qwen3.8-27B + MTP (multi-token prediction speculative decoding)
# Hybrid attention: only 16/64 layers are full-attention -> KV cache is cheap

# Test-NetConnection 沒有 timeout、port 剛關會卡住，直接試 REST 即可
$up = $true
if ($up) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 3 -ErrorAction Stop
        if ($r.data[0].id -like "*qwen38*") {
            Write-Host "[OK] Qwen3.8-27B already on :$Port ($($r.data[0].id))" -ForegroundColor Green
            exit 0
        }
        Write-Host "[INFO] Port $Port has another model ($($r.data[0].id)), switching..." -ForegroundColor Yellow
    } catch {}
}

Write-Host "Killing existing LLM servers to free VRAM..." -ForegroundColor Yellow
Get-Process llama-server, unsloth -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Write-Host "  Killed PID $($_.Id) ($($_.ProcessName))" } catch {}
}
Start-Sleep -Seconds 5

$llama = 'C:\Users\pjunm\.unsloth\llama.cpp-b10435\llama-server.exe'
$model = 'C:\Users\pjunm\.cache\huggingface\hub\Qwen3.8-27B-UD-Q4_K_XL.gguf'

if (-not (Test-Path $model)) {
    Write-Host "[ERROR] Model not found: $model" -ForegroundColor Red
    exit 1
}

Write-Host "Starting Qwen3.8-27B on :$Port (ctx $Ctx, MTP on, think=$Think, mode=$Mode)..." -ForegroundColor Cyan
$launchArgs = @(
    '--model', $model,
    '--alias', 'qwen38_mtp',
    '--host', '0.0.0.0',
    '--port', "$Port",
    '--jinja',
    '--chat-template-file', "$PSScriptRoot\qwen38_chat_template.jinja",
    '--parallel', '1',
    '--ctx-size', "$Ctx",
    '-fa', 'on',
    # MTP: uses the model's built-in multi-token prediction head, no separate drafter
    '--spec-type', 'draft-mtp',
    '--spec-draft-n-max', '3',
    '--cache-type-k', 'q4_0',
    '--cache-type-v', 'q4_0',
    '--n-gpu-layers', '999',
    '--tensor-split', '6,12,13',
    '-sm', 'layer',
    '--batch-size', '2048',
    '--ubatch-size', '512',
    # === Prompt cache 修復（hybrid/recurrent 架構專用）===

    # --swa-full            : 用完整 SWA cache，恢復被 sliding window 破壞的快取操作

    # --checkpoint-min-step : 預設 8192 太大，一般對話輪次根本建不出 checkpoint

    # --cache-ram -1        : 不限制快取記憶體（預設只有 8192 MiB）

    '--swa-full',

    '--checkpoint-min-step', '0',

    '--ctx-checkpoints', '32',

    '--cache-ram', '-1',

    '--no-warmup'
)
$launchArgs += $reasonArgs
$launchArgs += $sampleArgs
Start-Process -FilePath $llama -ArgumentList $launchArgs -WindowStyle Minimized

Write-Host "Waiting up to 180s for model load..." -ForegroundColor Cyan
for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 2
        if ($r.data) {
            Write-Host "[OK] Ready ($i s) - $($r.data[0].id)" -ForegroundColor Green
            exit 0
        }
    } catch {}
}
Write-Host "[ERROR] Not ready in 180s" -ForegroundColor Red
exit 1
