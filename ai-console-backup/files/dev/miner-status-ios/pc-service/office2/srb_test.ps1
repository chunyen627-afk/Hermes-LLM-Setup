# Short SRBMiner (pearlhash) test on office 2, same Kryptex account and worker as the guarded krig miner.
# Runs each "core,mem" pair for $Minutes, logs every 30 s, stops at $StopC (and never starts above $StartMaxC).
# The guarded krig miner must be stopped first; this script restores clocks and fans when it ends.
#   powershell -File srb_test.ps1 -Version 3.8.1 -Sets "1150,5001,6,pearlhash;1300,5001,5,quantus"
# Each set is core,mem[,minutes[,algorithm]]; pearlhash mines PRL, quantus mines QTC (same Kryptex account).
param([string]$Sets = '990,5001', [double]$Minutes = 4, [int]$StopC = 80, [int]$StartMaxC = 70, [string]$Version = '3.8.1')
$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\pjunm\Pearl-mining'
$Pools = @{ pearlhash = 'prl-hk.kryptex.network:8048'; quantus = 'qtc-hk.kryptex.network:8049' }
$Exe = Get-ChildItem "$Root\srbminer-$Version" -Recurse -Filter 'SRBMiner-MULTI.exe' | Select-Object -First 1
if (-not $Exe) { "SRBMiner-MULTI.exe not found under srbminer-$Version (removed by antivirus?)"; exit 1 }
$Config = Get-Content "$Root\laptop-config.json" -Raw | ConvertFrom-Json
$Out = Join-Path $Root ("logs\srb-test-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
function Say($s) { $line = (Get-Date -Format 'HH:mm:ss') + ' ' + $s; $line; Add-Content $Out $line }

Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class NvmlFans {
    const string Dll = @"C:\Windows\System32\nvml.dll";
    [DllImport(Dll)] static extern int nvmlInit_v2();
    [DllImport(Dll)] static extern int nvmlDeviceGetHandleByIndex_v2(uint i, out IntPtr h);
    [DllImport(Dll)] static extern int nvmlDeviceGetNumFans(IntPtr h, out uint n);
    [DllImport(Dll)] static extern int nvmlDeviceSetFanSpeed_v2(IntPtr h, uint f, uint s);
    [DllImport(Dll)] static extern int nvmlDeviceSetDefaultFanSpeed_v2(IntPtr h, uint f);
    static IntPtr gpu; static uint fans;
    public static bool Open() { return nvmlInit_v2() == 0 && nvmlDeviceGetHandleByIndex_v2(0, out gpu) == 0 && nvmlDeviceGetNumFans(gpu, out fans) == 0 && fans > 0; }
    public static bool Set(uint percent) { for (uint f = 0; f < fans; f++) if (nvmlDeviceSetFanSpeed_v2(gpu, f, percent) != 0) return false; return true; }
    public static void Restore() { for (uint f = 0; f < fans; f++) nvmlDeviceSetDefaultFanSpeed_v2(gpu, f); }
}
'@
# office 2 fan curve (same as the guard): 40 % at 40 C to 100 % at 75 C, never below 50 %
function Fan-Target($t) { [int][Math]::Max(50, [Math]::Min(100, [Math]::Round(40 + ($t - 40) * 60 / 35))) }
function Read-Gpu {
    $v = (& nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,clocks.mem --format=csv,noheader,nounits) -split ',\s*'
    [pscustomobject]@{ Temp = [int]$v[0]; Power = [double]$v[1]; Sm = [int]$v[2]; Mem = [int]$v[3] }
}
function Read-Api {
    try {
        $j = Invoke-RestMethod -Uri 'http://127.0.0.1:21550' -TimeoutSec 3
        $a = $j.algorithms[0]
        [pscustomobject]@{ Hs = [double]$a.hashrate.gpu.total; Acc = [int]$a.shares.accepted; Rej = [int]$a.shares.rejected }
    } catch { $null }
}

if (Get-Process -Name krig-miner -ErrorAction SilentlyContinue) { Say 'krig-miner is running: stop the guard first'; exit 1 }
if (-not [NvmlFans]::Open()) { Say 'NVML fan control unavailable: not starting'; exit 1 }
$fan = 60; [void][NvmlFans]::Set($fan)
$proc = $null
try {
    foreach ($set in ($Sets -split ';')) {
        $core, $mem, $mins, $algo = $set -split ','
        $mins = if ($mins) { [double]$mins } else { $Minutes }
        if (-not $algo) { $algo = 'pearlhash' }
        $div, $unit = if ($algo -eq 'quantus') { 1e6, 'MH/s' } else { 1e12, 'TH/s' }
        $a = $null
        $g = Read-Gpu
        while ($g.Temp -gt $StartMaxC) { Start-Sleep 5; $g = Read-Gpu }
        & nvidia-smi -lgc "210,$core" | Out-Null
        & nvidia-smi -lmc "$mem,$mem" | Out-Null
        $log = Join-Path $Root ("logs\srb-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
        $minerArgs = @('--algorithm', $algo, '--pool', $Pools[$algo], '--tls', 'true',
                  '--wallet', "$($Config.account).$($Config.worker)", '--password', 'x',
                  '--disable-cpu', '--disable-gpu-amd', '--disable-gpu-intel',
                  '--api-enable', '--api-port', '21550', '--log-file', $log)
        Say "set $algo core $core MHz mem $mem MHz for $mins min: starting SRBMiner $($Exe.Directory.Name)"
        $proc = Start-Process -FilePath $Exe.FullName -ArgumentList $minerArgs -WorkingDirectory $Exe.DirectoryName -WindowStyle Hidden -PassThru
        $t0 = Get-Date; $next = $t0.AddSeconds(30); $samples = @()
        while (((Get-Date) - $t0).TotalMinutes -lt $mins) {
            Start-Sleep 2
            if ($proc.HasExited) { Say "SRBMiner exited with $($proc.ExitCode)"; break }
            $g = Read-Gpu
            $want = Fan-Target $g.Temp
            if ($want -gt $fan -or $fan - $want -ge 4) { [void][NvmlFans]::Set($want); $fan = $want }
            if ($g.Temp -ge $StopC) { Say "STOP $($g.Temp) C >= $StopC C"; break }
            if ((Get-Date) -ge $next) {
                $next = $next.AddSeconds(30)
                $a = Read-Api
                $hs = if ($a) { $a.Hs / $div } else { 0 }
                if (((Get-Date) - $t0).TotalSeconds -ge 90 -and $hs -gt 0) { $samples += [pscustomobject]@{ Hs = $hs; W = $g.Power } }
                Say ("{0:N2} $unit  {1:N0} W  {2:N0} per kW  {3} C  fan {4}%  core {5}  mem {6}  acc {7} rej {8}" -f $hs, $g.Power,
                     ($(if ($g.Power) { $hs * 1000 / $g.Power } else { 0 })), $g.Temp, $fan, $g.Sm, $g.Mem, $a.Acc, $a.Rej)
            }
        }
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force; $proc.WaitForExit(10000) | Out-Null }
        if ($samples.Count) {
            $hs = ($samples | Measure-Object Hs -Average).Average; $w = ($samples | Measure-Object W -Average).Average
            Say ("RESULT {6} core {0} mem {1}: {2:N2} $unit  {3:N0} W  {4:N0} per kW  ({5} samples after 90 s)  accepted {7} rejected {8}" -f $core, $mem, $hs, $w, ($hs * 1000 / $w), $samples.Count, $algo, $a.Acc, $a.Rej)
        } else { Say "RESULT core $core mem ${mem}: no samples" }
        if ($g.Temp -ge $StopC) { break }
    }
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    & nvidia-smi -rgc | Out-Null; & nvidia-smi -rmc | Out-Null
    [NvmlFans]::Restore()
    Say 'clocks and fans restored'
}
