<#
    Hermes 一鍵設定 —— 新機器裝完官方桌面版後跑這個。

    官方安裝程式的預設值有五個地方會讓本地 LLM 跑不起來或跑不完，
    這支腳本一次全部改對。每一項為什麼要改，見 docs/踩過的坑.md。

    用法：
        .\SETUP-HERMES.ps1                       # 只設本地模型
        .\SETUP-HERMES.ps1 -GeminiKey "AIza..."  # 連壓縮與視覺一起設

    Gemini 免費 key： https://aistudio.google.com/apikey
    （只用在壓縮摘要和看圖，主模型仍然全程走本地）
#>

param(
    [string]$Model      = 'qwen38_mtp',
    [string]$BaseUrl    = 'http://127.0.0.1:1234/v1',
    [int]   $Ctx        = 180224,
    [string]$Language   = 'zh-hant',
    [string]$GeminiKey  = '',
    [int]   $MaxTurns   = 500
)

$ErrorActionPreference = 'Continue'

$hermes = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
if (-not (Test-Path $hermes)) {
    Write-Host "[錯誤] 找不到 hermes.exe：$hermes" -ForegroundColor Red
    Write-Host "       請先安裝官方桌面版："
    Write-Host "       https://hermes-assets.nousresearch.com/Hermes-Setup.exe"
    exit 1
}

function Set-Cfg($key, $value) {
    $out = & $hermes config set $key $value 2>&1 | Out-String
    if ($out -match '✓|Set ') {
        Write-Host ("  [OK] {0,-38} {1}" -f $key, $value) -ForegroundColor Green
    } else {
        Write-Host ("  [!!] {0,-38} 設定失敗" -f $key) -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '  ==========================================' -ForegroundColor Cyan
Write-Host '    Hermes 設定（本地 LLM 長任務用）' -ForegroundColor Cyan
Write-Host '  ==========================================' -ForegroundColor Cyan
Write-Host ''

# ── 1. 模型路由 ────────────────────────────────────────────────
# 官方安裝程式出廠預設是 provider: vertex（Google 雲端），
# 不改的話會噴 HTTP 400 Malformed publisher model。
Write-Host '[1/5] 模型路由（預設是 Google Vertex，一定要改）' -ForegroundColor Yellow
Set-Cfg 'model.provider'       'lmstudio'
Set-Cfg 'model.default'        $Model
Set-Cfg 'model.base_url'       $BaseUrl
Set-Cfg 'model.context_length' $Ctx
Set-Cfg 'display.language'     $Language
Write-Host ''

# ── 2. 工具呼叫上限 ────────────────────────────────────────────
# 預設 60。實測一個五小時的韌體專案用掉 186 次，
# 撞到就被強制中斷去寫總結，表現出來像「跑一整晚做不完」。
Write-Host '[2/5] 工具呼叫上限（預設 60 太低，長任務會被反覆腰斬）' -ForegroundColor Yellow
Set-Cfg 'agent.max_turns'              $MaxTurns
Set-Cfg 'code_execution.max_tool_calls' $MaxTurns
Write-Host ''

# ── 3. 授權 ───────────────────────────────────────────────────
# 不關的話，模型跑編譯要等你按確認，60 秒逾時就 BLOCKED，
# 而且錯誤訊息明確叫它不准重試 —— 等於「不准驗證直接交件」。
Write-Host '[3/5] 自動授權（不關的話編譯驗證會被擋掉）' -ForegroundColor Yellow
Set-Cfg 'approvals.mode'                      'off'
Set-Cfg 'approvals.mcp_reload_confirm'        'false'
Set-Cfg 'approvals.destructive_slash_confirm' 'false'
Set-Cfg 'agent.subagent_auto_approve'         'true'
Write-Host '  [--] approvals.cron_mode 保持 deny（排程執行任意指令風險不同）' -ForegroundColor DarkGray
Write-Host ''

# ── 4. 壓縮 ───────────────────────────────────────────────────
# 預設 enabled: false、threshold: 0.5。
Write-Host '[4/5] 上下文壓縮（預設是關的，門檻也不是 0.8）' -ForegroundColor Yellow
Set-Cfg 'compression.enabled'   'true'
Set-Cfg 'compression.threshold' '0.8'
Write-Host ''

# ── 5. 輔助模型 ───────────────────────────────────────────────
# 壓縮和視覺預設用主模型（auto）：
#   壓縮 → 本地 27B 讀 146K 要五分鐘，120 秒沒吐 token 就被取消
#   視覺 → Qwen3.8 不支援看圖，回 500
Write-Host '[5/5] 輔助模型（壓縮與視覺）' -ForegroundColor Yellow
if ($GeminiKey) {
    Set-Cfg 'auxiliary.compression.provider' 'gemini'
    Set-Cfg 'auxiliary.compression.model'    'gemini-2.5-flash'
    Set-Cfg 'auxiliary.compression.api_key'  $GeminiKey
    Set-Cfg 'auxiliary.compression.timeout'  '300'
    Set-Cfg 'auxiliary.vision.provider'      'gemini'
    Set-Cfg 'auxiliary.vision.model'         'gemini-2.5-flash'
    Set-Cfg 'auxiliary.vision.api_key'       $GeminiKey

    $envFile = "$env:LOCALAPPDATA\hermes\.env"
    if (Test-Path $envFile) {
        if (-not (Select-String -Path $envFile -Pattern '^GEMINI_API_KEY=' -Quiet)) {
            Add-Content $envFile "`nGEMINI_API_KEY=$GeminiKey`nGOOGLE_API_KEY=$GeminiKey"
            Write-Host '  [OK] .env 已加入 GEMINI_API_KEY' -ForegroundColor Green
        }
    }
} else {
    Write-Host '  [略過] 沒給 -GeminiKey' -ForegroundColor DarkYellow
    Write-Host '         壓縮會超時取消（ctx 撞上限）、視覺會回 500。' -ForegroundColor DarkGray
    Write-Host '         免費 key: https://aistudio.google.com/apikey' -ForegroundColor DarkGray
}
Write-Host ''

# ── 驗證 ─────────────────────────────────────────────────────
Write-Host '  ==========================================' -ForegroundColor Cyan
Write-Host '    設定結果' -ForegroundColor Cyan
Write-Host '  ==========================================' -ForegroundColor Cyan
foreach ($k in @('model.provider','model.default','model.base_url',
                 'model.context_length','display.language',
                 'agent.max_turns','approvals.mode',
                 'compression.enabled','compression.threshold',
                 'auxiliary.compression.provider','auxiliary.vision.provider')) {
    $v = (& $hermes config get $k 2>&1 | Select-Object -First 1)
    Write-Host ("  {0,-38} {1}" -f $k, $v)
}
Write-Host ''

# 上游通不通
try {
    $r = Invoke-RestMethod -Uri "$BaseUrl/models" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  [OK] 上游 $BaseUrl 有回應" -ForegroundColor Green
} catch {
    Write-Host "  [!!] 連不到 $BaseUrl" -ForegroundColor Yellow
    Write-Host '       確認 llama-server 和橋接器都開著。' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  ⚠ 設定要重啟 Hermes 才生效。' -ForegroundColor Yellow
Write-Host '  ⚠ 已經開著的對話不會跟著換 —— session 會凍結建立當下的設定，' -ForegroundColor Yellow
Write-Host '     要開新對話。（見 docs/踩過的坑.md #16）' -ForegroundColor DarkGray
Write-Host ''
