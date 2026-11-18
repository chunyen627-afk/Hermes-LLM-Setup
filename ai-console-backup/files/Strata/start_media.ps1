param([ValidateSet('Image','Video')][string]$Media = 'Video', [switch]$NoBrowser, [switch]$Managed)
$ErrorActionPreference = 'Stop'
$strataRoot = $PSScriptRoot
$oldRoot = 'C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B'
$hermesExe = 'C:\Users\pjunm\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe'
$env:HERMES_HOME = 'C:\Users\pjunm\AppData\Local\hermes'
$modelId = 'swift-1.5-qwen3.8-flash-next-abliterated-iq3_xxs'
$configFile = if ($Media -eq 'Image') { 'strata-swift-iq3_xxs-image.json' } else { 'strata-swift-iq3_xxs-video.json' }
$selectedConfig = Get-Content -LiteralPath (Join-Path $strataRoot $configFile) -Raw | ConvertFrom-Json
$contextIndex = [array]::IndexOf($selectedConfig.args, '--max-context')
if ($contextIndex -lt 0) { throw 'Selected profile has no context limit' }
$context = [int]$selectedConfig.args[$contextIndex + 1]
$launchFile = if ($Media -eq 'Image') { 'RUN-CHAT-IMAGE.bat' } else { 'RUN-CHAT-VIDEO.bat' }
if (-not (Test-Path -LiteralPath (Join-Path $strataRoot 'packs\swift-abliterated-iq3_xxs\native_experts.txt'))) {
    throw 'Model preparation has not finished. Keep using the existing launcher until it completes.'
}
if (-not (Test-Path -LiteralPath 'C:\Users\pjunm\Strata-data\models\swift-abliterated-iq3_xxs\mmproj-Qwen3.8-Flash-Next-BF16.gguf')) {
    throw 'The model vision projector is not ready. Run PREPARE-VISION.bat first.'
}
$mutex = New-Object System.Threading.Mutex($false, 'Local\Hermes-Strata-Media-Startup')
try { $locked = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
if (-not $locked) { $mutex.Dispose(); Write-Host 'Startup is already running.'; exit 0 }
try {
    function Listener([int]$port, [string[]]$expected) {
        $items = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        foreach ($listenerId in ($items.OwningProcess | Select-Object -Unique)) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerId"
            $matches = @($expected | Where-Object { $p.CommandLine -and $p.CommandLine.Contains($_) })
            if (-not $matches.Count) { throw "Port $port belongs to another program; no process was stopped." }
            $p
        }
    }
    function ComfyIdle([int]$port) {
        $q = Invoke-RestMethod "http://127.0.0.1:$port/queue" -TimeoutSec 5
        if ($q.queue_running.Count -or $q.queue_pending.Count) { throw "Media service $port has an active job." }
    }
    function ChatIdle([int]$port, [bool]$isStrata) {
        if ($isStrata) {
            $s = Invoke-RestMethod "http://127.0.0.1:$port/v1/status" -TimeoutSec 5
            if ($s.activity.in_flight) { throw 'Strata is answering or has queued requests. Finish them before switching.' }
        } else {
            $s = Invoke-RestMethod "http://127.0.0.1:$port/slots" -TimeoutSec 5
            if (@($s | Where-Object { $_.is_processing }).Count) { throw 'The previous chat model is still answering.' }
        }
    }
    function StartVisible([string]$path) {
        if ($Managed) {
            $logName = 'managed-' + [IO.Path]::GetFileNameWithoutExtension($path)
            Start-Process -FilePath 'cmd.exe' -ArgumentList ('/d /c "' + $path + '"') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $strataRoot "$logName.out.log") -RedirectStandardError (Join-Path $strataRoot "$logName.err.log")
        } else {
            Start-Process -FilePath 'cmd.exe' -ArgumentList ('/d /c "' + $path + '"') -WindowStyle Normal
        }
    }
    function WaitService([int]$port, [string]$path) {
        for ($i = 0; $i -lt 240; $i++) {
            try { Invoke-RestMethod "http://127.0.0.1:$port$path" -TimeoutSec 2 | Out-Null; return }
            catch { Start-Sleep -Seconds 1 }
        }
        throw "Service $port did not become ready. Check its visible window."
    }
    # Validate identities and active work before stopping anything.
    $chat = @(Listener 8001 @($strataRoot, 'Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf'))
    $bridge = @(Listener 1234 @((Join-Path $oldRoot 'hermes_bridge.py')))
    $h3 = @(Listener 8189 @('C:\Users\pjunm\ComfyUI-H3\main.py'))
    $image = @(Listener 8188 @('C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI\main.py'))
    $proxy = @(Listener 8003 @('C:\Users\pjunm\OneDrive\Desktop\hermes\comfy_proxy.py'))
    $stop = @()
    $experiment = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
    foreach ($testId in ($experiment.OwningProcess | Select-Object -Unique)) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$testId"
        if ($p.CommandLine -and $p.CommandLine.Contains($strataRoot)) {
            ChatIdle 8080 $true
            $stop += $p
        }
    }
    $chatWillChange = -not $chat.Count
    foreach ($p in $chat) {
        $isStrata = $p.CommandLine.Contains($strataRoot)
        if (-not $isStrata -or -not $p.CommandLine.Contains((Join-Path $strataRoot $configFile))) {
            ChatIdle 8001 $isStrata
            $stop += $p
            $chatWillChange = $true
        }
    }
    if ($chatWillChange -and $h3.Count) { ComfyIdle 8189 }
    if ($chatWillChange -and $image.Count) { ComfyIdle 8188 }
    # Release idle image weights before reserving VRAM for a fresh chat process.
    # Otherwise the image allocation is counted twice (used + reserved), shrinking its expert cache.
    if ($chatWillChange -and $Media -eq 'Image' -and $image.Count) {
        Invoke-RestMethod 'http://127.0.0.1:8188/free' -Method Post -ContentType 'application/json' -Body '{"unload_models":true,"free_memory":true}' -TimeoutSec 5 | Out-Null
        Start-Sleep -Seconds 2
    }
    if ($Media -eq 'Image' -and $h3.Count) { ComfyIdle 8189; $stop += $h3 }
    if ($Media -eq 'Video' -and $image.Count) { ComfyIdle 8188; $stop += $image; $stop += $proxy }
    foreach ($p in @($h3 + $image)) {
        $selected = ($Media -eq 'Video' -and $h3.ProcessId -contains $p.ProcessId) -or
                    ($Media -eq 'Image' -and $image.ProcessId -contains $p.ProcessId)
        if ($selected -and $p.CommandLine -notmatch '\s--cuda-device\s+1(?:\s|$)') {
            ComfyIdle $(if ($Media -eq 'Video') { 8189 } else { 8188 })
            $stop += $p
        }
    }
    foreach ($p in ($stop | Sort-Object ProcessId -Unique)) {
        Write-Host "Stopping verified idle $($p.Name) (PID $($p.ProcessId))."
        Stop-Process -Id $p.ProcessId -Force
    }
    Start-Sleep -Seconds 2
    if (-not (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)) {
        $loading = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -eq 'python.exe' -and $_.CommandLine -and
            $_.CommandLine.Contains($strataRoot) -and $_.CommandLine -match '-m serve\.server' -and
            $_.CommandLine -match '--port\s+8001(?:\s|$)'
        })
        if ($loading.Count) { Write-Host 'Chat is loading; waiting for the existing process.' }
        else { StartVisible (Join-Path $strataRoot $launchFile) }
    }
    WaitService 8001 '/v1/models'
    $status = Invoke-RestMethod 'http://127.0.0.1:8001/v1/status' -TimeoutSec 5
    if ($status.service -ne 'strata' -or $status.model -ne $modelId -or -not $status.loaded -or $status.cache_max_tokens -ne $context -or -not $status.vision.enabled) {
        throw 'The running model/context is different from the selected profile; Hermes settings were not changed.'
    }
    $tools = @('file','terminal','skills','vision')
    $settings = [ordered]@{
        'model.default'=$modelId; 'model.provider'='lmstudio'; 'model.base_url'='http://127.0.0.1:1234/v1';
        'model.context_length'=$context; 'model.local_media_mode'=$Media; 'platform_toolsets.cli'=(ConvertTo-Json -InputObject @($tools) -Compress);
        'model.supports_vision'='true'; 'agent.image_input_mode'='native';
        'auxiliary.title_generation.enabled'='false';
        'auxiliary.compression.provider'='lmstudio'; 'auxiliary.compression.model'=$modelId;
        'auxiliary.compression.base_url'='http://127.0.0.1:1234/v1';
        'auxiliary.vision.provider'='lmstudio'; 'auxiliary.vision.model'=$modelId;
        'auxiliary.vision.base_url'='http://127.0.0.1:1234/v1'
    }
    # Keep a private rollback copy before the first successful activation.
    $backupRoot = 'C:\Users\pjunm\.cache\hermes-strata-backups'
    $originalConfig = Join-Path $env:HERMES_HOME 'config.yaml'
    $backupFile = Join-Path $backupRoot 'config-before-strata.yaml'
    if (-not (Test-Path -LiteralPath $backupFile)) {
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        Copy-Item -LiteralPath $originalConfig -Destination $backupFile -ErrorAction Stop
    }
    foreach ($entry in $settings.GetEnumerator()) {
        & $hermesExe config set $entry.Key ([string]$entry.Value) 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Hermes configuration failed: $($entry.Key)" }
    }
    if (-not $bridge.Count) { StartVisible (Join-Path $oldRoot '_bridge.bat') }
    WaitService 1234 '/v1/models'
    # This child inherits the controller job, so Stop All also closes remote access.
    $remoteConfig = Get-Content -LiteralPath (Join-Path $strataRoot 'zerotier-api.json') -Raw | ConvertFrom-Json
    $ztAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
        $_.IPAddress -eq $remoteConfig.bind_ip -and $_.InterfaceAlias -like '*ZeroTier*'
    }
    if ($ztAddress) {
        $remote = @(Listener ([int]$remoteConfig.port) @((Join-Path $strataRoot 'zerotier_api.py')))
        if (-not $remote.Count) { StartVisible (Join-Path $strataRoot 'RUN-ZEROTIER-API.bat') }
        $remoteReady = $false
        for ($r = 0; $r -lt 10; $r++) {
            try {
                Invoke-RestMethod "http://$($remoteConfig.bind_ip):$($remoteConfig.port)/v1/models" -TimeoutSec 2 | Out-Null
                $remoteReady = $true; break
            } catch { Start-Sleep -Seconds 1 }
        }
        if (-not $remoteReady) { Write-Warning 'ZeroTier API is unavailable. Local model services can still run.' }
    } else { Write-Warning 'ZeroTier address is offline. Remote API was not started.' }
    if ($Media -eq 'Video') {
        if (-not (Get-NetTCPConnection -LocalPort 8189 -State Listen -ErrorAction SilentlyContinue)) { StartVisible (Join-Path $strataRoot 'RUN-H3.bat') }
        WaitService 8189 '/queue'
        $url = 'http://127.0.0.1:8189/'
    } else {
        if (-not (Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue)) { StartVisible (Join-Path $strataRoot 'RUN-STATIC-IMAGE.bat') }
        WaitService 8188 '/queue'
        if (-not (Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction SilentlyContinue)) { StartVisible (Join-Path $oldRoot 'RUN-IMAGE-PROXY-PAIR.bat') }
        WaitService 8003 '/status'
        if (-not (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)) { StartVisible 'C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui\START-FAMILY-GUI.bat' }
        $url = 'http://127.0.0.1:5000/'
    }
    $gpuLabel = if ($Media -eq 'Image') { 'GPUs 0+1+2; GPU 1 shares reserved VRAM with static images' } else { 'GPUs 0+2; GPU 1 for H3' }
    Write-Host "Ready: Strata ($context context, $gpuLabel) and $Media." -ForegroundColor Green
    if ($Managed) { Write-Host 'Use Stop All or close the AI control panel to release resources. Native vision runs on CPU.' }
    else { Write-Host 'Close the service windows to release resources. Own model vision encoder runs on CPU.' }
    if (-not $NoBrowser) { Start-Process $url }
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
