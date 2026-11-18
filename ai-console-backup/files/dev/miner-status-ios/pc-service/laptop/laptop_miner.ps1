# Guarded Pearl miner for the RTX 3070 laptop (Windows PowerShell 5.1, nothing to install).
#
# - Clocks: memory locked at 6001 MHz; core ceiling starts at 800 MHz (range 600-900).
# - Thermal: from 74 C lower the ceiling by 50 MHz every 15 s; 80 C stops mining. 3 minutes at or
#   below 70 C raises it by 50 MHz. The ceiling is re-applied whenever the measured clock exceeds it.
# - Also stops on: GPU telemetry failing 3 times in a row, or a STOP file in this folder.
# - A miner that exits, or whose log (SRBMiner: API) stops advancing for 3 minutes, is started again
#   with freshly ranked nodes; more than 5 such restarts within an hour stops mining. (User,
#   2026-11-01: every machine should get back to mining on its own.)
# - 45 minutes without an accepted share restarts the miner once; still none 45 minutes after that
#   restart stops mining. (The earlier 30-minute stop tripped on 2026-10-26 on an ordinary gap:
#   with few shares a 30-minute gap happens 2-3 times a day.)
# - A share drought while no pool node takes a connection is this PC's network being down, not the
#   miner: it waits (rechecking every minute) instead of restarting or stopping, and the miner gets
#   a fresh 45 minutes once a node answers again. Each machine mines on its own line, so an outage
#   at the office does not stop the laptop, and an outage here does not end in a stop either.
# - Pool: Kryptex Hong Kong / Singapore / global nodes, ordered by measured connect time and passed
#   to krig as failover pools; a share-drought restart re-measures and moves off the node in use.
# - The office node watcher can ask for other nodes by writing SWITCH_NODES; only the miner restarts.
# - Console QuickEdit is turned off: a click in a visible window froze the guard (Write-Host
#   blocks in selection mode) until the office guardian killed the unguarded miner.
# - Unplugging (running on battery) stops mining at once, to spare the battery.
# - Emergency shutdown: 88 C at any time, or still 85 C a minute after a temperature stop.
# - The miner runs inside a Job Object, so it dies with this script instead of running unguarded.
# - CPU maximum state on AC is held at 80 % while mining (shared heat pipes), restored on exit.
# - Writes laptop-status.json (same shape as the RTX monitor's /status) for the office aggregator,
#   which reads it over SSH. No port is opened here.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Config = Get-Content (Join-Path $Root 'laptop-config.json') -Raw | ConvertFrom-Json
# The same script guards other single-GPU PCs ("office 2", a desktop RTX 3070): their config may
# override the tuning below; anything left out keeps the laptop's values.
function Setting($name, $default) { if ($null -ne $Config.$name) { $Config.$name } else { $default } }
$StatusFile = Join-Path $Root (Setting 'status_file' 'laptop-status.json')
$StopFile = Join-Path $Root 'STOP'
# Written by the office node watcher: preferred Kryptex nodes, one per line, best first.
$SwitchFile = Join-Path $Root 'SWITCH_NODES'
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Tuned on 2026-10-25 (memory 6001 MHz): core 800 MHz = 27.1 TH/s at 72 C / 84 W; 900 MHz =
# 30.6 TH/s at 74-76 C; 1000 MHz = 77-78 C. Hashrate follows the core clock; memory above 6001
# only adds heat (7001: same hashrate, +14 W), while 810 MHz cuts hashrate to a quarter.
$MemoryMHz = Setting 'memory_mhz' 6001
$StartCeiling = Setting 'start_ceiling_mhz' 800; $MaxCeiling = Setting 'max_ceiling_mhz' 900; $MinCeiling = Setting 'min_ceiling_mhz' 600
$StepDown = 50; $StepUp = 50; $StepSeconds = 15
$SlowAt = Setting 'slow_at_c' 74; $StopAt = Setting 'stop_at_c' 80; $CoolAt = Setting 'cool_at_c' 70; $CoolMinutes = 3
$CpuMaxWhileMining = Setting 'cpu_max_percent' 80
# Fan floor while mining (percent), or none to leave the fans to the driver. "office 2" needs it: under
# the driver's zero-RPM policy its fans were still stopped when the GPU went from 39 to 80 C in 15 s.
$FanFloor = Setting 'fan_min_percent' $null
# Emergency: shut the laptop down at 88 C, or if it stays at 85 C for a minute after mining stopped.
$ShutdownAt = 88; $HotAfterStop = 85
$Total = [regex]'Total:\s*([\d.]+)\s*([KMGTPE]?H/s)\s+shares:\s*(\d+) accepted\s+(\d+) stale\s+(\d+) rejected'
$Clock = [regex]'^(\d{2}:\d{2}:\d{2})\.\d+'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MinerJob {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimit {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit; public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize; public uint ActiveProcessLimit;
        public UIntPtr Affinity; public uint PriorityClass, SchedulingClass; }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong a, b, c, d, e, f; }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimit {
        public BasicLimit Basic; public IoCounters Io; public UIntPtr ProcessMemoryLimit, JobMemoryLimit,
        PeakProcessMemoryUsed, PeakJobMemoryUsed; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string n);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr j, int c, ref ExtendedLimit i, uint l);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll")] static extern bool GetConsoleMode(IntPtr h, out uint m);
    [DllImport("kernel32.dll")] static extern bool SetConsoleMode(IntPtr h, uint m);
    public static void NoQuickEdit() {
        IntPtr input = GetStdHandle(-10); uint mode;
        if (GetConsoleMode(input, out mode)) SetConsoleMode(input, (mode & ~0x40u) | 0x80u);
    }
    static IntPtr job;
    public static void Attach(IntPtr process) {
        job = CreateJobObject(IntPtr.Zero, null);
        var info = new ExtendedLimit(); info.Basic.LimitFlags = 0x2000; // KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(typeof(ExtendedLimit))) ||
            !AssignProcessToJobObject(job, process)) throw new Exception("job object failed");
    }
}
'@

[MinerJob]::NoQuickEdit()

if ($null -ne $FanFloor) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NvmlFans {
    const string Dll = @"C:\Windows\System32\nvml.dll";
    [DllImport(Dll)] static extern int nvmlInit_v2();
    [DllImport(Dll)] static extern int nvmlDeviceGetHandleByIndex_v2(uint i, out IntPtr h);
    [DllImport(Dll)] static extern int nvmlDeviceGetNumFans(IntPtr h, out uint n);
    [DllImport(Dll)] static extern int nvmlDeviceSetFanSpeed_v2(IntPtr h, uint f, uint s);
    [DllImport(Dll)] static extern int nvmlDeviceSetDefaultFanSpeed_v2(IntPtr h, uint f);
    static IntPtr gpu; static uint fans;
    public static bool Open() {
        return nvmlInit_v2() == 0 && nvmlDeviceGetHandleByIndex_v2(0, out gpu) == 0 &&
               nvmlDeviceGetNumFans(gpu, out fans) == 0 && fans > 0;
    }
    public static bool Set(uint percent) {
        for (uint f = 0; f < fans; f++) if (nvmlDeviceSetFanSpeed_v2(gpu, f, percent) != 0) return false;
        return true;
    }
    public static void Restore() { for (uint f = 0; f < fans; f++) nvmlDeviceSetDefaultFanSpeed_v2(gpu, f); }
}
'@
}

# Same curve as the office PC's fan_control.py: 40 % at 40 C rising to 100 % at 75 C, never below the floor.
function Fan-Target($temp) { [int][Math]::Max($FanFloor, [Math]::Min(100, [Math]::Round(40 + ($temp - 40) * 60 / 35))) }

function Now { (Get-Date).ToString('o') }

function Read-Gpu {
    $line = & nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,utilization.gpu,clocks.mem --format=csv,noheader,nounits 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $line) { return $null }
    $p = ($line | Select-Object -First 1).Split(',') | ForEach-Object { $_.Trim() }
    $num = { param($v) $d = 0.0; if ([double]::TryParse($v, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) { $d } else { $null } }
    $temp = & $num $p[0]
    if ($null -eq $temp) { return $null }
    [pscustomobject]@{ Temp = $temp; Power = (& $num $p[1]); Core = (& $num $p[2]); Util = (& $num $p[3]); Memory = (& $num $p[4]); At = (Now) }
}

function Set-Ceiling([int]$mhz) {
    & nvidia-smi -lgc "210,$mhz" 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Set-Memory([int]$mhz) {
    & nvidia-smi -lmc "$mhz,$mhz" 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-CpuMax {
    $q = powercfg /qh SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX
    $ac = ($q | Select-String '0x[0-9a-fA-F]{8}' -AllMatches | Select-Object -Last 2 | Select-Object -First 1)
    if ($ac) { [Convert]::ToInt32($ac.Matches[0].Value, 16) } else { 100 }
}

function Set-CpuMax([int]$percent) {
    powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX $percent | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
}

$state = @{ running = $false; expected = 'running'; stop = $null; rate = $null; unit = $null; rateAt = $null
            accepted = $null; stale = $null; rejected = $null; sharesAt = $null; lastAccepted = $null
            gpu = $null; ceiling = $StartCeiling }

function Write-Status {
    $g = $state.gpu
    $body = [ordered]@{
        computer_name = $env:COMPUTERNAME; worker = $Config.worker
        expected_state = $state.expected; miner_running = $state.running
        hashrate = [ordered]@{ value = $state.rate; unit = $state.unit; data_at = $state.rateAt }
        shares = [ordered]@{ accepted = $state.accepted; stale = $state.stale; rejected = $state.rejected }
        shares_data_at = $state.sharesAt; last_accepted_share_at = $state.lastAccepted
        gpus = @(if ($g) { [ordered]@{ index = 0; temperature_c = $g.Temp; fan_percent = $fanNow; power_w = $g.Power } })
        gpu_data_at = $(if ($g) { $g.At } else { $null }); data_at = (Now)
        stop_reason = $(if ($state.running) { $null } else { $state.stop })
        core_ceiling_mhz = $state.ceiling
    }
    $tmp = "$StatusFile.tmp"
    # UTF-8 without a BOM: the aggregator parses this as JSON.
    [IO.File]::WriteAllText($tmp, ($body | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))
    # The office PC reads this file over SSH every 10 s; a replace that lands on that read fails
    # ("file already exists", "office 2" 2026-10-29) and once stopped mining. Retry, else skip a beat.
    for ($try = 0; $try -lt 5; $try++) {
        try { Move-Item -Force $tmp $StatusFile -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds 200 }
    }
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
}

$script:HasBattery = $true
function On-Ac {
    # A desktop has no battery: after the first empty answer stop asking (every 2 s it filled the WMI log).
    if (-not $script:HasBattery) { return $true }
    $b = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $b) { $script:HasBattery = $false; return $true }  # No battery data: do not stop on a guess.
    return [bool]$b.PowerOnline
}

function Emergency-Shutdown($why) {
    Write-Host "$(Get-Date -Format HH:mm:ss) EMERGENCY: $why; shutting down in 60 s"
    & shutdown.exe /s /t 60 /c "Miner guard: $why. Shutting down to protect the laptop." 2>&1 | Out-Null
}

function Stop-Mining($reason, $expected = 'running') {
    $state.running = $false; $state.stop = $reason; $state.expected = $expected
    Write-Host "$(Get-Date -Format HH:mm:ss) stop: $reason"
}

# ---- Start checks
$running = Get-Process -Name 'krig-miner', 'SRBMiner-MULTI' -ErrorAction SilentlyContinue
if ($running) { Write-Host 'A miner is already running; not starting a second one.'; exit 1 }
Remove-Item -Force $StopFile, $SwitchFile -ErrorAction SilentlyContinue
$first = Read-Gpu
if (-not $first) { Stop-Mining 'invalid_gpu_telemetry'; Write-Status; exit 1 }
if ($first.Temp -ge $SlowAt) { Write-Host "GPU is $($first.Temp) C; wait until it is below $SlowAt C."; exit 1 }
if (-not (On-Ac)) { Write-Host 'Running on battery; plug in the charger before mining.'; exit 1 }
if (-not (Set-Ceiling $StartCeiling) -or -not (Set-Memory $MemoryMHz)) {
    & nvidia-smi -rgc 2>&1 | Out-Null; & nvidia-smi -rmc 2>&1 | Out-Null
    Stop-Mining 'gpu_limits_mismatch'; $state.gpu = $first; Write-Status; exit 1
}
$fanNow = $null
if ($null -ne $FanFloor) {
    $fanNow = Fan-Target $first.Temp
    if (-not [NvmlFans]::Open() -or -not [NvmlFans]::Set($fanNow)) {
        & nvidia-smi -rgc 2>&1 | Out-Null; & nvidia-smi -rmc 2>&1 | Out-Null
        Stop-Mining 'gpu_limits_mismatch'; $state.gpu = $first; Write-Status; exit 1
    }
}

$cpuBefore = Get-CpuMax
$exe = Join-Path $Root 'krig-1.5.12\krig-miner.exe'
# "miner": "srbminer" runs SRBMiner-Multi instead (srb_exe, relative to this folder). On "office 2"
# (desktop 3070, 2026-10-29) version 3.8.1 gave 52 TH/s at 73 W where krig gave 42.5 TH/s at 125 W,
# shares accepted; it keeps a 2 % dev fee. Its numbers come from its local API instead of a log line.
$MinerKind = Setting 'miner' 'krig'
$SrbApi = 'http://127.0.0.1:21550'
if ($MinerKind -eq 'srbminer') { $exe = Join-Path $Root (Setting 'srb_exe' 'srbminer-3.8.1\SRBMiner-Multi-3-8-1\SRBMiner-MULTI.exe') }
# This laptop finds a share about every 7 minutes on average (median 5), with random gaps: 24 min
# twice in one night. 45 min restarts the miner (~0.3 times a day by chance alone); 90 min in total
# without a share almost never happens by chance (about 2 in a million), so then something is wrong.
$ShareRestartMinutes = Setting 'share_restart_minutes' 45
$MinerRestartsPerHour = 5

# Kryptex nodes for the same account (earnings still convert to BTC). From Taiwan on 2026-10-26:
# Hong Kong ~65 ms, Singapore ~95 ms, the global name (Germany) ~250 ms.
# "pool_nodes" in the config replaces them (an outage test points it at a local relay).
$KryptexNodes = @(Setting 'pool_nodes' @('prl-hk.kryptex.network', 'prl-sg.kryptex.network', 'prl.kryptex.network'))

# Nodes fastest first by TCP connect time (3 tries each); unreachable ones last. `$avoid` goes last
# too, so a restart after a share drought moves off the node that was in use.
function Get-NodeOrder([string]$avoid = '') {
    $timed = foreach ($node in $KryptexNodes) {
        $samples = @()
        for ($i = 0; $i -lt 3; $i++) {
            $client = New-Object System.Net.Sockets.TcpClient
            $watch = [Diagnostics.Stopwatch]::StartNew()
            try { if ($client.ConnectAsync($node, 8048).Wait(3000) -and $client.Connected) { $samples += $watch.Elapsed.TotalMilliseconds } }
            catch { } finally { $client.Close() }
        }
        $ms = if ($samples.Count) { ($samples | Sort-Object)[[int][Math]::Floor($samples.Count / 2)] } else { [double]::MaxValue }
        if ($node -eq $avoid) { $ms = [double]::MaxValue - 1 }
        [pscustomobject]@{ Node = $node; Ms = $ms }
    }
    $timed | Sort-Object Ms | ForEach-Object { $_.Node }
}

function Test-PoolReachable {
    foreach ($node in $KryptexNodes) {
        $client = New-Object System.Net.Sockets.TcpClient
        try { if ($client.ConnectAsync($node, 8048).Wait(3000) -and $client.Connected) { return $true } }
        catch { } finally { $client.Close() }
    }
    return $false
}

function New-MinerLog { Join-Path $LogDir "mining-$(Get-Date -Format 'yyyyMMdd-HHmmss').log" }

# krig failover pools: one --url per node in order, --user once; krig moves down the list on its own
# when a node drops.
function Start-Miner($logPath, [string[]]$nodes) {
    if ($MinerKind -eq 'srbminer') {
        # SRBMiner failover pools: comma lists with one entry per pool, in order.
        $pools = @($nodes | ForEach-Object { "$($_):8048" })
        $minerArgs = @('--algorithm', 'pearlhash', '--pool', ($pools -join ','),
                       '--wallet', ((@("$($Config.account).$($Config.worker)") * $pools.Count) -join ','),
                       '--password', ((@('x') * $pools.Count) -join ','), '--tls', ((@('true') * $pools.Count) -join ','),
                       '--disable-cpu', '--disable-gpu-amd', '--disable-gpu-intel',
                       '--api-enable', '--api-port', '21550', '--log-file', $logPath)
        # Its console says why it quit when that happens before the log is open.
        $process = Start-Process -FilePath $exe -ArgumentList $minerArgs -WorkingDirectory (Split-Path -Parent $exe) -WindowStyle Hidden -PassThru `
                                 -RedirectStandardOutput ($logPath -replace '\.log$', '-console.txt')
        [MinerJob]::Attach($process.Handle)
        return $process
    }
    $minerArgs = @('--coin', 'pearl')
    foreach ($node in $nodes) { $minerArgs += @('--url', "stratum+ssl://$($node):8048") }
    $minerArgs += @('--user', "$($Config.account).$($Config.worker)",
                    '--devices', '0', '--no-rocm', '--no-intel', '--gpu-coffset', '0', '--gpu-moffset', '0',
                    '--gpu-no-reset-oc', '--no-tui', '--log-file', $logPath)
    $process = Start-Process -FilePath $exe -ArgumentList $minerArgs -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    [MinerJob]::Attach($process.Handle)
    return $process
}

$log = New-MinerLog
$miner = $null
try {
    Set-CpuMax $CpuMaxWhileMining
    $nodes = @(Get-NodeOrder)
    Write-Host "$(Get-Date -Format HH:mm:ss) pool nodes, fastest first: $($nodes -join ', ')"
    $miner = Start-Miner $log $nodes
    $state.running = $true; $state.gpu = $first
    Write-Host "$(Get-Date -Format HH:mm:ss) mining as $($Config.worker), ceiling $StartCeiling MHz; log $log"

    $started = Get-Date; $lastShare = Get-Date; $lastLogChange = Get-Date; $lastLogSize = -1
    $lastStep = [datetime]::MinValue; $coolSince = $null; $badReads = 0; $emergency = $null; $shareRestarts = 0
    $lowMemorySince = $null; $lastMemoryRestart = $null; $netDown = $false; $netChecked = [datetime]::MinValue
    $minerExits = @()
    while ($state.running) {
        Start-Sleep -Seconds 2
        if (Test-Path $StopFile) { Stop-Mining 'user_requested' 'stopped'; break }
        if (Test-Path $SwitchFile) {
            # Only known nodes, in the requested order; any missing ones follow as further failovers.
            $wanted = @(Get-Content $SwitchFile | ForEach-Object { $_.Trim() } | Where-Object { $KryptexNodes -contains $_ })
            Remove-Item -Force $SwitchFile -ErrorAction SilentlyContinue
            if ($wanted.Count) {
                foreach ($node in $KryptexNodes) { if ($wanted -notcontains $node) { $wanted += $node } }
                Write-Host "$(Get-Date -Format HH:mm:ss) switching pool nodes on request: $($wanted -join ', ')"
                Stop-Process -Id $miner.Id -Force -ErrorAction SilentlyContinue
                [void]$miner.WaitForExit(15000)
                $nodes = $wanted
                $log = New-MinerLog
                $miner = Start-Miner $log $nodes
                $state.accepted = $null; $lastShare = Get-Date; $lastLogChange = Get-Date; $lastLogSize = -1
            }
        }
        if ($miner.HasExited) {
            # SRBMiner restarts itself after an internal error ("office 2" 2026-11-01 13:28, 0x2007), as a
            # new process this guard does not watch, and the old stop here left the machine idle. Clear
            # whatever is left and start a fresh miner.
            $minerExits = @($minerExits | Where-Object { ((Get-Date) - $_).TotalMinutes -lt 60 })
            if ($minerExits.Count -ge $MinerRestartsPerHour) { Stop-Mining 'miner_exited'; break }
            $minerExits += Get-Date
            Write-Host "$(Get-Date -Format HH:mm:ss) miner exited (code $($miner.ExitCode)); starting it again"
            Get-Process -Name 'krig-miner', 'SRBMiner-MULTI' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 5
            $log = New-MinerLog
            $nodes = @(Get-NodeOrder)
            Write-Host "$(Get-Date -Format HH:mm:ss) pool nodes, fastest first: $($nodes -join ', ')"
            $miner = Start-Miner $log $nodes
            $state.accepted = $null; $lastShare = Get-Date; $lastLogChange = Get-Date; $lastLogSize = -1
            continue
        }
        if (-not (On-Ac)) { Stop-Mining 'on_battery'; break }

        $g = Read-Gpu
        if (-not $g) { $badReads++; if ($badReads -ge 3) { Stop-Mining 'invalid_gpu_telemetry'; break } }
        else {
            $badReads = 0; $state.gpu = $g
            if ($g.Temp -ge $ShutdownAt) { Stop-Mining 'temperature_cutoff'; $emergency = "GPU $($g.Temp) C"; break }
            if ($g.Temp -ge $StopAt) { Stop-Mining 'temperature_cutoff'; break }
            if ($null -ne $fanNow) {
                # Up at once; down only by 4 points or more, so the fans do not hunt.
                $want = Fan-Target $g.Temp
                if ($want -gt $fanNow -or $fanNow - $want -ge 4) {
                    if (-not [NvmlFans]::Set($want)) { Stop-Mining 'gpu_limits_mismatch'; break }
                    $fanNow = $want
                }
            }
            # Something else raised the clock (another tool, a driver reset): put our ceiling back.
            if ($null -ne $g.Core -and $g.Core -gt $state.ceiling + 60) {
                if (-not (Set-Ceiling $state.ceiling)) { Stop-Mining 'gpu_limits_mismatch'; break }
                Write-Host "$(Get-Date -Format HH:mm:ss) clock $($g.Core) MHz above ceiling; re-applied $($state.ceiling) MHz"
            }
            # Re-applied even at the minimum, so the ceiling never depends on an earlier call holding.
            if ($g.Temp -ge $SlowAt -and ((Get-Date) - $lastStep).TotalSeconds -ge $StepSeconds) {
                $state.ceiling = [Math]::Max($MinCeiling, $state.ceiling - $StepDown); $lastStep = Get-Date; $coolSince = $null
                if (-not (Set-Ceiling $state.ceiling)) { Stop-Mining 'gpu_limits_mismatch'; break }
                Write-Host "$(Get-Date -Format HH:mm:ss) $($g.Temp) C: ceiling $($state.ceiling) MHz"
            }
            if ($g.Temp -le $CoolAt) {
                if (-not $coolSince) { $coolSince = Get-Date }
                if (((Get-Date) - $coolSince).TotalMinutes -ge $CoolMinutes -and $state.ceiling -lt $MaxCeiling) {
                    $state.ceiling = [Math]::Min($MaxCeiling, $state.ceiling + $StepUp); $coolSince = Get-Date
                    if (-not (Set-Ceiling $state.ceiling)) { Stop-Mining 'gpu_limits_mismatch'; break }
                    Write-Host "$(Get-Date -Format HH:mm:ss) $($g.Temp) C: ceiling back up to $($state.ceiling) MHz"
                }
            } else { $coolSince = $null }
        }

        if ($MinerKind -eq 'srbminer') {
            # A live API counts as progress (instead of a growing log); 3 minutes without it stops.
            $api = $null
            try { $api = (Invoke-RestMethod -Uri $SrbApi -TimeoutSec 3).algorithms[0] } catch { }
            if ($api) {
                $lastLogChange = Get-Date; $at = Now
                $accepted = [int]$api.shares.accepted
                if ($null -eq $state.accepted -or $accepted -gt $state.accepted) { if ($accepted -gt 0) { $state.lastAccepted = $at; $lastShare = Get-Date; $shareRestarts = 0 } }
                $state.rate = [Math]::Round([double]$api.hashrate.gpu.total / 1e12, 2); $state.unit = 'TH/s'
                $state.rateAt = $at; $state.sharesAt = $at
                $state.accepted = $accepted; $state.stale = 0; $state.rejected = [int]$api.shares.rejected
            }
        }
        elseif (Test-Path $log) {
            $size = (Get-Item $log).Length
            if ($size -ne $lastLogSize) { $lastLogSize = $size; $lastLogChange = Get-Date }
            $tail = Get-Content $log -Tail 40
            for ($i = $tail.Count - 1; $i -ge 0; $i--) {
                $m = $Total.Match($tail[$i])
                if (-not $m.Success) { continue }
                $at = Now
                $c = $Clock.Match($tail[$i]); if ($c.Success) { $at = [datetime]::SpecifyKind([datetime]::ParseExact("$((Get-Date).ToString('yyyy-MM-dd')) $($c.Groups[1].Value)", 'yyyy-MM-dd HH:mm:ss', $null), 'Local').ToString('o') }
                $accepted = [int]$m.Groups[3].Value
                if ($null -eq $state.accepted -or $accepted -gt $state.accepted) { if ($accepted -gt 0) { $state.lastAccepted = $at; $lastShare = Get-Date; $shareRestarts = 0 } }
                $state.rate = [double]::Parse($m.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
                $state.unit = $m.Groups[2].Value; $state.rateAt = $at; $state.sharesAt = $at
                $state.accepted = $accepted; $state.stale = [int]$m.Groups[4].Value; $state.rejected = [int]$m.Groups[5].Value
                break
            }
        }
        if (((Get-Date) - $lastLogChange).TotalMinutes -ge 3) {
            # A hung miner: end it, and the exit branch above starts a new one on the next pass.
            Write-Host "$(Get-Date -Format HH:mm:ss) miner silent for 3 minutes; ending it"
            Stop-Process -Id $miner.Id -Force -ErrorAction SilentlyContinue
            [void]$miner.WaitForExit(15000)
            $lastLogChange = Get-Date
            continue
        }
        if ($netDown -or ((Get-Date) - $lastShare).TotalMinutes -ge $ShareRestartMinutes) {
            # Network check: at once when the drought runs out, then once a minute while down.
            $netUp = $true
            if (-not $netDown -or ((Get-Date) - $netChecked).TotalSeconds -ge 60) { $netChecked = Get-Date; $netUp = Test-PoolReachable }
            elseif ($netDown) { $netUp = $false }
            if (-not $netUp) {
                if (-not $netDown) { Write-Host "$(Get-Date -Format HH:mm:ss) no pool node reachable (network down); waiting instead of stopping" }
                $netDown = $true
            } elseif ($netDown) {
                $netDown = $false; $lastShare = Get-Date; $shareRestarts = 0
                Write-Host "$(Get-Date -Format HH:mm:ss) network back; the miner gets a fresh $ShareRestartMinutes minutes for shares"
            }
        }
        if (-not $netDown -and ((Get-Date) - $lastShare).TotalMinutes -ge $ShareRestartMinutes) {
            if ($shareRestarts -ge 1) { Stop-Mining 'no_accepted_shares'; break }
            $shareRestarts++
            Write-Host "$(Get-Date -Format HH:mm:ss) no accepted share for $ShareRestartMinutes min; restarting the miner to reconnect"
            Stop-Process -Id $miner.Id -Force -ErrorAction SilentlyContinue
            [void]$miner.WaitForExit(15000)
            $log = New-MinerLog
            $nodes = @(Get-NodeOrder $nodes[0])
            Write-Host "$(Get-Date -Format HH:mm:ss) pool nodes now: $($nodes -join ', ')"
            $miner = Start-Miner $log $nodes
            # The new miner counts shares from zero.
            $state.accepted = $null; $lastShare = Get-Date; $lastLogChange = Get-Date; $lastLogSize = -1
        }
        # "office 2" once dropped into P5 mid-run (memory 810 MHz, a quarter of the hashrate) and stayed
        # there though the lock was re-applied; a new miner process brought P2 back. Twice within
        # 10 minutes means something else holds the card down, so stop instead of looping.
        if ($g -and $null -ne $g.Memory -and $g.Memory -lt $MemoryMHz / 2) {
            if (-not $lowMemorySince) { $lowMemorySince = Get-Date }
            if (((Get-Date) - $lowMemorySince).TotalSeconds -ge 60) {
                if ($lastMemoryRestart -and ((Get-Date) - $lastMemoryRestart).TotalMinutes -lt 10) { Stop-Mining 'gpu_limits_mismatch'; break }
                Write-Host "$(Get-Date -Format HH:mm:ss) memory clock $($g.Memory) MHz for a minute (power-saving state); restarting the miner"
                Stop-Process -Id $miner.Id -Force -ErrorAction SilentlyContinue
                [void]$miner.WaitForExit(15000)
                if (-not (Set-Memory $MemoryMHz)) { Stop-Mining 'gpu_limits_mismatch'; break }
                $log = New-MinerLog
                $miner = Start-Miner $log $nodes
                $state.accepted = $null; $lastShare = Get-Date; $lastLogChange = Get-Date; $lastLogSize = -1
                $lowMemorySince = $null; $lastMemoryRestart = Get-Date
            }
        } else { $lowMemorySince = $null }
        Write-Status
    }
} catch {
    Stop-Mining 'unknown'
    Write-Host "error: $($_.Exception.Message)"
} finally {
    if ($miner -and -not $miner.HasExited) { Stop-Process -Id $miner.Id -Force -ErrorAction SilentlyContinue }
    & nvidia-smi -rgc 2>&1 | Out-Null
    & nvidia-smi -rmc 2>&1 | Out-Null
    # After a temperature stop the fans stay at full speed through the cool-down watch below.
    if ($null -ne $fanNow) { if ($state.stop -eq 'temperature_cutoff') { [void][NvmlFans]::Set(100); $fanNow = 100 } else { [NvmlFans]::Restore(); $fanNow = $null } }
    Set-CpuMax $cpuBefore
    if ($state.running) { Stop-Mining 'user_requested' 'stopped' }
    Write-Status
    Remove-Item -Force $StopFile -ErrorAction SilentlyContinue
    Write-Host "$(Get-Date -Format HH:mm:ss) miner stopped, clocks and CPU limit restored."
    if ($emergency) { Emergency-Shutdown $emergency }
    elseif ($state.stop -eq 'temperature_cutoff') {
        # Mining is off; if the GPU still does not cool, something else is wrong: shut down.
        $hotSince = $null
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 2
            $g = Read-Gpu
            if (-not $g) { continue }
            $state.gpu = $g; Write-Status
            if ($g.Temp -ge $ShutdownAt) { Emergency-Shutdown "GPU $($g.Temp) C after stopping"; break }
            if ($g.Temp -ge $HotAfterStop) {
                if (-not $hotSince) { $hotSince = Get-Date }
                if (((Get-Date) - $hotSince).TotalSeconds -ge 60) { Emergency-Shutdown "GPU still $($g.Temp) C a minute after stopping"; break }
            } else { $hotSince = $null }
        }
    }
    if ($null -ne $fanNow) { [NvmlFans]::Restore() }
}
