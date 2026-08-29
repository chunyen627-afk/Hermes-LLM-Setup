<#
    切換獨佔 / 共享模式。

    獨佔：1 slot，ctx 全給你一個人。跑大專案用。
    共享：2 slot 各 120K，家人可以同時用 /chat。

    --parallel 是啟動參數，所以切換 = 重啟 llama-server。
    正在跑的對話會中斷（KV cache 沒了），下次送訊息要重算。

    用法：
        .\_switch_mode.ps1          # 顯示選單
        .\_switch_mode.ps1 -Mode solo
        .\_switch_mode.ps1 -Mode share
        .\_switch_mode.ps1 -Mode solo -Force   # 不管在不在跑，直接切
#>

param(
    [ValidateSet('solo', 'share', '')]
    [string]$Mode = '',
    [switch]$Force
)

$ErrorActionPreference = 'Continue'
$Port = 8001
$Ensure = Join-Path $PSScriptRoot '_ensure_38.ps1'

# 獨佔 204800：單 slot 時 KV cache 不用切兩份，可以塞更大。
# 2026-08-29 起有掛 mmproj（模型自己看圖，不再走雲端 Gemini）。
# 視覺不用犧牲 ctx —— 關鍵是 --tensor-split 6,13,13 讓 3070 少扛，
# 否則 888MiB 的 mmproj 會在 device0 OOM。實測 200K + 視覺 = 28.3/32.7 GB。
# 共享的 245760 會被 --parallel 2 平分成每 slot 122,880。
$MODES = @{
    solo  = @{ Slots = 1; Ctx = 204800; Desc = '獨佔 — 1 slot，ctx 200K 全給你' }
    share = @{ Slots = 2; Ctx = 245760; Desc = '共享 — 2 slot 各 120K，家人也能用' }
}


function Get-CurrentMode {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/slots" -TimeoutSec 8 -ErrorAction Stop
        return @{ Slots = $r.Count; Ctx = $r[0].n_ctx }
    } catch {
        return $null
    }
}

$cur = Get-CurrentMode

if (-not $Mode) {
    Clear-Host
    Write-Host ''
    Write-Host '  ==========================================' -ForegroundColor Cyan
    Write-Host '    切換 slot 模式' -ForegroundColor Cyan
    Write-Host '  ==========================================' -ForegroundColor Cyan
    Write-Host ''
    if ($cur) {
        Write-Host ("   目前: {0} slot，每個 {1:N0} ctx" -f $cur.Slots, $cur.Ctx) -ForegroundColor Green
    } else {
        Write-Host '   目前: server 沒在跑' -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host '   [1] 獨佔 — 1 slot，ctx 200K 全給你' -ForegroundColor Yellow
    Write-Host '       跑大專案用。家人的 /chat 會排隊等你。' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '   [2] 共享 — 2 slot 各 120K' -ForegroundColor Yellow
    Write-Host '       你和家人同時用不互搶。' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '   [0] 取消' -ForegroundColor DarkYellow
    Write-Host ''
    $pick = Read-Host '   選擇'
    switch ($pick) {
        '1' { $Mode = 'solo' }
        '2' { $Mode = 'share' }
        default { Write-Host '   已取消'; exit 0 }
    }
}

$sel = $MODES[$Mode]

# 已經是這個模式就不用動
if ($cur -and $cur.Slots -eq $sel.Slots) {
    Write-Host ''
    Write-Host ("   [OK] 已經是{0}模式了（{1} slot），不用切換" -f `
        $(if ($Mode -eq 'solo') { '獨佔' } else { '共享' }), $cur.Slots) -ForegroundColor Green
    Start-Sleep -Seconds 2
    exit 0
}

# 正在推理中就先警告 —— 切換會讓那次請求丟失
if (-not $Force -and $cur) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/slots" -TimeoutSec 8 -ErrorAction Stop
        if ($r | Where-Object { $_.is_processing }) {
            Write-Host ''
            Write-Host '   ⚠ 模型正在推理中，現在切換會中斷那次請求。' -ForegroundColor Yellow
            Write-Host '     而且對話的 KV cache 會清空，下次送訊息要重算一次。' -ForegroundColor DarkGray
            Write-Host ''
            $go = Read-Host '   還是要切換嗎？(y/N)'
            if ($go -ne 'y' -and $go -ne 'Y') { Write-Host '   已取消'; exit 0 }
        }
    } catch {}
}

Write-Host ''
Write-Host ("   >> 切換到{0}" -f $sel.Desc) -ForegroundColor Cyan
Write-Host ''

Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {}
}
Start-Sleep -Seconds 6

& $Ensure -Port $Port -Think low -Mode fw -Ctx $sel.Ctx -Slots $sel.Slots
$rc = $LASTEXITCODE

if ($rc -eq 0) {
    Start-Sleep -Seconds 2
    $new = Get-CurrentMode
    if ($new) {
        Write-Host ''
        Write-Host ("   [OK] 現在是 {0} slot，每個 {1:N0} ctx" -f $new.Slots, $new.Ctx) -ForegroundColor Green

        # Hermes 的 context_length 要跟著改，否則它會以為還有舊的額度
        $hermes = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
        if (Test-Path $hermes) {
            $per = [int]($sel.Ctx / $sel.Slots)
            & $hermes config set model.context_length $per 2>&1 | Out-Null
            Write-Host ("   [OK] Hermes context_length 已同步為 {0:N0}" -f $per) -ForegroundColor Green
        }

        Write-Host ''
        if ($Mode -eq 'solo') {
            Write-Host '   家人的 /chat 還是能用，只是會跟你排隊。' -ForegroundColor Gray
        }
        Write-Host '   ⚠ 已經開著的對話要「開新對話」才會用到新的 ctx 上限。' -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 5
} else {
    Write-Host ''
    Write-Host ("   [ERROR] 啟動失敗（代碼 {0}）" -f $rc) -ForegroundColor Red
    Write-Host '   VRAM 可能不夠，試試把 Ctx 調小一點。' -ForegroundColor DarkGray
    Read-Host '   按 Enter 關閉'
}
