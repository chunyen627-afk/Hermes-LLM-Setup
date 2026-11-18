# 家裡有線網路測試（排程每 30 分鐘一次，使用者 2026-10-31 要求）：同時 ping 路由器和 8.8.8.8 各 120 次（約 2 分鐘），
# 只走有線網卡（不管目前上網走哪條），結果附加到 logs\wired-check.log。只量測，不改任何網路設定。
$root = 'C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining'
$log = Join-Path $root 'logs\wired-check.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
$wired = Get-NetIPConfiguration | Where-Object { $_.InterfaceDescription -match 'Realtek' -and $_.IPv4Address } | Select-Object -First 1
if (-not $wired -or $wired.NetAdapter.Status -ne 'Up') {
    "$stamp 有線網卡沒有連線" | Add-Content $log -Encoding utf8
    exit
}
$source = $wired.IPv4Address[0].IPAddress
$gateway = if ($wired.IPv4DefaultGateway) { $wired.IPv4DefaultGateway[0].NextHop } else { '192.168.31.1' }
$jobs = foreach ($target in $gateway, '8.8.8.8') {
    Start-Job -ArgumentList $source, $target -ScriptBlock {
        param($s, $t)
        $o = ping -n 120 -w 1000 -S $s $t
        $times = @($o | Select-String '(?:time|時間)[=<](\d+)ms' | ForEach-Object { [int]$_.Matches[0].Groups[1].Value })
        [pscustomobject]@{ target = $t; ok = $times.Count; worst = ($times | Measure-Object -Maximum).Maximum }
    }
}
$r = $jobs | Wait-Job | Receive-Job
$jobs | Remove-Job
$router = $r | Where-Object target -eq $gateway
$net = $r | Where-Object target -eq '8.8.8.8'
$loss = { param($x) [math]::Round((120 - $x.ok) / 120 * 100, 1) }
$verdict = if ((& $loss $net) -lt 1 -and (& $loss $router) -lt 1) { '穩' } else { '不穩' }
'{0} {1}：路由器掉包 {2}%（最慢 {3} ms）・外網掉包 {4}%（最慢 {5} ms）' -f $stamp, $verdict, (& $loss $router), $router.worst, (& $loss $net), $net.worst |
    Add-Content $log -Encoding utf8
