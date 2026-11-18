# 珍珠挖礦（桌面捷徑）：在背景開始受保護的挖礦，再打開「珍珠挖礦資訊」視窗看即時狀態；已經在挖就只開資訊視窗。
# 挖礦本身不綁任何視窗：關掉資訊視窗不會停挖。
# 跟手機 App、AI 控制台一樣交給看守排程 PearlAutoStart（沒有那個排程時才直接跑 session.py）。
$root = 'C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining'
$shell = New-Object -ComObject WScript.Shell
function Say($text, $seconds = 8) { if ($env:PEARL_QUIET) { Write-Host $text; return }; [void]$shell.Popup($text, $seconds, '珍珠挖礦', 64) }  # PEARL_QUIET: tests over SSH, no desktop
function Watch {  # the read-only live view, in a normal window
    if ($env:PEARL_QUIET) { Write-Host 'watch window'; return }
    Start-Process powershell.exe -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $root 'watch_pearl.ps1') + '"')
}
# A running miner counts too: without administrator rights the session's command line is not readable.
function Mining { [bool](Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue) -or [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'session\.py' -and $_.CommandLine -notmatch '--restore' }) }

if (Mining) { Watch; exit }
$identity = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$admin = $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# 2026-11-01: mining runs under the PearlAutoStart supervisor, which restarts it whatever stops it until a
# stop button leaves HOLD. Starting clears HOLD and runs that task; it cools the GPU first if needed.
if (Get-ScheduledTask -TaskName 'PearlAutoStart' -ErrorAction SilentlyContinue) {
    Remove-Item (Join-Path $root 'HOLD'), (Join-Path $root 'STOP_REQUEST') -Force -ErrorAction SilentlyContinue
    try { Start-ScheduledTask -TaskName 'PearlAutoStart' -ErrorAction Stop }
    catch {
        if ($admin) { Say "沒有開始挖礦：$($_.Exception.Message)" 20; exit }
        Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"')
        exit
    }
    for ($i = 0; $i -lt 24; $i++) { Start-Sleep 5; if (Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue) { Watch; exit } }
    Say '已交給看守開始：顯卡降到 58°C 以下就會開挖，不用再按。' 8
    Watch
    exit
}
if (-not $admin) {
    # session.py sets the power limit and clocks, which needs administrator rights.
    Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"')
    exit
}
$host.UI.RawUI.WindowTitle = '珍珠挖礦：準備開始'
for ($i = 0; $i -lt 60; $i++) {  # session.py starts only below 60 C
    $temp = [int](& nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits).Trim()
    if ($temp -lt 58) { break }
    if ($i -eq 0) { Say "顯卡 $temp°C，降到 58°C 以下就會自動開始（通常 1～2 分鐘），不用再按。" 6 }
    Start-Sleep 5
}
$log = Join-Path $root 'logs\desktop-start.log'
Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList ('"' + (Join-Path $root 'session.py') + '"') `
    -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 3
    if (Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue) { Watch; exit }
    if (-not (Mining)) { break }
}
$why = (Get-Content $log, "$log.err" -ErrorAction SilentlyContinue | Where-Object { $_ -match 'NOT STARTED|STOPPED|Error' } | Select-Object -Last 1)
Say ('沒有開始挖礦。' + $(if ($why) { "`n$why" } else { '' })) 20
