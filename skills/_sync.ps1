<#
    Skill 備份 / 還原。

    只管「自己寫的」skill —— Hermes 官方內建的重裝就會回來，
    備份它們只會在還原時衝突。

    用法：
        .\_sync.ps1              # 備份：本機 -> 倉庫
        .\_sync.ps1 -Restore     # 還原：倉庫 -> 本機（重灌後用）
        .\_sync.ps1 -List        # 只列出目前備份了哪些
#>

param([switch]$Restore, [switch]$List)

$Local = "$env:LOCALAPPDATA\hermes\skills"
$Repo  = $PSScriptRoot

function Get-Skills {
    param($Root)
    Get-ChildItem -Path $Root -Filter 'SKILL.md' -Recurse -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Directory.FullName.Substring($Root.Length).TrimStart('\') }
}

if ($List) {
    Write-Host ''
    Write-Host '  倉庫裡備份的 skill：' -ForegroundColor Cyan
    Get-Skills $Repo | ForEach-Object { Write-Host "    $_" }
    Write-Host ''
    exit 0
}

if ($Restore) {
    Write-Host ''
    Write-Host '  === 還原：倉庫 -> 本機 ===' -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $Local)) {
        New-Item -ItemType Directory -Path $Local -Force | Out-Null
    }
    $n = 0
    foreach ($s in Get-Skills $Repo) {
        $src = Join-Path $Repo $s
        $dst = Join-Path $Local $s
        $parent = Split-Path $dst -Parent
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -Path $src -Destination $parent -Recurse -Force
        Write-Host "    [還原] $s" -ForegroundColor Green
        $n++
    }
    Write-Host ''
    Write-Host "  完成，還原 $n 個。重開 Hermes 才會載入。" -ForegroundColor Yellow
    Write-Host ''
    exit 0
}

# 預設：備份
Write-Host ''
Write-Host '  === 備份：本機 -> 倉庫 ===' -ForegroundColor Cyan
$n = 0
foreach ($s in Get-Skills $Repo) {
    $src = Join-Path $Local $s
    if (-not (Test-Path -LiteralPath $src)) {
        Write-Host "    [略過] $s（本機已刪除）" -ForegroundColor DarkGray
        continue
    }
    $parent = Split-Path (Join-Path $Repo $s) -Parent
    Copy-Item -Path $src -Destination $parent -Recurse -Force
    Write-Host "    [備份] $s" -ForegroundColor Green
    $n++
}
Write-Host ''
Write-Host "  完成，備份 $n 個。" -ForegroundColor Yellow
Write-Host '  要納入新的 skill：先手動複製一份到這個資料夾，之後就會自動同步。' -ForegroundColor DarkGray
Write-Host ''
