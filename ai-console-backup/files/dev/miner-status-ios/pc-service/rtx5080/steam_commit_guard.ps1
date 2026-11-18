# Steam commit guard (home PC / RTX 5080).
#
# Steam leaks handles (2026-11-01: 2.47 million handles, system commit 175.8 of 177.9 GB, dwm.exe
# crashed with c00001ad and the screen flashed). When the system commit charge reaches the limit
# below and Steam is running, ask Steam to shut down normally; if it is still there after a
# minute, end it. Nothing else is touched, and Steam is not started again.
#
# Started at logon by the scheduled task "SteamCommitGuard" (this user, not elevated) as one
# hidden loop, so no console window flashes every few minutes.
#   -Once      check one time and exit
#   -DryRun    log what would happen, close nothing
# ASCII only (PowerShell 5.1 reads BOM-less scripts in the ANSI code page).
param(
  [double]$LimitGB = 100,
  [int]$EverySeconds = 60,
  [switch]$Once,
  [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$logFile = Join-Path $PSScriptRoot 'steam_commit_guard.log'

function Write-Log([string]$text) {
  $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $text
  Add-Content -Path $logFile -Value $line -Encoding ASCII
  if ((Get-Item $logFile).Length -gt 1MB) {
    $keep = Get-Content $logFile -Tail 2000
    Set-Content -Path $logFile -Value $keep -Encoding ASCII
  }
}

function Get-CommitGB {
  # Win32_OperatingSystem: virtual = commit limit, free virtual = limit - committed (KB).
  $os = Get-CimInstance Win32_OperatingSystem
  return @{ used = ($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB; limit = $os.TotalVirtualMemorySize / 1MB }
}

function Close-Steam {
  $steam = Get-Process -Name steam -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $steam) { return }
  $exe = $steam.Path
  if ($DryRun) { Write-Log "dry run: would ask $exe to shut down"; return }
  Start-Process -FilePath $exe -ArgumentList '-shutdown'
  for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    if (-not (Get-Process -Name steam -ErrorAction SilentlyContinue)) { break }
  }
  if (Get-Process -Name steam -ErrorAction SilentlyContinue) {
    Get-Process -Name steam, steamwebhelper, steamservice -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Log 'Steam did not exit within 60 s; ended it'
  } else {
    Write-Log "Steam shut down normally after $(($i + 1) * 5) s"
  }
  Start-Sleep -Seconds 10
  $after = Get-CommitGB
  Write-Log ('commit after: {0:N1} of {1:N1} GB' -f $after.used, $after.limit)
}

Write-Log ("started: limit {0} GB, every {1} s{2}" -f $LimitGB, $EverySeconds, $(if ($DryRun) { ', dry run' } else { '' }))
while ($true) {
  try {
    $c = Get-CommitGB
    $steam = Get-Process -Name steam -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Once) {
      $handles = if ($steam) { $steam.HandleCount } else { 'not running' }
      Write-Log ('check: commit {0:N1} of {1:N1} GB, steam handles {2}' -f $c.used, $c.limit, $handles)
    }
    if ($steam -and $c.used -ge $LimitGB) {
      Write-Log ('commit {0:N1} GB >= {1} GB with Steam running ({2} handles): closing Steam' -f $c.used, $LimitGB, $steam.HandleCount)
      Close-Steam
    }
  } catch {
    Write-Log "error: $($_.Exception.Message)"
  }
  if ($Once) { break }
  Start-Sleep -Seconds $EverySeconds
}
