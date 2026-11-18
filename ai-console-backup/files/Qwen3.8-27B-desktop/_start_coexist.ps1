param([ValidateSet('Both','Image','Video')][string]$Media = 'Both', [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$startupMutex = New-Object System.Threading.Mutex($false, 'Local\27B-H3-Coexist-Startup')
try { $locked = $startupMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
if (-not $locked) { Write-Host 'Coexist startup is already running. No duplicate was started.'; $startupMutex.Dispose(); exit 0 }
try {
$context = if ($Media -eq 'Image') { 245760 } else { 32768 }
$perSlot = [int]($context / 2)
& (Join-Path $PSScriptRoot '_switch_gpu_mode.ps1') -Mode Coexist -Media $Media
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Start-Sleep -Seconds 2
function Start-Visible([string]$file) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/d','/c',('"' + $file + '"')) -WindowStyle Normal
}
function Wait-Service([int]$port, [string]$path) {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try { Invoke-RestMethod "http://127.0.0.1:$port$path" -TimeoutSec 2 -ErrorAction Stop | Out-Null; return }
        catch { Start-Sleep -Seconds 1 }
    }
    throw "Service $port did not become ready. Check its visible window."
}
if (-not (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)) {
    & (Join-Path $PSScriptRoot '_start_27b_coexist.ps1') -Context $context -Slots 2
}
Wait-Service 8001 '/v1/models'
$hermesExe = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\hermes.exe'
if (Test-Path -LiteralPath $hermesExe) {
    & $hermesExe config set model.context_length $perSlot 2>&1 | Out-Null
    # Video uses the tested minimal agent tools within its smaller context.
    # Image restores the user's existing tools and compression provider.
    $profiles = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'docs\hermes-media-profiles.json') | ConvertFrom-Json
    $profile = if ($Media -eq 'Image') { $profiles.Image } else { $profiles.Video }
    $toolsetsJson = ConvertTo-Json -InputObject @($profile.toolsets) -Compress
    & $hermesExe config set platform_toolsets.cli $toolsetsJson 2>&1 | Out-Null
    foreach ($field in @('provider', 'model', 'base_url')) {
        $profileValue = [string]$profile.compression.$field
        if ([string]::IsNullOrEmpty($profileValue)) {
            # Windows PowerShell drops empty native arguments; unset restores
            # the default empty URL without corrupting the provider endpoint.
            & $hermesExe config unset "auxiliary.compression.$field" 2>&1 | Out-Null
        } else {
            & $hermesExe config set "auxiliary.compression.$field" $profileValue 2>&1 | Out-Null
        }
        if ($LASTEXITCODE -ne 0) { throw "Hermes profile setting failed: $field" }
    }
}
if (-not (Get-NetTCPConnection -LocalPort 1234 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Visible (Join-Path $PSScriptRoot '_bridge.bat')
}
if ($Media -ne 'Image') {
    if (-not (Get-NetTCPConnection -LocalPort 8189 -State Listen -ErrorAction SilentlyContinue)) {
        Start-Visible (Join-Path $PSScriptRoot 'RUN-H3-PAIR.bat')
    }
    Wait-Service 8189 '/queue'
}
if ($Media -ne 'Video') {
  if (-not (Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue)) {
    # Free idle H3 caches before the image interpreter creates its CUDA context.
    if (Get-NetTCPConnection -LocalPort 8189 -State Listen -ErrorAction SilentlyContinue) {
        Invoke-RestMethod 'http://127.0.0.1:8189/free' -Method Post -ContentType 'application/json' -Body '{"unload_models":true,"free_memory":true}' -TimeoutSec 10 | Out-Null
    }
    Start-Sleep -Seconds 2
    Start-Visible (Join-Path $PSScriptRoot 'RUN-IMAGE-PAIR.bat')
  }
  Wait-Service 8188 '/queue'
  if (-not (Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction SilentlyContinue)) {
      Start-Visible (Join-Path $PSScriptRoot 'RUN-IMAGE-PROXY-PAIR.bat')
  }
  Wait-Service 8003 '/status'
}
if (-not (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Visible 'C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui\START-FAMILY-GUI.bat'
}
Wait-Service 1234 '/v1/models'
Write-Host "Ready: 27B chat (2 x $perSlot context) + $Media. Vision files are preserved, but not loaded." -ForegroundColor Green
Write-Host 'Chat/images: http://127.0.0.1:5000/   H3: http://127.0.0.1:8189/'
if (-not $NoBrowser -and $Media -ne 'Both') {
    $url = if ($Media -eq 'Image') { 'http://127.0.0.1:5000/' } else { 'http://127.0.0.1:8189/' }
    Start-Process $url
}
} finally { $startupMutex.ReleaseMutex(); $startupMutex.Dispose() }
