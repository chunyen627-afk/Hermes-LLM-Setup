---
name: ps1-encoding-traps
description: PowerShell 5.1 ps1 檔三大踩雷 — UTF-8 必須有 BOM、行尾必須 CRLF、變數名不能叫 $args
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 86205309-645c-4dc6-8b4a-9d930c8e333c
---

寫 .ps1 給 Windows PowerShell 5.1 跑時三件事必做，否則檔案會在 PowerShell parse 階段炸掉。

**1. UTF-8 必須有 BOM**
- PowerShell 5.1 預設用 system codepage（cp950 in zh-TW）讀檔
- 沒 BOM 的 UTF-8 → 中文註解被讀成亂碼 → parser 看到亂碼 → `Unexpected token` 報錯
- 用 Write 工具寫 ps1 預設沒 BOM，**寫完一定要轉**

**2. 行尾必須 CRLF**
- Unix LF 換行在 PowerShell 5.1 大部分時候 OK，但複雜結構（多行 array @()）有時 parse 出錯

**3. 變數名不能叫 `$args`**
- `$args` 是 PowerShell 內建保留字（function 自動 argv）
- 拿來當自訂變數名 → `Unexpected token ')' in expression or statement.`
- 改用 `$launchArgs` 或其他名字

**Why**：踩過至少 3 次。雷 #47、#48。每次都浪費 30 分鐘 debug。

**How to apply**：
寫完 ps1 一定跑這段 PowerShell 轉檔：
```powershell
$utf8bom = New-Object System.Text.UTF8Encoding $true
$content = [System.IO.File]::ReadAllText($path)
$content = $content -replace "(?<!`r)`n", "`r`n"
[System.IO.File]::WriteAllText($path, $content, $utf8bom)
```

驗證：`file <ps1>` 應該顯示 `Unicode text, UTF-8 (with BOM) text, CRLF`

相關：[[mobile-remote-gui]]、[[llm-vram-budget]]
