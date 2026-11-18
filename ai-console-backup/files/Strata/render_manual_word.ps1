param([string]$Docx = 'C:\Users\pjunm\OneDrive\Desktop\H3 圖生影片完整操作手冊.docx')
$ErrorActionPreference = 'Stop'
$outDir = Join-Path $PSScriptRoot 'manual-review'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$pdf = Join-Path $outDir 'manual.pdf'
$word = $null
$doc = $null
try {
    # An independent Word automation instance; never close the user's documents.
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($Docx, $false, $true)
    $doc.Repaginate()
    $pages = $doc.ComputeStatistics(2)
    $doc.ExportAsFixedFormat($pdf, 17)
    Write-Host "Rendered $pages pages to $pdf"
} finally {
    if ($doc) { $doc.Close(0); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) }
    if ($word) { $word.Quit(); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
}
& 'C:\Users\pjunm\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' (Join-Path $PSScriptRoot 'raster_manual.py')
if ($LASTEXITCODE -ne 0) { throw 'PDF page rendering failed' }
