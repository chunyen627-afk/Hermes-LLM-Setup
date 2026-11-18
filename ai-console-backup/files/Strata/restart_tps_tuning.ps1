param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference='Stop'
if ($Config -notmatch '^strata-(tune-[a-z0-9-]+|orca-iq3_xxs(?:-image)?)\.json$') { throw 'Unexpected tuning config name' }
$configPath=Join-Path $PSScriptRoot $Config
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Missing tuning config' }
$settings=Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($settings.model_name -ne 'orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs') { throw 'Wrong model' }
$listeners=@(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count) {
    $status=Invoke-RestMethod 'http://127.0.0.1:8001/v1/status' -TimeoutSec 5
    if ($status.service -ne 'strata' -or $status.activity.in_flight) { throw 'Model busy or wrong service; not stopped' }
    $all=@(Get-CimInstance Win32_Process)
    $targets=@()
    foreach ($owner in @($listeners.OwningProcess | Select-Object -Unique)) {
        $server=$all | Where-Object ProcessId -eq $owner
        if (-not $server.CommandLine.Contains($PSScriptRoot) -or $server.CommandLine -notmatch 'serve\.server') { throw 'Unexpected process identity' }
        $children=@($all | Where-Object ParentProcessId -eq $owner)
        foreach ($child in $children) {
            if ($child.Name -notin @('strata.exe','strata-vision.exe') -or -not $child.CommandLine.StartsWith($PSScriptRoot+'\engine\')) { throw 'Unexpected child process' }
        }
        $targets += $children
        $targets += $server
        $parent=$all | Where-Object ProcessId -eq $server.ParentProcessId
        if ($parent.Name -eq 'python.exe' -and $parent.CommandLine -match 'serve\.server' -and $parent.CommandLine.Contains($PSScriptRoot)) { $targets += $parent }
    }
    foreach ($target in $targets | Sort-Object ProcessId -Unique) {
        try { Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop }
        catch {
            # The server's Windows job can already have closed its verified children.
            if (Get-Process -Id $target.ProcessId -ErrorAction SilentlyContinue) { throw }
        }
    }
}
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) { throw 'Old listener still active' }
# The user explicitly requests visible service windows, including tuning runs.
$batch=Join-Path $PSScriptRoot 'RUN-TPS-TUNING.bat'
if ($Config -eq 'strata-orca-iq3_xxs-image.json') { $batch=Join-Path $PSScriptRoot 'RUN-CHAT-IMAGE.bat' }
if ($Config -eq 'strata-orca-iq3_xxs.json') { $batch=Join-Path $PSScriptRoot 'RUN-CHAT-VIDEO.bat' }
$window=Start-Process -FilePath 'cmd.exe' -ArgumentList ('/d /c ""'+$batch+'" "'+$Config+'""') -WorkingDirectory $PSScriptRoot -WindowStyle Normal -PassThru
[pscustomobject]@{Started=$true;Config=$Config;WindowPid=$window.Id;Readiness='Pending; verify /v1/status before testing'} | ConvertTo-Json -Compress
