# 珍珠挖礦資訊（前景視窗）：每 5 秒顯示算力、份額、溫度、功率、時脈、節點與最近紀錄。
# 只讀不控制：關掉這個視窗不會停挖；要停請按桌面「停止珍珠挖礦」。
$root = 'C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining'
$nodes = @{ 'prl-hk.kryptex.network' = '香港'; 'prl-sg.kryptex.network' = '新加坡'; 'prl.kryptex.network' = '德國' }
$host.UI.RawUI.WindowTitle = '珍珠挖礦資訊（關掉這個視窗不會停挖）'
try { $host.UI.RawUI.WindowSize = New-Object Management.Automation.Host.Size(96, 30) } catch { }
$best = 0.0
while ($true) {
    $lines = @()
    # Process names only: run without administrator rights, this window cannot read the command line
    # or start time of the mining session (started elevated), so those showed it as not mining.
    $miner = Get-Process SRBMiner-MULTI, krig-miner -ErrorAction SilentlyContinue | Select-Object -First 1
    $gpu = (& nvidia-smi --query-gpu=temperature.gpu,power.draw,power.limit,clocks.gr,utilization.gpu,fan.speed --format=csv,noheader,nounits) -split ',\s*'
    $log = Get-ChildItem (Join-Path $root 'logs') -Filter 'mining-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    Clear-Host
    Write-Host ('珍珠挖礦資訊　' + (Get-Date -Format 'MM/dd HH:mm:ss')) -ForegroundColor Cyan
    Write-Host '關掉這個視窗不會停挖；要停挖請按桌面「停止珍珠挖礦」。' -ForegroundColor DarkGray
    Write-Host ''
    if (-not $miner) {
        Write-Host '● 目前沒有在挖礦（按桌面「珍珠挖礦」開始）' -ForegroundColor Yellow
        # The supervisor (PearlAutoStart) says whether it is cooling down to start again, or was stopped.
        $guard = Get-Content (Join-Path $root 'logs\boot-start.log') -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($guard) { Write-Host ('　看守　　' + $guard) -ForegroundColor Gray }
        $best = 0.0
    } else {
        $rate = $null; $accepted = $null; $rejected = $null
        try {
            $api = Invoke-RestMethod http://127.0.0.1:21550 -TimeoutSec 2
            $rate = $api.algorithms[0].hashrate.gpu.total / 1e12
            $accepted = $api.algorithms[0].shares.accepted; $rejected = $api.algorithms[0].shares.rejected
        } catch { }
        $stamp = if ($log -and $log.Name -match 'mining-(\d{8}-\d{6})') { [datetime]::ParseExact($Matches[1], 'yyyyMMdd-HHmmss', $null) } else { $null }
        if ($rate -ne $null) { $best = [math]::Max($best, $rate) }
        $state = '挖礦中'; $color = 'Green'
        if ($rate -eq $null) { $state = '礦工剛啟動或沒有回應'; $color = 'Yellow' }
        elseif ($rate -eq 0) { $state = '算力 0（剛啟動、或正在重新連礦池）'; $color = 'Yellow' }
        elseif ($best -gt 0 -and $rate -lt $best / 2 -and [int]$gpu[4] -ge 50) { $state = '讓路中（有其他程式在用顯卡，挖礦自動降速）'; $color = 'DarkYellow' }
        Write-Host "● $state" -ForegroundColor $color
        Write-Host ('　算力　　{0}' -f $(if ($rate -ne $null) { '{0:N1} TH/s' -f $rate } else { '—' }))
        Write-Host ('　份額　　接受 {0}・拒絕 {1}' -f $accepted, $rejected)
        if ($stamp) { Write-Host ('　已挖　　{0:%h} 小時 {0:%m} 分（{1:HH:mm} 起）' -f ((Get-Date) - $stamp), $stamp) }
        if ($log) {
            $connected = Select-String -Path $log.FullName -Pattern 'connected: stratum\+ssl://([^:]+)' | Select-Object -Last 1
            if ($connected) { $node = $connected.Matches[0].Groups[1].Value; Write-Host ('　礦池　　{0}' -f $(if ($nodes[$node]) { $nodes[$node] } else { $node })) }
        }
    }
    if ($gpu.Count -ge 6) {
        Write-Host ('　顯卡　　{0}°C・{1:N0} W（上限 {2:N0} W）・{3} MHz・使用率 {4}%・風扇 {5}%' -f $gpu[0], [double]$gpu[1], [double]$gpu[2], $gpu[3], $gpu[4], $gpu[5])
    }
    # Home wired line, tested every 30 minutes by the PearlWiredCheck task (wired_check.ps1).
    $wired = @(Get-Content (Join-Path $root 'logs\wired-check.log') -Tail 3 -Encoding UTF8 -ErrorAction SilentlyContinue)
    if ($wired.Count) {
        Write-Host ''
        Write-Host '家裡有線網路（每 30 分鐘測一次）' -ForegroundColor Cyan
        $wired | ForEach-Object { Write-Host ('　' + $_) -ForegroundColor $(if ($_ -match ' 穩：') { 'Green' } else { 'Yellow' }) }
        if ($wired.Count -eq 3 -and @($wired | Where-Object { $_ -match ' 穩：' }).Count -eq 3) {
            Write-Host '　有線已連續穩定 1.5 小時：可以拔掉 iPhone 改回有線。' -ForegroundColor Green
        }
    }
    Write-Host ''
    if ($log) {
        Write-Host '最近紀錄' -ForegroundColor Cyan
        Get-Content $log.FullName -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('　' + $_) -ForegroundColor Gray }
    }
    Start-Sleep 5
}
