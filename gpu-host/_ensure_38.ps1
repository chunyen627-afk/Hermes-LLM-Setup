param([int]$Port = 8001, [int]$Ctx = 163840, [int]$Slots = 2,
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
$model = 'C:\Users\pjunm\.cache\huggingface\hub\Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf'

$mmproj = 'C:\Users\pjunm\.cache\huggingface\hub\mmproj-Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-BF16.gguf'

if (-not (Test-Path -LiteralPath $model)) {
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
    '--parallel', "$Slots",
    '--ctx-size', "$Ctx",
    # 預設 disabled -> ctx 滿了那輪請求直接失敗，不是優雅降級。
    # 開著的話 llama.cpp 會丟掉最舊的 token 繼續跑，等於幫 Hermes 的
    # 自動壓縮爭取時間。2026-08-27 彈珠台任務差點因此中斷。
    '--context-shift',
    '-fa', 'on',
    # MTP: uses the model's built-in multi-token prediction head, no separate drafter
    '--spec-type', 'draft-mtp',
    '--spec-draft-n-max', '3',
    '--cache-type-k', 'q4_0',
    '--cache-type-v', 'q4_0',
    '--n-gpu-layers', '999',
    # 6,13,13 而非 8,12,10：mmproj(888MiB) 最後才配置，原本的比例會讓
    # device0 (3070 8GB) 剛好塞不下 -> cudaMalloc OOM。把負載推給兩張 3060
    # 之後，200K ctx + 視覺同時成立（實測 28.3/32.7 GB）。
    '--tensor-split', '6,13,13',
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

# 視覺編碼器（ViT + projector），BF16 未量化 ~0.87GB。
# 這份模型原生就是多模態，chat template 早就有 <|vision_start|>，
# 只是先前沒掛 mmproj，所以 /props 回報 vision:false。
# 2026-08-29 補上 —— 之前好幾輪任務都敗在「看不懂自己產出的圖」。
# 找不到檔案就退回純文字，不要讓 server 開不起來。
if (Test-Path -LiteralPath $mmproj) {
    # --image-min-tokens 1024：llama.cpp 啟動時會警告 Qwen-VL 低於 1024
    # image token 在 grounding 任務（指出畫面上某物在哪）會失準。
    # 實測預設只給 868 token，模型把渲染雜點誤判成訊號跳變。
    $launchArgs += @('--mmproj', $mmproj, '--image-min-tokens', '1024')
    Write-Host "Vision: ON (mmproj BF16, image-min-tokens 1024)" -ForegroundColor Green
} else {
    Write-Host "Vision: OFF (mmproj not found: $mmproj)" -ForegroundColor Yellow
}
Start-Process -FilePath $llama -ArgumentList $launchArgs -WindowStyle Minimized

Write-Host "Waiting up to 180s for model load..." -ForegroundColor Cyan
for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 2
        if ($r.data) {
            Write-Host "[OK] Ready ($i s) - $($r.data[0].id)" -ForegroundColor Green

            # Hermes 的 context_length 必須等於「每個 slot」的額度，不是總量。
            # 不同步的話 Hermes 會用過大的值算壓縮門檻，可能高過 slot 上限
            # -> 永遠不觸發壓縮，直接撞牆讓請求失敗。
            $perSlot = [int]($Ctx / $Slots)
            # 雙引號字串裡的 \v 會被 PowerShell 當成跳脫字元(0x0B)吃掉，
            # "hermes-agent\venv\..." 會變成 "hermes-agent<VT>env\..."。
            # 改用 Join-Path + 單引號，路徑才會是字面值。
            $hermesExe = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\hermes.exe'
            if (Test-Path -LiteralPath $hermesExe) {
                & $hermesExe config set model.context_length $perSlot 2>&1 | Out-Null
                Write-Host ("[OK] Hermes context_length -> {0:N0} ({1} slot)" -f $perSlot, $Slots) -ForegroundColor Green
            }
            exit 0
        }
    } catch {}
}
Write-Host "[ERROR] Not ready in 180s" -ForegroundColor Red
exit 1
