param([string]$Pick = '')

# 切換 :8001 上載入的模型。預設 Qwen3.8-27B。
# llama-server 一次只能載一個模型，所以切換 = 重啟 server。

$ErrorActionPreference = 'Continue'
$Port = 8001
$llama = 'C:\Users\pjunm\.unsloth\llama.cpp-b10435\llama-server.exe'
$hub = 'C:\Users\pjunm\.cache\huggingface\hub'
$lms = 'C:\Users\pjunm\.lmstudio\models'

# ctx 依 VRAM 32GB 估算，模型越大 ctx 越保守
$models = @(
    @{ Key='1'; Name='Qwen3.8-27B 無審查  (預設，主力)'
       Path="$hub\Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf"
       Alias='qwen38_mtp'
       Ctx=180224; Split='6,12,13'; Mtp=$true
       Tmpl="$PSScriptRoot\qwen38_chat_template.jinja"
       Desc='HauhauCS abliterated，MTP 與視覺塔都保留，比原版小 0.4GB' }

    @{ Key='2'; Name='Qwen3-Coder-30B-A3B  (寫程式)'
       Path="$lms\unsloth\Qwen3-Coder-30B-A3B-Instruct-GGUF\Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf"
       Alias='qwen3coder30'; Ctx=131072; Split='6,12,13'; Mtp=$false
       Desc='MoE 架構，寫 code 反應快' }

    @{ Key='3'; Name='Qwen3.6-35B-A3B Uncensored  (無限制)'
       Path="$lms\HauhauCS\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
       Alias='qwen36_35b_unc'; Ctx=131072; Split='6,12,13'; Mtp=$false
       Desc='沒有內容限制，ctx 128K' }

    @{ Key='4'; Name='DeepSeek-V4-Lite  (輕量快速)'
       Path="$lms\unsloth\DeepSeek-V4-Lite-Instruct-GGUF\DeepSeek-V4-Lite-Instruct-Q4_K_M.gguf"
       Alias='dsv4lite'; Ctx=131072; Split='6,12,13'; Mtp=$false
       Desc='體積小、載入快，簡單任務用' }
)

if (-not $Pick) {
    Clear-Host
    Write-Host ''
    Write-Host '  ==========================================' -ForegroundColor Cyan
    Write-Host '    切換本地模型  (:8001)' -ForegroundColor Cyan
    Write-Host '  ==========================================' -ForegroundColor Cyan
    Write-Host ''

    # 顯示目前跑的是哪個
    try {
        $cur = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 3 -ErrorAction Stop
        $curId = $cur.models[0].name
        Write-Host ("   目前: {0}" -f $curId) -ForegroundColor Green
    } catch {
        Write-Host '   目前: (沒有模型在跑)' -ForegroundColor DarkGray
    }
    Write-Host ''

    foreach ($m in $models) {
        $exists = Test-Path $m.Path
        $color = if ($exists) { 'Yellow' } else { 'DarkRed' }
        $mark = if ($exists) { '' } else { '  [檔案不存在]' }
        Write-Host ("   [{0}] {1}{2}" -f $m.Key, $m.Name, $mark) -ForegroundColor $color
        Write-Host ("       {0}" -f $m.Desc) -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host '   [0] 取消' -ForegroundColor DarkYellow
    Write-Host ''
    $Pick = Read-Host '   選擇 (直接按 Enter = 1 Qwen3.8)'
    if (-not $Pick) { $Pick = '1' }
}

if ($Pick -eq '0') { Write-Host '   已取消'; exit 0 }

$sel = $models | Where-Object { $_.Key -eq $Pick } | Select-Object -First 1
if (-not $sel) { Write-Host "   沒有這個選項: $Pick" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $sel.Path)) {
    Write-Host ("   [ERROR] 找不到模型檔: {0}" -f $sel.Path) -ForegroundColor Red
    exit 1
}

# 已經是這個模型就不用重載
try {
    $cur = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 3 -ErrorAction Stop
    if ($cur.models[0].name -eq $sel.Alias) {
        Write-Host ("   [OK] {0} 已經在跑了，不用切換" -f $sel.Alias) -ForegroundColor Green
        exit 0
    }
} catch {}

Write-Host ''
Write-Host ("   >> 切換到 {0}" -f $sel.Name) -ForegroundColor Cyan
Write-Host '   停掉現有 server...' -ForegroundColor Yellow
Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {}
}
Start-Sleep -Seconds 5

$launchArgs = @(
    '--model', $sel.Path,
    '--alias', $sel.Alias,
    '--host', '0.0.0.0',
    '--port', "$Port",
    '--jinja',
    '--parallel', '1',
    '--ctx-size', "$($sel.Ctx)",
    '-fa', 'on',
    '--cache-type-k', 'q4_0',
    '--cache-type-v', 'q4_0',
    '--n-gpu-layers', '999',
    '--tensor-split', $sel.Split,
    '-sm', 'layer',
    '--batch-size', '2048',
    '--ubatch-size', '512',
    '--swa-full',
    '--checkpoint-min-step', '0',
    '--ctx-checkpoints', '32',
    '--cache-ram', '-1',
    '--no-warmup',
    '--temp', '0.3',
    '--top-p', '0.70',
    '--top-k', '20',
    '--min-p', '0.05',
    '--presence-penalty', '0.0',
    '--repeat-penalty', '1.05'
)

# Qwen3.8 專屬：MTP 推測解碼 + 自訂 chat template + 思考預算
if ($sel.Mtp) {
    $launchArgs += @('--spec-type','draft-mtp','--spec-draft-n-max','3')
    $launchArgs += @('--reasoning','on','--reasoning-effort','low','--reasoning-budget','2048')
}
if ($sel.Tmpl -and (Test-Path $sel.Tmpl)) {
    $launchArgs += @('--chat-template-file', $sel.Tmpl)
}

Write-Host ("   載入中 (ctx {0})..." -f $sel.Ctx) -ForegroundColor Cyan
Start-Process -FilePath $llama -ArgumentList $launchArgs -WindowStyle Minimized

for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 2 -ErrorAction Stop
        if ($r.models) {
            Write-Host ("   [OK] 就緒 ({0}s) - {1}" -f $i, $r.models[0].name) -ForegroundColor Green

            # 橋接器沒跑就順手開起來（Hermes 需要它補 tools capability）
            $busy = Get-NetTCPConnection -LocalPort 1234 -State Listen -ErrorAction SilentlyContinue
            if (-not $busy) {
                Start-Process -FilePath 'cmd.exe' `
                    -ArgumentList '/c','start','/min','"Hermes Bridge"',(Join-Path $PSScriptRoot '_bridge.bat') `
                    -WindowStyle Hidden -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Write-Host '   [OK] 橋接器已啟動 (:1234)' -ForegroundColor Green
            }
            # === 同步 Hermes 設定（不然 Hermes 還是送舊的模型名稱）===
            $hermes = 'C:\Users\pjunm\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe'
            if (Test-Path $hermes) {
                & $hermes config set model.default $sel.Alias 2>&1 | Out-Null
                & $hermes config set model.provider lmstudio 2>&1 | Out-Null
                & $hermes config set model.base_url 'http://127.0.0.1:1234/v1' 2>&1 | Out-Null
                & $hermes config set model.context_length $sel.Ctx 2>&1 | Out-Null
                Write-Host ("   [OK] Hermes 設定已同步 ({0}, ctx {1})" -f $sel.Alias, $sel.Ctx) -ForegroundColor Green
            } else {
                Write-Host '   [!] 找不到 hermes.exe，請自己在 Hermes 裡改模型' -ForegroundColor Yellow
            }

            Write-Host ''
            Write-Host '   桌面版 Hermes 要「開新對話」才會用新模型。' -ForegroundColor Gray
            Write-Host '   （舊對話會記住建立當下的模型設定）' -ForegroundColor DarkGray
            Start-Sleep -Seconds 5
            exit 0
        }
    } catch {}
}
Write-Host '   [ERROR] 180 秒內沒載好' -ForegroundColor Red
Read-Host '   按 Enter 關閉'
exit 1
