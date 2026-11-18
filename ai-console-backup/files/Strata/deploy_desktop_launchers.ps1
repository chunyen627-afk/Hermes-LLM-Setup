$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$target = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$controller = Join-Path $PSScriptRoot 'media_control.py'
$arguments = '"' + $controller + '"'
foreach ($file in @($target, $controller)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing: $file" }
}
$ws = New-Object -ComObject WScript.Shell
$obsolete = @()
foreach ($entry in @(@{name='模型＋靜態生圖.lnk';mode='Image'}, @{name='模型＋生影片.lnk';mode='Video'})) {
    $path = Join-Path $desktop $entry.name
    if (Test-Path -LiteralPath $path) {
        $old = $ws.CreateShortcut($path)
        if ($old.TargetPath -ne $target -or $old.Arguments -ne ($arguments + ' --mode ' + $entry.mode)) {
            throw "Unexpected shortcut; preserved: $path"
        }
        $obsolete += $path
    }
}
$new = Join-Path $desktop '本機 AI 控制台.lnk'
if (Test-Path -LiteralPath $new) {
    $existing = $ws.CreateShortcut($new)
    if ($existing.TargetPath -ne $target -or $existing.Arguments -ne $arguments) { throw "Unexpected shortcut: $new" }
}
$shortcut = $ws.CreateShortcut($new)
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = '手動選擇模型＋靜態生圖或生影片；開啟先待機；顯示 TPS；關閉停止服務'
$shortcut.WindowStyle = 1
$shortcut.Save()
$verified = $ws.CreateShortcut($new)
if ($verified.TargetPath -ne $target -or $verified.Arguments -ne $arguments -or $verified.WindowStyle -ne 1) { throw 'Shortcut verification failed' }
$backup = Join-Path $env:USERPROFILE '.cache\hermes-strata-backups\manual-control-shortcuts'
New-Item -ItemType Directory -Path $backup -Force | Out-Null
foreach ($path in $obsolete) {
    Copy-Item -LiteralPath $path -Destination $backup -Force
    Remove-Item -LiteralPath $path
}
Write-Host "Manual control shortcut verified: $new"
