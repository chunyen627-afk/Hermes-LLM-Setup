$root = 'C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI'
$python = 'C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe'
if (Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host 'Image service already running. No duplicate was started.'
    exit 0
}
Set-Location -LiteralPath $root
$env:XFORMERS_DISABLED = '1'
$env:XFORMERS_MORE_DETAILS = '0'
Write-Host 'Static images share physical GPU 1 with H3. Generate images and videos one at a time.'
& cmd.exe /d /c "$python -u $root\main.py --port 8188 --cuda-device 1 --lowvram --reserve-vram 1 --use-pytorch-cross-attention 2>&1" |
    Tee-Object -FilePath (Join-Path $PSScriptRoot 'image-coexist.log')
exit $LASTEXITCODE
