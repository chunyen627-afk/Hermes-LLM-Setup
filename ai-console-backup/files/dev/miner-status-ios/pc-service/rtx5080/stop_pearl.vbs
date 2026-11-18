' Desktop shortcut launcher: runs stop_pearl.ps1 with no console window at all (a hidden PowerShell still flashes).
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining\stop_pearl.ps1""", 0, False
