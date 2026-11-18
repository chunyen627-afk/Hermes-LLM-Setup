# 停止珍珠挖礦（桌面捷徑）：請保護程式停下礦工並還原顯卡設定（跟手機 App 的停止一樣），不用管理員權限。
$root = 'C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining'
$shell = New-Object -ComObject WScript.Shell
function Say($text, $seconds = 8) { if ($env:PEARL_QUIET) { Write-Host $text; return }; [void]$shell.Popup($text, $seconds, '珍珠挖礦', 64) }  # PEARL_QUIET: tests over SSH, no desktop
# HOLD first and always: the supervisor may be cooling down between sessions with no miner running.
New-Item -ItemType File -Force (Join-Path $root 'HOLD') | Out-Null
if (-not (Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue)) { Say '目前沒有在挖礦；看守也不會再自動開。'; exit }
New-Item -ItemType File -Force (Join-Path $root 'STOP_REQUEST') | Out-Null
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep 2
    if (-not (Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue)) { Say '已停止挖礦，顯卡設定已還原。'; exit }
}
Say '已送出停止，礦工還沒結束；再等一下或稍後再按一次。' 15
