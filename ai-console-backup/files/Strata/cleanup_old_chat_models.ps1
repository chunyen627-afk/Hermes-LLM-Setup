param([ValidateSet('27B','All')][string]$Scope='All', [string]$AuthorizationFile)
$ErrorActionPreference = 'Stop'
$validationArgs = @((Join-Path $PSScriptRoot 'validate_chat_cleanup.py'), '--scope', $Scope)
if ($AuthorizationFile) { $validationArgs += @('--authorization-file', $AuthorizationFile) }
& 'C:\Users\pjunm\Strata\.venv\Scripts\python.exe' @validationArgs
if ($LASTEXITCODE -ne 0) { throw 'Conditional cleanup validation failed; no files removed' }
$planPath = Join-Path $PSScriptRoot 'model-cleanup-plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$selected = @($plan.proposed_files | Where-Object { -not $_.removed -and ($Scope -eq 'All' -or [IO.Path]::GetFileName($_.path).StartsWith('Qwen3.8-27B-')) })
$commands = @(Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine)
foreach ($record in $selected) {
    foreach ($command in $commands) {
        if ($command -and $command.IndexOf($record.path, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw "A running process references $($record.path); no files removed."
        }
    }
    $item = Get-Item -LiteralPath $record.path -Force
    if ($item.Length -ne $record.bytes -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'A model file changed; no files removed' }
    $resolved = $item.FullName
    $allowed = $resolved.StartsWith('C:\Users\pjunm\.cache\huggingface\hub\', [StringComparison]::OrdinalIgnoreCase) -or
               $resolved.StartsWith('C:\Users\pjunm\.lmstudio\models\', [StringComparison]::OrdinalIgnoreCase)
    if (-not $allowed -or $resolved -ne $record.resolved_path) { throw 'Deletion target outside the authorized model roots' }
}
$before = (Get-PSDrive C).Free
$removed = @()
try {
    # Exact regular files only; no recursive deletion and no generated shell code.
    foreach ($record in $selected) {
        Remove-Item -LiteralPath $record.path -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $record.path) { throw "Removal did not complete: $($record.path)" }
        $removed += $record
        $record | Add-Member -NotePropertyName removed -NotePropertyValue $true -Force
        Write-Host "Removed obsolete chat weights: $([IO.Path]::GetFileName($record.path))"
    }
} finally {
    $after = (Get-PSDrive C).Free
    $result = [ordered]@{
        executed=$true; scope=$Scope; completed=($removed.Count -eq $selected.Count);
        explicit_authorization_file=$AuthorizationFile;
        desktop_ui_acceptance='Not asserted by this cleanup; subsequent user authorization covers exactly five files';
        removed_files=$removed; removed_bytes=($removed | Measure-Object bytes -Sum).Sum;
        disk_free_before=$before; disk_free_after=$after; measured_free_increase_GiB=[math]::Round(($after-$before)/1GB,2);
        retained='New Strata model, all vision/projectors, H3/static models, outputs, projects, user documents';
        time=(Get-Date).ToString('o')
    }
    $resultName = if ($Scope -eq '27B') { 'model-cleanup-27b-result.json' } else { 'model-cleanup-result.json' }
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PSScriptRoot $resultName) -Encoding utf8
    $plan.executed = @($plan.proposed_files | Where-Object { -not $_.removed }).Count -eq 0
    $plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $planPath -Encoding utf8
}
Write-Host "Freed $($result.measured_free_increase_GiB) GiB; retained vision and media models."
