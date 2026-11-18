"""Rasterize the Word-exported manual with the bundled PDFium runtime."""
import json
from pathlib import Path
import pypdfium2 as pdfium

root = Path(__file__).parent / 'manual-review'
document = pdfium.PdfDocument(root / 'manual.pdf')
pages = []
for i in range(len(document)):
    page = document[i]
    bitmap = page.render(scale=150 / 72)
    image = bitmap.to_pil()
    output = root / f'page-{i+1}.png'
    image.save(output)
    pages.append({'page': i+1, 'path': str(output), 'size': list(image.size)})
    image.close()
    bitmap.close()
    page.close()
document.close()
(root / 'pages.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pages': len(pages), 'directory': str(root)}))
