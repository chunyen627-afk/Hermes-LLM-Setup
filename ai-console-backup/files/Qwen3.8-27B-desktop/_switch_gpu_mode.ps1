param([ValidateSet('Chat','H3','Coexist')][string]$Mode,
      [ValidateSet('Both','Image','Video')][string]$Media = 'Both')
$ErrorActionPreference = 'Stop'

# Verify every process against the installation before stopping it.
function Get-VerifiedListener([int]$Port, [string]$Expected) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($processId in ($listeners.OwningProcess | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
        if (-not $process.CommandLine.Contains($Expected)) {
            throw "Port $Port is used by another program. Close it manually before switching."
        }
        $process
    }
}
function Assert-ComfyIdle([int]$Port) {
    $queue = Invoke-RestMethod "http://127.0.0.1:$Port/queue" -TimeoutSec 5
    if ($queue.queue_running.Count -gt 0 -or $queue.queue_pending.Count -gt 0) {
        throw "ComfyUI :$Port has an active or queued job. Finish or cancel it before switching."
    }
}
function Assert-LLMIdle {
    $slots = Invoke-RestMethod 'http://127.0.0.1:8001/slots' -TimeoutSec 5
    if (@($slots | Where-Object { $_.is_processing }).Count) {
        throw '27B is answering a request. Finish it before switching modes.'
    }
}

$targets = @()
if ($Mode -eq 'H3') {
    $targets += @(Get-VerifiedListener 8001 'Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf')
    $targets += @(Get-VerifiedListener 1234 'Desktop\Qwen3.8-27B\hermes_bridge.py')
    $static = @(Get-VerifiedListener 8188 'Desktop\hermes\ComfyUI\main.py')
    if ($static.Count) { Assert-ComfyIdle 8188 }
    $targets += $static
    $targets += @(Get-VerifiedListener 8003 'Desktop\hermes\comfy_proxy.py')
    if (@($targets | Where-Object { $_.Name -eq 'llama-server.exe' }).Count) {
        $slots = Invoke-RestMethod 'http://127.0.0.1:8001/slots' -TimeoutSec 5
        if (@($slots | Where-Object { $_.is_processing }).Count) {
            throw '27B is answering a request. Finish it before switching to H3.'
        }
    }
} elseif ($Mode -eq 'Coexist') {
    $expectedContext = if ($Media -eq 'Image') { 245760 } else { 32768 }
    $llm = @(Get-VerifiedListener 8001 'Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf')
    foreach ($process in $llm) {
        if ($process.CommandLine -notlike '*--device CUDA0,CUDA2*' -or
            $process.CommandLine -notlike "*--ctx-size $expectedContext*" -or
            $process.CommandLine -notlike '*--no-mmproj*') {
            Assert-LLMIdle
            $targets += $process
        }
    }
    $h3 = @(Get-VerifiedListener 8189 'ComfyUI-H3\main.py')
    if ($h3.Count) { Assert-ComfyIdle 8189 }
    foreach ($process in $h3) {
        if ($Media -eq 'Image' -or $process.CommandLine -notmatch '\s--cuda-device\s+1(?:\s|$)') { $targets += $process }
    }
    $static = @(Get-VerifiedListener 8188 'Desktop\hermes\ComfyUI\main.py')
    if ($static.Count) { Assert-ComfyIdle 8188 }
    foreach ($process in $static) {
        if ($Media -eq 'Video' -or $process.CommandLine -notmatch '\s--cuda-device\s+1(?:\s|$)') { $targets += $process }
    }
    # Validate listeners before any process is stopped.
    Get-VerifiedListener 1234 'Desktop\Qwen3.8-27B\hermes_bridge.py' | Out-Null
    $proxy = @(Get-VerifiedListener 8003 'Desktop\hermes\comfy_proxy.py')
    if ($Media -eq 'Video') { $targets += $proxy }
} else {
    $h3 = @(Get-VerifiedListener 8189 'ComfyUI-H3\main.py')
    $startingH3 = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains('C:\Users\pjunm\ComfyUI-H3\main.py')
    })
    if (-not $h3.Count -and $startingH3.Count) {
        throw 'H3 is still starting. Close its launch window before switching to 27B.'
    }
    if ($h3.Count) { Assert-ComfyIdle 8189 }
    $targets += $h3
    # Restart the smaller coexist model so the normal launcher can restore its long context and vision.
    $llm = @(Get-VerifiedListener 8001 'Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf')
    foreach ($process in $llm) {
        if ($process.CommandLine -like '*--device CUDA0,CUDA2*') {
            Assert-LLMIdle
            $targets += $process
        }
    }
}

# The persistent marker also makes the logon watchdog exit without relaunching.
[IO.File]::WriteAllText((Join-Path $PSScriptRoot '.manual-gpu-mode'), $Mode)
$watchdogFile = Join-Path $PSScriptRoot '_watchdog.py'
$watchdogs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -and $_.CommandLine.Contains($watchdogFile)
})
foreach ($process in $watchdogs) { Stop-Process -Id $process.ProcessId -Force }
foreach ($process in $targets) {
    Write-Host "Stopping $($process.Name) (PID $($process.ProcessId)) to release GPU memory."
    Stop-Process -Id $process.ProcessId -Force
}
Write-Host "GPU mode: $Mode. Automatic model restart is disabled." -ForegroundColor Green
