# 珍珠挖礦看守（排程 PearlAutoStart：開機 2 分鐘後、以及手機 App／控制台／桌面捷徑按「開始」時都走這裡）。
# 5080 是主力機，使用者 2026-11-01：無論如何都不能停。session.py 不管為什麼結束（過熱停、設定被改、礦工一直當掉…），
# 這裡都等它降溫後重新開始；只有使用者按停止（留下 HOLD 檔，或 guard 收到 STOP_REQUEST）才真的停。
# 用使用者帳號（S4U，不用登入）跑，狀態監控才看得到礦工。
$root = 'C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining'
$hold = Join-Path $root 'HOLD'
$log = Join-Path $root 'logs\boot-start.log'
function Log($text) { ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text) | Add-Content $log -Encoding utf8 }
function Mining { [bool](Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue) -or [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'session\.py' -and $_.CommandLine -notmatch '--restore' }) }
function Temp { try { $t = [int](& nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits).Trim(); if ($LASTEXITCODE -eq 0) { $t } else { $null } } catch { $null } }

if (Test-Path $hold) { Log '使用者之前按了停止（HOLD），不自動開挖'; exit 0 }
$delays = @(30, 120, 300, 600)  # after quick failures in a row; a session that ran 30 minutes resets it
$failures = 0
while ($true) {
    $temp = Temp
    for ($i = 0; $i -lt 120 -and $temp -eq $null; $i++) { Start-Sleep 5; $temp = Temp }  # display driver after boot
    if ($temp -eq $null) { Log '顯卡驅動 10 分鐘沒有回應，1 分鐘後再試'; Start-Sleep 60; continue }
    if (Mining) { Log '已經在挖礦，看守不重複開'; exit 0 }
    while ($temp -ge 58) {  # session.py starts only below 60 C
        if (Test-Path $hold) { Log '使用者按了停止，看守結束'; exit 0 }
        Start-Sleep 10; $temp = Temp; if ($temp -eq $null) { $temp = 99 }
    }
    if (Test-Path $hold) { Log '使用者按了停止，看守結束'; exit 0 }
    Log "開始挖礦（顯卡 $temp°C）"
    $began = Get-Date
    Set-Location $root
    & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'session.py') *>> (Join-Path $root 'logs\boot-session.log')
    $ran = (Get-Date) - $began
    $last = Get-ChildItem (Join-Path $root 'logs') -Filter 'result-*.json' | Sort-Object LastWriteTime | Select-Object -Last 1
    $reason = if ($last -and $last.LastWriteTime -ge $began) { (Get-Content $last.FullName -Raw | ConvertFrom-Json).reason } else { '沒有結束紀錄' }
    if ($reason -eq 'Stopped from the phone') { New-Item -ItemType File -Force $hold | Out-Null }  # every stop button goes through STOP_REQUEST
    if (Test-Path $hold) { Log "使用者按了停止（$reason），看守結束"; exit 0 }
    if ($ran.TotalMinutes -ge 30) { $failures = 0 }
    $wait = $delays[[math]::Min($failures, $delays.Count - 1)]
    $failures++
    Log ("挖礦結束（{0}，挖了 {1:N0} 分鐘），{2} 秒後降溫完重新開始" -f $reason, $ran.TotalMinutes, $wait)
    for ($i = 0; $i -lt $wait; $i += 5) { if (Test-Path $hold) { Log '使用者按了停止，看守結束'; exit 0 }; Start-Sleep 5 }
}
