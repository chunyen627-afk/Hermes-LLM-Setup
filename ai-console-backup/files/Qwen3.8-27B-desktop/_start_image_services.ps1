# Visible image services for the desktop "Start 27B" shortcut.
$ErrorActionPreference = 'Stop'
$services = @(
    @{ Name = 'ComfyUI'; Port = 8188; Launcher = '_start_comfyui.bat' }
    @{ Name = 'Image Proxy'; Port = 8003; Launcher = '_start_image_proxy.bat' }
)
foreach ($service in $services) {
    $listener = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Host ("      [OK] {0} already running (: {1})" -f $service.Name, $service.Port) -ForegroundColor Green
        continue
    }
    $launcher = Join-Path $PSScriptRoot $service.Launcher
    if (-not (Test-Path -LiteralPath $launcher)) {
        throw "Launcher not found: $launcher"
    }
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', ('"' + $launcher + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Normal
    Write-Host ("      [START] {0}: visible window (close it to stop)" -f $service.Name) -ForegroundColor Cyan
}
