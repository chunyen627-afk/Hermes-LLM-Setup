# Claude 的長期記憶

這些是 Claude Code 在建置過程中累積的記憶檔，記錄實測數據與踩過的教訓。

**用法**：新機器上設定 Claude Code 時，把這些複製到
`C:\Users\<你>\.claude\projects\<專案>\memory\`，
Claude 就會知道這套環境的所有細節，不用重新摸索。

| 檔案 | 內容 |
|---|---|
| `qwen38-mtp-config.md` | Qwen3.8 的 ctx 上限實測、MTP 設定、chat template 雷、Hermes vs Claude Code 對照 |
| `llama-cpp-tuning-rules.md` | `-sm layer` vs `-sm row`、`-fa` 語法、KV 量化 |
| `llm-vram-budget.md` | 多模型的 VRAM 配置表 |
| `skills-context-diet.md` | 規則拆 Skills 省 83% context、教原則優於補洞 |
| `ps1-encoding-traps.md` | PowerShell/bat 的編碼要求 |
| `local-llm-hermes-rig.md` | 硬體配置概況 |
| `claude-code-kill-trap.md` | 殺行程時的注意事項 |

⚠ 這些是**寫下當時**的事實，引用前先確認檔案/參數是否還存在。
