# 開機選單：10 秒倒數，沒選就用預設（韌體模式）
# 由排程工作 Qwen38-GPU-Server 呼叫

$ErrorActionPreference = 'Continue'
$timeout = 10

$menu = @(
    @{ Key='1'; Name='韌體設計 (預設)'; Think='high'; Mode='fw';   Ctx=180224; Desc='STM32/RTOS。實測 3098 項測試 0 失敗，慢但品質最好' }
    @{ Key='2'; Name='一般寫 code';     Think='off';  Mode='chat'; Ctx=180224; Desc='寫程式/網頁/遊戲，最順的組合' }
    @{ Key='3'; Name='快速模式';         Think='off';  Mode='fw';   Ctx=180224; Desc='趕時間或簡單任務。品質較差（55 項測試錯 1）' }
    @{ Key='4'; Name='長 context 模式';  Think='off';  Mode='chat'; Ctx=212992; Desc='208K ctx，讀大量檔案用（VRAM 很緊，可能失敗）' }
    @{ Key='5'; Name='不啟動';           Think=$null }
)

Clear-Host
Write-Host ''
Write-Host '  ==========================================' -ForegroundColor Cyan
Write-Host '    Qwen3.8-27B GPU Server' -ForegroundColor Cyan
Write-Host '  ==========================================' -ForegroundColor Cyan
Write-Host ''
foreach ($m in $menu) {
    if ($m.Think) {
        Write-Host ("   [{0}] {1}" -f $m.Key, $m.Name) -ForegroundColor Yellow
        Write-Host ("       {0}" -f $m.Desc) -ForegroundColor DarkGray
    } else {
        Write-Host ("   [{0}] {1}" -f $m.Key, $m.Name) -ForegroundColor DarkYellow
    }
}
Write-Host ''

$choice = $null
for ($i = $timeout; $i -gt 0; $i--) {
    Write-Host ("`r   {0} 秒後自動選擇 [1] 韌體設計...  按數字鍵選擇 " -f $i) -NoNewline -ForegroundColor Green
    $waited = 0
    while ($waited -lt 1000) {
        if ([Console]::KeyAvailable) {
            $k = [Console]::ReadKey($true)
            if ($k.KeyChar -match '[1-5]') { $choice = $k.KeyChar.ToString(); break }
        }
        Start-Sleep -Milliseconds 50
        $waited += 50
    }
    if ($choice) { break }
}
Write-Host ''

if (-not $choice) { $choice = '1' }
$sel = $menu | Where-Object { $_.Key -eq $choice } | Select-Object -First 1

if (-not $sel.Think) {
    Write-Host ''
    Write-Host '   已取消啟動。要手動開請雙擊 1-START-GPU-Server.bat' -ForegroundColor DarkGray
    Start-Sleep -Seconds 3
    exit 0
}

Write-Host ''
Write-Host ("   >> {0}  (think={1} mode={2} ctx={3})" -f $sel.Name, $sel.Think, $sel.Mode, $sel.Ctx) -ForegroundColor Cyan
Write-Host ''

& (Join-Path $PSScriptRoot '_ensure_38.ps1') -Think $sel.Think -Mode $sel.Mode -Ctx $sel.Ctx
$rc = $LASTEXITCODE

if ($rc -eq 0) {
    # 啟動 Hermes 橋接器（1234 -> 8001，並補 tools capability）
    $bridge = Join-Path $PSScriptRoot 'hermes_bridge.py'
    $busy = Get-NetTCPConnection -LocalPort 1234 -State Listen -ErrorAction SilentlyContinue
    if (-not $busy) {
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','start','/min','"Hermes Bridge"',(Join-Path $PSScriptRoot '_bridge.bat') -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Write-Host '   [OK] Hermes 橋接器已啟動 (:1234)' -ForegroundColor Green
    } else {
        Write-Host '   [--] :1234 已有服務，略過橋接器' -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host '   [OK] 伺服器已就緒' -ForegroundColor Green
    Write-Host '   別台電腦連: http://10.35.219.64:8001' -ForegroundColor Gray
    Write-Host '   這個視窗會在 8 秒後自動關閉（伺服器繼續在背景跑）' -ForegroundColor DarkGray
    Start-Sleep -Seconds 8
} else {
    Write-Host ''
    Write-Host '   [ERROR] 啟動失敗' -ForegroundColor Red
    Write-Host '   按任意鍵關閉...' -ForegroundColor DarkGray
    [Console]::ReadKey($true) | Out-Null
}
exit $rc
