# Read-only memory snapshot of the RTX 5080 PC, as JSON: commit, kernel pools, and every process's private bytes and handles.
$c = (Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit','\Memory\Pool Nonpaged Bytes','\Memory\Pool Paged Bytes').CounterSamples
$procs = Get-Process | Group-Object ProcessName | ForEach-Object {
    [pscustomobject]@{ name = $_.Name; count = $_.Count
        privateMB = [math]::Round(($_.Group | Measure-Object PrivateMemorySize64 -Sum).Sum / 1MB)
        handles = ($_.Group | Measure-Object HandleCount -Sum).Sum } }
[pscustomobject]@{
    time = (Get-Date).ToString('s')
    boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s')
    commitGB = [math]::Round($c[0].CookedValue / 1GB, 2); limitGB = [math]::Round($c[1].CookedValue / 1GB, 2)
    nonpagedGB = [math]::Round($c[2].CookedValue / 1GB, 2); pagedGB = [math]::Round($c[3].CookedValue / 1GB, 2)
    processes = $procs | Sort-Object privateMB -Descending
} | ConvertTo-Json -Depth 3 -Compress
