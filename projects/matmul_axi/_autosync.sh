#!/bin/bash
# 定期把 matmul_axi 的工作區同步進 git 存證並推上去。
# 目的是防當機 —— 27B 的推理 context 丟了沒關係（HANDOFF/STATUS_NOW 可以接續），
# 但 RTL / tb / 文件丟了就是真的白做。
#
# 只在「檔案真的有變」時才 commit，避免製造空 commit。

set -u
P=/c/Users/pjunm/matmul_axi
D=/c/Users/pjunm/OneDrive/Desktop/Hermes-LLM-Setup
T="$D/projects/matmul_axi"

[ -d "$P" ] || { echo "找不到專案目錄"; exit 1; }

# 同步（只複製存證該有的東西，不含 out/ 這種產物）
mkdir -p "$T/rtl" "$T/tb" "$T/constraints"
cp "$P"/rtl/*.v "$T/rtl/" 2>/dev/null
cp "$P"/tb/*.v  "$T/tb/"  2>/dev/null
cp "$P"/constraints/*.xdc "$T/constraints/" 2>/dev/null
for f in simcheck.json ARCHITECTURE.md HANDOFF.md NOTE_FROM_USER.md \
         SPEC_xspi_bridge.md STATUS_NOW.md README.md \
         CHANGELOG_xspi.md HANDOFF_TO_NEXT.md TODO.md; do
    [ -f "$P/$f" ] && cp "$P/$f" "$T/" 2>/dev/null
done

cd "$D" || exit 1
git add projects/matmul_axi >/dev/null 2>&1

# 沒有變更就安靜結束
git diff --cached --quiet && { echo "$(date '+%H:%M') 沒有變更"; exit 0; }

# commit 訊息帶上當下的驗證狀態，之後回頭看得出每個點的進度
STATUS="work in progress"
if command -v /c/iverilog/bin/iverilog >/dev/null 2>&1; then
    cd "$P" || exit 1
    ERRS=$(/c/iverilog/bin/iverilog -o /tmp/_sync.out -g2012 -s tb_xspi_slave \
           tb/tb_xspi_slave.v rtl/*.v 2>&1 | grep -c error)
    if [ "$ERRS" = "0" ]; then
        # 編譯 0 error 不代表跑得起來：$past 之類 iverilog 不支援的系統函式
        # 是在 vvp 執行階段才失敗的。要分開報，不然 commit 訊息會誤導。
        RUN=$(cd /tmp && timeout 90 /c/iverilog/bin/vvp /tmp/_sync.out 2>&1)
        RUNERR=$(echo "$RUN" | grep -cE "not runnable|Error: System task")
        SIM=$(echo "$RUN" | grep -E "^CHECK data_integrity" | tail -1)
        if [ "$RUNERR" != "0" ]; then
            BAD=$(echo "$RUN" | grep -oE '\$[a-z_]+\(\)' | sort -u | tr '\n' ' ')
            STATUS="RUNTIME FAILURE - sim will not run (${BAD:-see log})"
        else
            STATUS="compiles clean; ${SIM:-no data_integrity line}"
        fi
    else
        STATUS="$ERRS compile errors"
    fi
    cd "$D" || exit 1
fi

git commit -q -m "snapshot $(date '+%Y-%m-%d %H:%M') — $STATUS

Automatic checkpoint of the matmul_axi working tree so a crash costs
only the model's context, never the files.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" \
  && echo "$(date '+%H:%M') committed: $STATUS"

git push -q origin main 2>&1 | tail -2 && echo "$(date '+%H:%M') pushed"
