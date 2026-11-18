param([int]$Context = 32768, [int]$Slots = 2)
$ErrorActionPreference = 'Stop'
$server = 'C:\Users\pjunm\.unsloth\llama.cpp-b10435\llama-server.exe'
$model = 'C:\Users\pjunm\.cache\huggingface\hub\Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf'
foreach ($file in @($server,$model)) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing file: $file" }
}
if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8001 is already occupied. No duplicate model was started.'
}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot '.manual-gpu-mode'), 'Coexist')
$launchArgs = @(
    '--model', $model, '--alias', 'qwen38_mtp',
    '--host', '0.0.0.0', '--port', '8001', '--jinja',
    '--chat-template-file', (Join-Path $PSScriptRoot 'qwen38_chat_template.jinja'),
    '--device', 'CUDA0,CUDA2', '--split-mode', 'layer',
    '--n-gpu-layers', 'auto', '--fit', 'on', '--fit-target', '768',
    '--ctx-size', "$Context", '--parallel', "$Slots", '--context-shift',
    '-fa', 'on', '--cache-type-k', 'q4_0', '--cache-type-v', 'q4_0',
    '--batch-size', '512', '--ubatch-size', '128',
    '--spec-type', 'draft-mtp', '--spec-draft-n-max', '3',
    '--reasoning', 'off', '--reasoning-budget', '0',
    '--temp', '0.7', '--top-p', '0.80', '--top-k', '20', '--min-p', '0.0',
    '--presence-penalty', '1.5', '--n-predict', '8192',
    '--swa-full', '--checkpoint-min-step', '0', '--ctx-checkpoints', '8', '--cache-ram', '2048',
    '--no-mmproj',
    '--no-warmup', '--log-file', (Join-Path $PSScriptRoot '27b-coexist.log')
)
Write-Host "27B coexist test: $Slots slots, $($Context / $Slots) tokens each; GPUs 0 and 2." -ForegroundColor Cyan
Write-Host 'Chat only. Keep the model window open; close it to release memory.'
$process = Start-Process -FilePath $server -ArgumentList $launchArgs -WindowStyle Normal -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot '27b-coexist.pid') -Encoding ASCII
