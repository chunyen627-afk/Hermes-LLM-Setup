"""
Remote Workstation GUI
Mobile-friendly web UI for controlling Claude Code remotely.
"""
import base64
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
import requests
from flask import Flask, render_template, jsonify, request, send_from_directory, Response, stream_with_context

app = Flask(__name__)
# 局部改圖會把原圖 + 遮罩 base64 一起 POST、單張 SDXL 圖約 2-5MB、base64 後 ~7MB、留 buffer
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB
app.config['MAX_FORM_MEMORY_SIZE'] = 64 * 1024 * 1024  # 64 MB — Werkzeug 對單一 form field 有上限、預設 500KB
app.config['MAX_FORM_PARTS'] = 1000

# 設定
DESKTOP = Path(r"C:\Users\pjunm\OneDrive\Desktop")
TTYD_URL = "/ttyd"  # 同站反代，從手機看是同 host

def get_current_server():
    try:
        r = requests.get("http://127.0.0.1:8888/v1/models", headers={"Authorization": "Bearer sk-unsloth-0524a2ff4bdf7c1d25e337ed0681f274"}, timeout=1)
        if r.ok:
            return "http://127.0.0.1:8888", "sk-unsloth-0524a2ff4bdf7c1d25e337ed0681f274"
    except Exception:
        pass
    return "http://127.0.0.1:8001", "lmstudio"

# Claude Code 絕對路徑（npm 全域安裝的 .cmd 包裝）
CLAUDE_EXE = r"C:\Users\pjunm\AppData\Roaming\npm\claude.cmd"

# 常用 prompt 模板
PROMPT_TEMPLATES = [
    {"id": "ppt", "icon": "📊", "title": "做 PPT", "template": "做一份關於 {topic} 的 PPT 簡報，約 {pages} 頁，每頁都要有 speaker notes，存到當前目錄。"},
    {"id": "pdf", "icon": "📄", "title": "做 PDF", "template": "整理 {topic} 的資料，用 reportlab 寫成 PDF 報告，存到當前目錄。"},
    {"id": "doc", "icon": "📝", "title": "做 Word", "template": "寫一份 {topic} 的 Word 文件（.docx），存到當前目錄。"},
    {"id": "xlsx", "icon": "📈", "title": "做 Excel", "template": "整理 {topic} 的資料成 Excel 試算表（.xlsx），含圖表，存到當前目錄。"},
    {"id": "search", "icon": "🔍", "title": "搜尋整理", "template": "用 ddgs 搜尋 {topic}，整理 10 筆結果摘要成 markdown 檔案存到當前目錄。"},
    {"id": "web", "icon": "🌐", "title": "做網站", "template": "做一個 {topic} 的 Flask 網站，深色主題，所有檔案在當前目錄，包含 README 和 start.bat。"},
    {"id": "code", "icon": "⚙️", "title": "寫程式", "template": "寫一個 {topic} 的 Python 程式，存到當前目錄，含註解和簡單使用範例。"},
    {"id": "game", "icon": "🎮", "title": "做小遊戲", "template": "做一個 {topic} 的 HTML 單檔遊戲，存到當前目錄，可以直接用瀏覽器打開玩。"},
]


def _get_unsloth_llama_port():
    try:
        log_dir = Path.home() / ".unsloth" / "studio" / "logs" / "llama-server"
        files = list(log_dir.glob("llama-*-port-*.log"))
        if files:
            latest = max(files, key=lambda p: p.stat().st_mtime)
            import re
            m = re.search(r"-port-(\d+)\.log", latest.name)
            if m: return int(m.group(1))
    except Exception:
        pass
    return None

def get_server_status():
    """檢查 27B server 狀態

    說明：llama-server 多 slot 下，每個 slot 的 n_ctx 是「該 slot 容量」（總 ÷ slot 數）。
    對單一對話（跑在單一 slot）來說，slot.n_ctx 就是該對話的 context 上限。
    這裡回傳「目前用量最高的 slot」做為主要進度，最貼近使用者直覺。
    """
    base_url, auth = get_current_server()
    headers = {"Authorization": f"Bearer {auth}"}
    try:
        r = requests.get(f"{base_url}/v1/models", headers=headers, timeout=2)
        if not r.ok:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        model = r.json()["data"][0]["id"]
        try:
            target_url = base_url
            if "8888" in base_url:
                port = _get_unsloth_llama_port()
                if port: target_url = f"http://127.0.0.1:{port}"
            slots = requests.get(f"{target_url}/slots", headers=headers, timeout=2).json()
        except Exception:
            slots = [{"n_ctx": 128000, "n_prompt_tokens": 0, "is_processing": True}]
        if not slots:
            return {"ok": False, "error": "no slots"}

        slot_ctx = slots[0].get("n_ctx", 0)  # 單 slot 容量（=該對話上限）
        active = [s for s in slots if s.get("is_processing")]
        # 找用量最高的 slot（多半就是當前活躍對話）
        max_used = max((s.get("n_prompt_tokens", 0) for s in slots), default=0)
        used_total = sum(s.get("n_prompt_tokens", 0) for s in slots)

        return {
            "ok": True,
            "model": model,
            "processing": len(active),
            "total_slots": len(slots),
            "used_ctx": max_used,                    # 「該對話」用量（最大 slot）
            "total_ctx": slot_ctx,                   # 「該對話」上限
            "ctx_pct": round(max_used / slot_ctx * 100, 1) if slot_ctx else 0,
            "used_ctx_all_slots": used_total,        # 所有 slot 加總（供 debug）
            "slot_capacity": slot_ctx,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_folders():
    """列出 Desktop 下的資料夾"""
    folders = []
    for p in DESKTOP.iterdir():
        if p.is_dir() and not p.name.startswith(".") and p.name not in ["hermes"]:
            try:
                file_count = sum(1 for _ in p.rglob("*") if _.is_file())
                folders.append({
                    "name": p.name,
                    "path": str(p),
                    "files": file_count,
                    "has_claude_md": (p / "CLAUDE.md").exists(),
                })
            except PermissionError:
                pass
    return sorted(folders, key=lambda x: x["name"])


def list_files(folder_path):
    """列出某資料夾的檔案（不含 .pyc, __pycache__）"""
    p = Path(folder_path)
    if not p.exists() or not p.is_dir():
        return []
    files = []
    for f in p.iterdir():
        if f.name.startswith(".") or f.name == "__pycache__":
            continue
        stat = f.stat()
        files.append({
            "name": f.name,
            "path": str(f),
            "is_dir": f.is_dir(),
            "size": stat.st_size if f.is_file() else 0,
            "ext": f.suffix.lower() if f.is_file() else "",
            "mtime": stat.st_mtime
        })
    return sorted(files, key=lambda x: (not x["is_dir"], x["name"]))


# ===== Routes =====

@app.route("/")
def index():
    from flask import make_response
    resp = make_response(render_template("index.html", templates=PROMPT_TEMPLATES))
    # 強制不快取，改 GUI 立刻生效
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/sdxl_prompt_generator.html")
def sdxl_prompt_generator():
    """SDXL prompt 勾選式生成器、給 iframe 嵌入用。"""
    from flask import make_response
    resp = make_response(render_template("sdxl_prompt_generator.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/api/status")
def api_status():
    return jsonify(get_server_status())


# ===== GPU 監視（手機可看 GPU 是否在動）=====
_GPU_CACHE = {"data": None, "ts": 0}

@app.route("/api/gpu")
def api_gpu():
    """回傳 3 個 GPU 的利用率 %。前端每 2 秒打一次。"""
    # 0.5 秒內重複呼叫直接回 cache，避免 nvidia-smi 太頻繁
    now = time.time()
    if _GPU_CACHE["data"] and (now - _GPU_CACHE["ts"] < 0.5):
        return jsonify(_GPU_CACHE["data"])
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu",
             "--format=csv,noheader,nounits"],
            text=True, timeout=2, encoding="utf-8", errors="replace"
        )
        gpus = []
        for line in out.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                # 縮短名稱：NVIDIA GeForce RTX 3070 → 3070
                short = parts[1].replace("NVIDIA GeForce ", "").replace("RTX ", "")
                gpus.append({"i": int(parts[0]), "name": short, "util": int(parts[2])})
        result = {"ok": True, "gpus": gpus, "ts": now}
        _GPU_CACHE["data"] = result
        _GPU_CACHE["ts"] = now
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "gpus": []})


@app.route("/api/folders")
def api_folders():
    return jsonify(list_folders())


@app.route("/api/files")
def api_files():
    folder = request.args.get("folder", "")
    return jsonify(list_files(folder))


@app.route("/api/new_project", methods=["POST"])
def api_new_project():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    # 過濾不安全字元
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_ ")
    if not safe_name:
        return jsonify({"ok": False, "error": "Invalid name"}), 400
    new_dir = DESKTOP / safe_name
    if new_dir.exists():
        return jsonify({"ok": False, "error": "資料夾已存在"}), 400
    new_dir.mkdir(parents=True)
    # CLAUDE.md 由 ~/.claude/CLAUDE.md 全域提供，不需要複製到每個專案
    return jsonify({"ok": True, "path": str(new_dir), "name": safe_name})


@app.route("/api/new_subfolder", methods=["POST"])
def api_new_subfolder():
    """在指定 parent 資料夾底下建子資料夾。body: {parent: <絕對路徑>, name: <資料夾名>}"""
    data = request.json or {}
    parent_str = data.get("parent", "").strip()
    name = data.get("name", "").strip()
    if not parent_str or not name:
        return jsonify({"ok": False, "error": "parent + name required"}), 400
    parent = Path(parent_str)
    if not parent.exists() or not parent.is_dir():
        return jsonify({"ok": False, "error": "parent 資料夾不存在"}), 404
    # parent 必須在 Desktop 底下 + 不在保護名單（同 _is_safe_path 規則）
    ok, err = _is_safe_path(parent)
    if not ok:
        return jsonify({"ok": False, "error": err}), 403
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_ ")
    if not safe_name:
        return jsonify({"ok": False, "error": "資料夾名稱無效（只能英數+_-空格）"}), 400
    new_dir = parent / safe_name
    if new_dir.exists():
        return jsonify({"ok": False, "error": "資料夾已存在"}), 400
    try:
        new_dir.mkdir(parents=False)
        return jsonify({"ok": True, "path": str(new_dir), "name": safe_name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ===== 檔案 / 資料夾刪除（安全限制嚴格）=====
# 保護清單：絕對禁刪
PROTECTED_NAMES = {"hermes", "hermes-test", "claude code test", "claude code test2"}
PROTECTED_FILES = {"CLAUDE.md", "AGENTS.md", ".claude"}


def _is_safe_path(p: Path) -> tuple[bool, str]:
    """檢查路徑是否可刪
    回傳 (是否安全, 錯誤訊息)
    """
    try:
        rp = p.resolve()
    except Exception:
        return False, "路徑解析失敗"
    # 必須在 Desktop 底下
    try:
        rel = rp.relative_to(DESKTOP.resolve())
    except ValueError:
        return False, "只能刪 Desktop 底下的東西"
    # 不能就是 Desktop 本身
    if rp == DESKTOP.resolve():
        return False, "不可刪 Desktop 本身"
    # 第一層名稱不可在保護名單
    parts = rel.parts
    if parts and parts[0] in PROTECTED_NAMES:
        return False, f"「{parts[0]}」是受保護資料夾，禁止刪除"
    return True, ""


@app.route("/api/delete_folder", methods=["POST"])
def api_delete_folder():
    """刪除 Desktop 底下某個資料夾（含子檔案）"""
    data = request.json or {}
    folder = data.get("folder", "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "folder required"}), 400
    p = Path(folder)
    if not p.exists():
        return jsonify({"ok": False, "error": "資料夾不存在"}), 404
    if not p.is_dir():
        return jsonify({"ok": False, "error": "不是資料夾"}), 400
    ok, err = _is_safe_path(p)
    if not ok:
        return jsonify({"ok": False, "error": err}), 403
    try:
        shutil.rmtree(p)
        return jsonify({"ok": True, "msg": f"已刪除資料夾 {p.name}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/delete_file", methods=["POST"])
def api_delete_file():
    """刪除單一檔案"""
    data = request.json or {}
    fp = data.get("path", "").strip()
    if not fp:
        return jsonify({"ok": False, "error": "path required"}), 400
    p = Path(fp)
    if not p.exists():
        return jsonify({"ok": False, "error": "檔案不存在"}), 404
    if not p.is_file():
        return jsonify({"ok": False, "error": "不是檔案（資料夾請用 /api/delete_folder）"}), 400
    ok, err = _is_safe_path(p)
    if not ok:
        return jsonify({"ok": False, "error": err}), 403
    # 額外保護：CLAUDE.md / AGENTS.md 等規則檔
    if p.name in PROTECTED_FILES:
        return jsonify({"ok": False, "error": f"「{p.name}」是規則檔，禁止刪除"}), 403
    try:
        p.unlink()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/delete_batch", methods=["POST"])
def api_delete_batch():
    """批次刪除多個檔案與資料夾"""
    data = request.json or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"ok": False, "error": "沒有提供路徑"}), 400
    errors = []
    for fp in paths:
        p = Path(fp)
        if not p.exists(): continue
        try:
            if p.is_dir():
                import shutil
                shutil.rmtree(p)
            else:
                p.unlink()
        except Exception as e:
            errors.append(f"刪除 {p.name} 失敗: {e}")
    if errors:
        return jsonify({"ok": False, "error": " / ".join(errors)}), 500
    return jsonify({"ok": True})


@app.route("/api/download")
def api_download():
    """下載檔案"""
    fp = request.args.get("path", "")
    p = Path(fp)
    if not p.exists() or not p.is_file():
        return "File not found", 404
    # 安全檢查：必須在 Desktop 下
    try:
        p.relative_to(DESKTOP)
    except ValueError:
        return "Access denied", 403
    return send_from_directory(p.parent, p.name, as_attachment=True)


@app.route("/api/preview")
def api_preview():
    """預覽檔案（純文字 / 圖片 / HTML 含相對連結）

    對 .html 特別處理：用 referer 的 root query 自動展開相對路徑
    其他類型直接 send_from_directory。
    """
    fp = request.args.get("path", "")
    p = Path(fp)
    if not p.exists() or not p.is_file():
        return "File not found", 404
    try:
        p.relative_to(DESKTOP)
    except ValueError:
        return "Access denied", 403
    return send_from_directory(p.parent, p.name)


@app.route("/api/site/<root_b64>/<path:relpath>")
def api_site_serve(root_b64, relpath):
    """服務網頁專案的整個目錄（HTML/CSS/JS/.ogg/圖片相對路徑都能載）

    用法：GET /api/site/<root_base64url>/<相對檔名>
    範例：root = C:/Users/pjunm/OneDrive/Desktop/11112 → base64url = QzovVXNlcnMv...
         /api/site/QzovVXNlcnMv.../index.html

    HTML 注入 <base href="/api/site/<root_b64>/">、之後 <script src='script.js'> 自動變成
    /api/site/<root_b64>/script.js ← 同 root_b64、所有相對連結都能服務。
    """
    import base64
    try:
        root = base64.urlsafe_b64decode(root_b64 + '==').decode('utf-8')
    except Exception:
        return "bad root encoding", 400
    root_p = Path(root)
    try:
        root_p.relative_to(DESKTOP)
    except ValueError:
        return "root must be under Desktop", 403
    if not root_p.is_dir():
        return "root not a directory", 404
    # 防 traversal
    safe = relpath.replace("\\", "/")
    if ".." in safe.split("/"):
        return "traversal not allowed", 403
    target = root_p / safe
    if not target.exists() or not target.is_file():
        return f"File not found: {safe}", 404
    # 對 HTML 特別注入 <base href> 讓相對路徑都自動帶 root_b64 前綴
    if target.suffix.lower() in (".html", ".htm"):
        from flask import make_response
        html = target.read_text(encoding="utf-8", errors="replace")
        base_tag = f'<base href="/api/site/{root_b64}/">'
        if "<head>" in html:
            html = html.replace("<head>", "<head>\n  " + base_tag, 1)
        elif "<HEAD>" in html:
            html = html.replace("<HEAD>", "<HEAD>\n  " + base_tag, 1)
        else:
            html = base_tag + "\n" + html
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        return resp
    return send_from_directory(target.parent, target.name)


# ===== 模型切換（4 個模型）=====
# category:
#   general   — 通用（聊天 + 程式都還行）
#   code      — 寫程式專用
#   chat      — 純聊天（越獄/無限制）
MODELS = {
    "qwen27": {
        "label": "Qwen3.8-27B Dense",
        "category": "code",
        "tagline": "🧠 全端開發與對話（主力推薦）— 邏輯與大局觀強，適合從零做網站/遊戲/除錯",
        "ps1": r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_38.ps1",
        "port": 8001, "auth": "lmstudio",
        "alias_match": "qwen38",
    },
    "coder30": {
        "label": "Qwen3-Coder-30B MoE",
        "category": "code",
        "tagline": "💻 特殊硬體與苦力（備用）— 適合 FPGA/STM32 暫存器，或百行無腦重複代碼",
        "ps1": r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_coder30.ps1",
        "port": 8001, "auth": "lmstudio",
        "alias_match": "coder30",
    },
    "uncensored35": {
        "label": "Qwen3.6-35B Uncensored MoE",
        "category": "chat",
        "tagline": "🔓 越獄大腦 — 無道德審查，純聊天問問題",
        "ps1": r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_uncensored35.ps1",
        "port": 8001, "auth": "lmstudio",
        "alias_match": "uncen35",
    },
    "dsv4lite": {
        "label": "DeepSeek V4 Lite (純顧問)",
        "category": "advisor",
        "tagline": "🧠 戰略顧問 — 智商極高但無法自動寫檔，請自行複製 Code",
        "ps1": r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_dsv4lite.ps1",
        "port": 8001, "auth": "lmstudio",
        "alias_match": "dsv4lite",
    },
    "generator_only": {
        # 純前端切換、不真的切 LLM、Vision 區換成 prompt 勾選生成器
        "label": "🎨 生圖專用（勾選式 prompt 生成器）",
        "category": "image",
        "tagline": "✨ 完全手動勾選 SDXL tag、不經過任何 LLM、秒組合秒生圖",
        "ps1": None,
        "port": None, "auth": None,
        "alias_match": "__generator_only__",  # 永遠不會 match、只當前端 ID 用
    },

}


@app.route("/api/model/list")
def api_model_list():
    """列出可切換的模型"""
    # 查當前在跑哪個（從 :8001/v1/models 看 alias）
    current = None
    try:
        base_url, auth = get_current_server()
        r = requests.get(f"{base_url}/v1/models", headers={"Authorization": f"Bearer {auth}"}, timeout=2)
        if r.ok:
            current_id = r.json()["data"][0]["id"]
            for k, v in MODELS.items():
                if v["alias_match"] in current_id:
                    current = k
                    break
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "models": [
            {"id": k, "label": v["label"], "category": v["category"], "tagline": v["tagline"]}
            for k, v in MODELS.items()
        ],
        "current": current,
    })


MODEL_SWITCH_LOCK = threading.Lock()


def _kill_llama_servers():
    """殺所有 llama-server 和 unsloth"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, timeout=10)
        subprocess.run(["taskkill", "/F", "/IM", "unsloth.exe"], capture_output=True, timeout=10)
    except Exception:
        pass
    # 等到 process 真的退出（最多 15 秒）
    import socket
    for _ in range(30):
        time.sleep(0.5)
        # 用 socket 檢查 port 8001 是否還開著
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", 8001))
            s.close()
            continue  # port 還開 → 沒死透
        except Exception:
            return True  # port 關了 → 死透
    return False


def _run_switch_job(jid, target):
    """背景執行模型切換（lock 保護，同時只能 1 個）"""
    if not MODEL_SWITCH_LOCK.acquire(blocking=False):
        _set_job(jid, status="error", error="另一個切換正在進行，請稍候")
        return
    try:
        info = MODELS[target]
        _set_job(jid, status="running", progress=f"準備切換到 {info['label']}...")

        ps1 = info["ps1"]
        if not Path(ps1).exists():
            _set_job(jid, status="error", error=f"啟動腳本不存在: {ps1}")
            return

        # 確認 GGUF 存在
        try:
            ps1_content = Path(ps1).read_text(encoding="utf-8", errors="replace")
            for line in ps1_content.splitlines():
                s = line.strip()
                if s.startswith("$model"):
                    model_path = None
                    if "'" in s:
                        model_path = s.split("'")[1]
                    elif '"' in s:
                        model_path = s.split('"')[1]
                    if model_path and not Path(model_path).exists():
                        _set_job(jid, status="error",
                                 error=f"GGUF 不存在: {model_path}（可能還在下載中）")
                        return
                    break
        except Exception:
            pass

        # Step 1: 殺舊 server，等 port 真的關
        _set_job(jid, progress="🔪 釋放舊模型 VRAM...")
        _kill_llama_servers()
        time.sleep(2)  # 多等一下讓 VRAM 釋放

        # Step 2: 啟動新 ps1
        _set_job(jid, progress="🚀 啟動新模型...")
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as e:
            _set_job(jid, status="error", error=f"啟動失敗: {e}")
            return

        # Step 3: 等 healthcheck（最多 150 秒）
        _set_job(jid, progress="⏳ 等待模型載入（最多 150 秒）...")
        port = info.get("port", 8001)
        auth = info.get("auth", "lmstudio")
        for i in range(150):
            time.sleep(1)
            try:
                r = requests.get(f"http://127.0.0.1:{port}/v1/models", headers={"Authorization": f"Bearer {auth}"}, timeout=2)
                if r.ok:
                    loaded_id = r.json()["data"][0]["id"]
                    if info["alias_match"] in loaded_id:
                        _set_job(jid, status="done", progress="✓ 載入完成",
                                 result={
                                     "target": target,
                                     "label": info["label"],
                                     "loaded": loaded_id,
                                     "elapsed_sec": i + 1,
                                 })
                        return
            except Exception:
                pass
        _set_job(jid, status="error", error="150 秒內模型沒載入完成，請檢查 llama-server log")
    finally:
        MODEL_SWITCH_LOCK.release()


@app.route("/api/model/switch", methods=["POST"])
def api_model_switch():
    """非同步版：立刻回 job_id，背景 lock + 殺舊 + 啟新 + 等載入

    回傳: {ok, job_id, target}
    """
    data = request.json or {}
    target = data.get("model", "").strip()
    if target not in MODELS:
        return jsonify({"ok": False, "error": f"未知模型: {target}"}), 400

    # 檢查是否已在切換
    if MODEL_SWITCH_LOCK.locked():
        return jsonify({"ok": False, "error": "已有切換在進行中"}), 409

    jid = _new_job("switch")
    threading.Thread(target=_run_switch_job, args=(jid, target), daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "target": target})


@app.route("/api/services")
def api_services():
    """檢查各服務狀態"""
    import socket
    def check_port(port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except:
            return False
    return jsonify({
        "llama_27b": check_port(8001),
    })


@app.route("/api/restart/<service>", methods=["POST"])
def api_restart(service):
    """重啟服務 - 先 kill 乾淨再啟動"""
    import subprocess as sp
    import time

    def kill(*names):
        for n in names:
            sp.run(["taskkill", "/F", "/IM", n], capture_output=True)
        time.sleep(1.5)  # 等 port 釋放

    if service == "ttyd":
        # Kill ttyd + 它子 process cmd (避免殘留 cmd window)
        kill("ttyd.exe")
        ttyd = r"C:\Users\pjunm\AppData\Local\Microsoft\WinGet\Packages\tsl0922.ttyd_Microsoft.Winget.Source_8wekyb3d8bbwe\ttyd.exe"
        shell = r"C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\_remote_shell.bat"
        sp.Popen([
            ttyd, "-p", "7681", "-W",
            "-w", str(DESKTOP),
            "-t", "titleFixed=Remote Terminal",
            "-t", "fontSize=20",
            "-t", "fontFamily=Consolas,monospace",
            "-t", "cursorBlink=true",
            "-t", "scrollback=5000",
            "-t", "macOptionIsMeta=true",
            r"C:\Windows\System32\cmd.exe", "/k", shell
        ], creationflags=sp.CREATE_NEW_CONSOLE | sp.CREATE_NEW_PROCESS_GROUP)
        return jsonify({"ok": True, "msg": "ttyd 已 kill 並重啟（請重新整理分頁）"})

    elif service == "filebrowser":
        kill("filebrowser.exe")
        fb = r"C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\filebrowser.exe"
        sp.Popen([fb, "-r", str(DESKTOP), "-a", "0.0.0.0", "-p", "8080", "--noauth"],
                 creationflags=sp.CREATE_NEW_CONSOLE)
        return jsonify({"ok": True, "msg": "filebrowser 已 kill 並重啟"})

    elif service == "llama_27b":
        # Kill 全部 llama-server 和 unsloth (避免 VRAM 殘留)
        kill("llama-server.exe", "unsloth.exe")
        ps_script = r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_38.ps1"
        sp.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script],
                 creationflags=sp.CREATE_NEW_CONSOLE)
        return jsonify({"ok": True, "msg": "Qwen3.8-27B server 已 kill 並重啟（30 秒載入）"})

    elif service == "claude":
        # **只殺 ttyd 開出來的 Claude Code**
        # 重要：絕對不能 taskkill /IM claude.exe（會殺到使用者的 Claude Desktop！）
        # 做法：找 ttyd.exe PID → 列出所有子孫 → 只殺其中的 claude.exe / node
        killed = []
        try:
            # 1. 找 ttyd 的 PID
            ttyd_procs = sp.run(
                ["wmic", "process", "where", "Name='ttyd.exe'", "get", "ProcessId", "/VALUE"],
                capture_output=True, text=True, timeout=5
            )
            ttyd_pids = []
            for line in ttyd_procs.stdout.splitlines():
                if "ProcessId=" in line:
                    pid = line.split("=")[1].strip()
                    if pid.isdigit():
                        ttyd_pids.append(pid)

            # 2. 遞迴找所有 ttyd 的子孫 PID
            def get_children(parent_pid):
                children = []
                r = sp.run(
                    ["wmic", "process", "where", f"ParentProcessId={parent_pid}", "get", "ProcessId,Name", "/VALUE"],
                    capture_output=True, text=True, timeout=5
                )
                lines = r.stdout.splitlines()
                pids = []
                names = []
                for line in lines:
                    if line.startswith("Name="):
                        names.append(line.split("=", 1)[1].strip())
                    elif line.startswith("ProcessId="):
                        pids.append(line.split("=", 1)[1].strip())
                for n, p in zip(names, pids):
                    if p.isdigit():
                        children.append((n.lower(), p))
                        children.extend(get_children(p))
                return children

            # 3. 收集所有 ttyd 子孫，只 kill claude.exe / node.exe
            for ttyd_pid in ttyd_pids:
                for name, pid in get_children(ttyd_pid):
                    if name in ("claude.exe", "node.exe"):
                        sp.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                        killed.append(f"{name}({pid})")
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        time.sleep(1)
        if killed:
            return jsonify({"ok": True, "msg": f"已結束 ttyd 內的 Claude Code: {', '.join(killed)}"})
        else:
            return jsonify({"ok": True, "msg": "沒找到 ttyd 內跑的 Claude Code（可能已結束）"})

    elif service == "all":
        # 全部重啟（除了 Flask 自己）
        kill("filebrowser.exe", "llama-server.exe", "unsloth.exe")
        sp.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                  r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_38.ps1"],
                 creationflags=sp.CREATE_NEW_CONSOLE)
        time.sleep(2)
        sp.Popen([r"C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\filebrowser.exe",
                  "-r", str(DESKTOP), "-a", "0.0.0.0", "-p", "8080", "--noauth"],
                 creationflags=sp.CREATE_NEW_CONSOLE)
        return jsonify({"ok": True, "msg": "全部服務已 kill 並重啟"})

    return jsonify({"ok": False, "error": "Unknown service"}), 400


@app.route("/api/prepare_cmd", methods=["POST"])
def api_prepare_cmd():
    """寫 _pending_cmd.bat → ttyd 下次新連線自動執行
    body: {cwd, mode: new|continue|resume, extra_prompt?: str}
    """
    data = request.json or {}
    cwd = data.get("cwd", "").strip()
    mode = data.get("mode", "new")
    extra_prompt = data.get("extra_prompt", "").strip()

    if not cwd:
        return jsonify({"ok": False, "error": "cwd required"}), 400
    p = Path(cwd)
    if not p.exists():
        return jsonify({"ok": False, "error": "cwd 不存在"}), 400

    # 組裝 Claude Code 執行指令
    base = "claude -p --output-format=stream-json --dangerously-skip-permissions"
    
    # 針對沒有網路能力或語法不相容的模型，關閉所有 MCP 工具 (搜尋等)
    _alias = data.get("model", "qwen27")
    if MODELS.get(_alias, {}).get("no_mcp"):
        base += ' --strict-mcp-config --mcp-config "{}"'

    if mode == "continue":
        claude_cmd = f"{base} -c"
    elif mode == "resume":
        claude_cmd = f"{base} --resume"
    else:
        claude_cmd = base

    # 若有 extra_prompt：用 stdin 餵入（避免命令列引號逃逸地獄）
    pending_bat = Path(r"C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\_pending_cmd.bat")
    lines = ["@echo off", "set ANTHROPIC_API_KEY=%ANTHROPIC_AUTH_TOKEN%", f'cd /d "{cwd}"']

    if extra_prompt:
        # 把 prompt 存到 _prompt.txt 然後用 type 餵 claude（避免特殊字元）
        prompt_file = Path(r"C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\_prompt.txt")
        prompt_file.write_text(extra_prompt, encoding="utf-8")
        # claude 的 -p 是 print 模式（一次性）。互動模式要先啟動再貼
        # 折衷：用 print 模式跑一次（簡單可靠）
        lines.append(f'echo === Auto-running with prompt ===')
        lines.append(f'type "{prompt_file}" | {claude_cmd}')
    else:
        lines.append(f'echo === Auto-starting Claude Code ===')
        lines.append(claude_cmd)

    # 用 CP950 寫（cmd 預設編碼），ASCII 內容不會有事
    pending_bat.write_text("\r\n".join(lines) + "\r\n", encoding="cp950", errors="replace")

    return jsonify({"ok": True,
                    "msg": "已準備指令，跳轉 ttyd 後會自動執行",
                    "cmd": claude_cmd,
                    "cwd": cwd})


@app.route("/api/save_prompt", methods=["POST"])
def api_save_prompt():
    """把 prompt 存成檔案，方便手機快速插入到 TUI"""
    data = request.json
    folder = data.get("folder", "")
    prompt = data.get("prompt", "")
    if not folder or not prompt:
        return jsonify({"ok": False}), 400
    # 寫到該資料夾的 _prompt.txt
    pp = Path(folder) / "_pending_prompt.txt"
    pp.write_text(prompt, encoding="utf-8")
    return jsonify({"ok": True, "file": str(pp)})


# ===== 方案 B：背景 Claude（手機=遙控器，電腦背景跑）=====
# 機制：每個 session 一條訊息 → spawn `claude -p --session-id <sid> "msg"`
#       第一則訊息用 --session-id 建立 session，後續用 --resume <sid> 接續
#       輸出走 stream-json，逐 token 推給 SSE
#       自己 spawn 的 PID 存 dict，砍只砍這顆（絕不 taskkill /IM claude.exe）

# session_id -> {"cwd": str, "history_started": bool}
CHAT_SESSIONS = {}
# session_id -> queue.Queue (stream events to SSE)
CHAT_QUEUES = {}
# session_id -> str (accumulated live streaming text for reconnects)
CHAT_LIVE_BUFFERS = {}
# session_id -> subprocess.Popen (currently-running claude)
CHAT_PROCS = {}
# session_id -> lock (serialize messages in same session)
CHAT_LOCKS = {}
# session_id -> list of events (for replay on F5 / 重開 App)
CHAT_HISTORY = {}
HISTORY_MAX = 1000  # 每 session 上限，超過會丟掉最舊的
# session_id -> {(type, fingerprint): last_ts_ms} 5 秒去重窗口
# 修：stream-json 同段會經 content_block_stop + assistant 兩條路徑各推一次
CHAT_DEDUPE = {}

# ===== 持久化（Flask 重啟也保留對話）=====
SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# 生圖永久存檔位置（單一資料夾、ref URL 保留 vision_sessions/_generated/<hash>.png 格式相容）
AI_GENERATED_DIR = Path(r"Z:\相簿\ai_generated")
# fallback：Z 槽掛了 / 沒掛載 → 用原本路徑
AI_GENERATED_FALLBACK = Path(__file__).parent / "vision_sessions" / "_generated"


def _ai_gen_dir():
    """目前可用的生圖存檔資料夾。Z 槽優先、不能寫就 fallback。"""
    try:
        AI_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        # 試寫一個 sentinel 確認可寫（avoid race / read-only mounted volumes）
        test = AI_GENERATED_DIR / ".write_test"
        test.write_bytes(b"")
        test.unlink()
        return AI_GENERATED_DIR
    except Exception as e:
        print(f"[gen_dir] Z 槽不可寫、fallback 到本機:{e}")
        AI_GENERATED_FALLBACK.mkdir(parents=True, exist_ok=True)
        return AI_GENERATED_FALLBACK


def _resolve_ref_path(rel):
    """ref 內的 rel 路徑 → 實際硬碟 path。
    生圖（_generated/）優先找 Z 槽、找不到 fallback 本機、再找不到回 Z 槽（讓 404 顯示乾淨）。
    其他 ref（inpaint）走 vision_sessions/<sid>/。
    """
    if rel.startswith("vision_sessions/_generated/"):
        filename = rel.split("/", 2)[-1]
        z_path = AI_GENERATED_DIR / filename
        if z_path.exists():
            return z_path
        local_path = AI_GENERATED_FALLBACK / filename
        if local_path.exists():
            return local_path
        return z_path   # 都不存在、回 Z 路徑讓 404 訊息一致
    return SESSIONS_DIR.parent / rel


def _session_file(sid):
    return SESSIONS_DIR / f"{sid}.jsonl"


def _append_to_disk(sid, ev):
    """事件 append 到 .jsonl（每行一個 JSON）"""
    try:
        with open(_session_file(sid), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception as e:
        # 寫硬碟失敗不影響功能（記憶體還在）
        print(f"[persist] write fail {sid}: {e}")


def _load_sessions_from_disk():
    """Flask 啟動時呼叫：掃 sessions/，把所有 .jsonl 載入記憶體"""
    if not SESSIONS_DIR.exists():
        return
    count = 0
    for f in SESSIONS_DIR.glob("*.jsonl"):
        sid = f.stem
        try:
            events = []
            meta = None
            with open(f, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    t = ev.get("type")
                    if t == "_meta":
                        meta = ev
                    elif t == "_meta_update":
                        # 套用 meta 變更到當前 meta
                        if meta:
                            for k, v in ev.items():
                                if k not in ("type",):
                                    meta[k] = v
                    else:
                        events.append(ev)
            if not meta:
                # 沒 meta 就略過（壞檔）
                continue
            CHAT_SESSIONS[sid] = {
                "cwd": meta.get("cwd", str(DESKTOP)),
                "history_started": meta.get("history_started", True),
                # Resume 模式狀態：Flask 重啟後也要復原
                "claude_sid": meta.get("claude_sid"),
                "resume_failed": meta.get("resume_failed", False),
            }
            CHAT_HISTORY[sid] = events[-HISTORY_MAX:]  # 過長截斷
            CHAT_QUEUES[sid] = []
            CHAT_LOCKS[sid] = threading.Lock()
            count += 1
        except Exception as e:
            print(f"[persist] load fail {f.name}: {e}")
    print(f"[persist] loaded {count} sessions from {SESSIONS_DIR}")


def _ensure_session(sid):
    if sid not in CHAT_QUEUES:
        CHAT_QUEUES[sid] = []
    if sid not in CHAT_LOCKS:
        CHAT_LOCKS[sid] = threading.Lock()
    if sid not in CHAT_HISTORY:
        CHAT_HISTORY[sid] = []


def _push_event(sid, ev):
    """推播到 SSE queue + 記錄到 history + 寫入 .jsonl

    雙重去重：
    1. type=text/tool_use/tool_result 在 5 秒內若已推過完全相同內容 → 整個跳過
       根因：stream-json 同一段會經 content_block_stop + assistant 兩條路徑各推一次
    2. type=text 即使第一次也不推 SSE：避免跟 delta 撞造成前端疊字（「現現在在」）
       完整段只進 jsonl + history、供 reload 重播
    """
    _ensure_session(sid)
    ev_copy = dict(ev)
    import time
    now = int(time.time() * 1000)
    if "ts" not in ev_copy:
        ev_copy["ts"] = now

    # 去重 key：(type, 內容指紋) — 5 秒窗口
    et = ev_copy.get("type", "")
    if et in ("text", "tool_use", "tool_result"):
        if et == "text":
            fp = ev_copy.get("text", "")
        elif et == "tool_use":
            fp = (ev_copy.get("tool_use_id", ""), ev_copy.get("name", ""))
        else:
            fp = ev_copy.get("tool_use_id", "")
        dedupe_key = (et, fp)
        with CHAT_LOCKS[sid]:
            dedupe = CHAT_DEDUPE.setdefault(sid, {})
            last_ts = dedupe.get(dedupe_key)
            if last_ts and (now - last_ts) < 5000:
                return  # 5 秒內重複事件 → 直接丟棄
            dedupe[dedupe_key] = now
            # 清理 > 30 秒舊紀錄，避免無限增長
            if len(dedupe) > 100:
                cutoff = now - 30000
                for k in [k for k, v in dedupe.items() if v < cutoff]:
                    del dedupe[k]

    # type=text 即使非重複也不推 SSE（delta 已串流給前端了）
    if et != "text":
        for q in list(CHAT_QUEUES[sid]):
            q.put(ev_copy)
    with CHAT_LOCKS[sid]:
        hist = CHAT_HISTORY[sid]
        hist.append(ev_copy)
        if len(hist) > HISTORY_MAX:
            del hist[50:50 + (len(hist) - HISTORY_MAX)]
    _append_to_disk(sid, ev_copy)


def _write_win_rules_file(sid, rules_text):
    """把 WIN_RULES 寫成檔案，因為 CLI arg 在 Windows cmd 有 8191 char 上限。
    回傳檔案絕對路徑給 --append-system-prompt-file 用。"""
    rules_dir = SESSIONS_DIR / "_win_rules"
    rules_dir.mkdir(exist_ok=True)
    fp = rules_dir / f"{sid}.txt"
    with open(fp, "w", encoding="utf-8") as f:
        f.write(rules_text)
    return str(fp)


def _build_context_prefix(sid, max_chars=16000):
    """從歷史抽出對話脈絡，組成 prefix 字串給下一輪 prompt 用

    策略：保留完整的 user/assistant/tool_use/tool_result 內容（盡量像桌面 Claude Code）。
    超過 max_chars 則只保留尾段（最新對話最重要）。
    """
    hist = CHAT_HISTORY.get(sid, [])
    if not hist:
        return ""
    lines = []
    for ev in hist:
        t = ev.get("type")
        if t == "user":
            txt = ev.get("text", "").strip()
            if txt:
                lines.append(f"[User] {txt}")
        elif t == "text":
            # 保留完整助理輸出（除非單則超過 8K 字才折）
            txt = ev.get("text", "").strip()
            if txt:
                if len(txt) > 8000:
                    txt = txt[:4000] + "\n...[此則過長省略中段]...\n" + txt[-2000:]
                lines.append(f"[Assistant] {txt}")
        elif t == "tool_use":
            name = ev.get("name", "?")
            inp = ev.get("input", {})
            # Write/Edit 的 content / new_string 可能很大（例如完整 HTML）
            # 對 Write 特別處理：保留 file_path + content 完整（除非 > 16K 才折）
            if name == "Write":
                fp = inp.get("file_path", "")
                content = inp.get("content", "")
                if len(content) > 16000:
                    content = content[:8000] + "\n...[檔案內容過長省略中段]...\n" + content[-4000:]
                lines.append(f"[Tool Write] file={fp}\ncontent:\n{content}")
            elif name == "Edit":
                fp = inp.get("file_path", "")
                old = inp.get("old_string", "")
                new = inp.get("new_string", "")
                if len(old) > 4000: old = old[:2000] + "...[省略]..." + old[-1000:]
                if len(new) > 4000: new = new[:2000] + "...[省略]..." + new[-1000:]
                lines.append(f"[Tool Edit] file={fp}\nold:\n{old}\nnew:\n{new}")
            else:
                inp_str = json.dumps(inp, ensure_ascii=False)
                if len(inp_str) > 2000:
                    inp_str = inp_str[:1500] + "..." + inp_str[-300:]
                lines.append(f"[Tool {name}] {inp_str}")
        elif t == "tool_result":
            # tool_result（Read 出來的檔案內容、Bash stdout）也保留，但折更狠
            txt = ev.get("text", "")
            if isinstance(txt, str) and txt.strip():
                if len(txt) > 4000:
                    txt = txt[:2000] + "\n...[結果過長省略]...\n" + txt[-1000:]
                lines.append(f"[Tool Result]\n{txt}")
    full = "\n\n".join(lines)
    # 超長 → 只留尾段（最新對話最重要）
    if len(full) > max_chars:
        full = "...[歷史紀錄截斷]...\n\n" + full[-max_chars:]
    return full


# 只從環境變數讀，不要寫死 —— 這支檔案會同步到公開倉庫。
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_VISION_MODEL = "gemini-2.5-flash"
VISION_PS1 = r"C:\Users\pjunm\OneDrive\Desktop\hermes\_ensure_vision_port_8002.ps1"


def _vision_gemini(image_base64, msg):
    """用 Gemini 看圖。不佔 VRAM、不跟主模型搶 slot，品質也比本地 2B 好。"""
    raw = image_base64
    mime = "image/png"
    if raw.startswith("data:"):
        head, _, body = raw.partition(",")
        mime = head.split(":", 1)[1].split(";", 1)[0] or mime
        raw = body
    prompt = (
        "使用者上傳了一張圖片。請仔細觀察，把圖中所有細節、文字、UI 佈局、"
        "程式碼片段或錯誤訊息轉成極其詳盡的純文字描述。有介面元素請說明位置。\n"
        "使用者的指示為：\n" + msg
    )
    payload = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": mime, "data": raw}},
    ]}]}
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           + GEMINI_VISION_MODEL + ":generateContent?key=" + GEMINI_KEY)
    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _vision_local(sid, image_base64, msg):
    """本機 27B 自己看圖（走 :1234 橋接器）。

    2026-08-29 起主模型掛了 mmproj，自己就有視覺，不用另開 Qwen2-VL-2B，
    也不多吃 VRAM。實測比 Gemini 準（同一張波形圖，Gemini 說白底、還描述了
    不存在的波形；27B 正確答出深色底與哪幾欄有畫出來）。
    """
    _push_event(sid, {"type": "status", "text": "本機看圖中 (27B)..."})
    payload = {
        "model": "qwen38_mtp",
        "messages": [{"role": "user", "content": [
            {"type": "text",
             "text": "請把圖片內容轉成詳盡的純文字描述。使用者的指示：\n" + msg},
            {"type": "image_url", "image_url": {"url": image_base64}},
        ]}],
        "max_tokens": 1500,
        "temperature": 0.2,
    }
    r = requests.post("http://127.0.0.1:1234/v1/chat/completions",
                      json=payload, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_image_with_vlm(sid, image_base64, msg):
    """解析圖片成文字描述。先走本機 27B，失敗才退回 Gemini。

    2026-08-29 把順序倒過來（原本是 Gemini 優先）：主模型掛了 mmproj 之後
    自己就有視覺，不用另開視覺模型也不多吃 VRAM，而且實測比 Gemini 準
    —— 同一張深色底、幾乎全空白的波形圖，Gemini 說是 "white canvas" 還
    描述了不存在的波形，27B 正確答出背景色與哪三欄有畫出來。
    走本機還不燒 Gemini 免費額度（20 次/天/模型）。

    ⚠ 本機看圖需要 llama-server 跑 2 slot。1 slot 時視覺請求得排隊等主任務，
    實測 900 秒都等不到；2 slot 下 21.9 秒完成。
    """
    try:
        _push_event(sid, {"type": "status", "text": "解析圖片中 (本機 27B)..."})
        desc = _vision_local(sid, image_base64, msg)
        _push_event(sid, {"type": "status", "text": "圖片解析完成"})
        return desc
    except Exception as e:
        _push_event(sid, {"type": "status",
                          "text": "本機看圖失敗(" + str(e)[:60] + ")，改用 Gemini"})
        if not GEMINI_KEY:
            _push_event(sid, {"type": "status",
                              "text": "圖片解析失敗（沒設 GEMINI_API_KEY，無備援）"})
            return "圖片解析失敗: " + str(e)
        try:
            desc = _vision_gemini(image_base64, msg)
            _push_event(sid, {"type": "status", "text": "圖片解析完成（Gemini）"})
            return desc
        except Exception as e2:
            _push_event(sid, {"type": "status",
                              "text": "圖片解析失敗: " + str(e2)[:80]})
            return "圖片解析失敗: " + str(e2)

def _run_claude_msg(sid, message, image_base64=None):
    """背景執行緒：跑一次 claude -p，把 stream-json 解析後丟進 queue + history

    模式：用 Claude Code 自己的 --session-id / --resume 管理對話狀態，
    讓本地 LLM 享有跟電腦端 BAT 一樣的體驗（cache 命中、不重塞 prefix）。
    Fallback：若 --resume 失敗，自動切回「重塞 prefix」模式。
    """
    sess = CHAT_SESSIONS.get(sid)
    if not sess:
        _push_event(sid, {"type": "error", "text": "session 不存在"})
        return

    cwd = sess["cwd"]

    if image_base64:
        image_desc = _parse_image_with_vlm(sid, image_base64, message)
        message = f"[系統自動補充：使用者上傳了一張圖片，以下是專門視覺模型對該圖片的詳細解析結果：\n\n{image_desc}\n\n]\n\n使用者的訊息/問題：\n{message}"

    # 先把新 user message 寫入歷史（持久化 + SSE）
    _push_event(sid, {"type": "user", "text": message})

    # === resume 模式 ===
    # 同一 Flask sid 對應同一 claude_internal_sid，讓 Claude Code 自己管 context
    # 第一輪用 --session-id 建 session，後續用 --resume 接續（cache 命中、prompt 小）
    prefix = ""  # 預設值，避免後續 debug log 寫盤時 unbound
    is_first_turn = not sess.get("claude_sid")
    if is_first_turn:
        claude_internal_sid = str(uuid.uuid4())
        sess["claude_sid"] = claude_internal_sid
        sess["resume_failed"] = False  # fallback 標記
        # 持久化到 jsonl：Flask 重啟後也能讀回來 → 真正接續舊對話
        _append_to_disk(sid, {
            "type": "_meta_update",
            "claude_sid": claude_internal_sid,
            "resume_failed": False,
        })
        full_message = message  # 第一輪直接送原訊息（cwd 已注入 system prompt）
    else:
        claude_internal_sid = sess["claude_sid"]
        if sess.get("resume_failed"):
            # 之前 resume 失敗過 → 用 fallback 模式（重塞 prefix）
            prefix = _build_context_prefix(sid)
            if prefix:
                last_user_idx = prefix.rfind(f"[User] {message}")
                if last_user_idx >= 0:
                    prefix = prefix[:last_user_idx].rstrip()
            full_message = (
                "以下是之前對話脈絡:\n\n=== 歷史 ===\n"
                f"{prefix}\n\n=== 新訊息 ===\n{message}"
            ) if prefix else message
        else:
            # 正常 resume：只送新訊息，Claude Code 自己有完整 context
            full_message = message

    # （舊 prefix-only 模式已重構，dead code 已移除）

    # 強制注入 Windows 規則 system prompt（避免 27B 用 ls/cat/&& 等 Linux 指令掛掉）
    WIN_RULES = (
        "# Agent Rules (Claude Code on local LLM via Flask)\n"
        f"Current working directory (cwd): {cwd}\n"
        f"OS: Windows 11. Bash tool runs in Git Bash (POSIX).\n"
        "\n"
        "## 行動原則（最重要）\n"
        "- 使用者要檔案/網頁/遊戲時：直接 Write。不要先 ls、不要先 Bash 探索、不要先做 TodoWrite 計畫。\n"
        "- 模糊指令（「繼續」「如何」「修一下」「再來」「好」「OK」「動工」）= 對上次提到的東西繼續執行。**直接用工具動手，不要再重複分析。**\n"
        "- 任務完成才停。中途不要問「要不要繼續？」「需要我繼續嗎？」— 直接做完。\n"
        "- 寫程式超過 20 行 → 用 Write 寫入檔案，不要把整段 code 貼在對話裡。\n"
        "- 失敗 2 次同樣指令 → 換方法，不要鬼打牆。\n"
        "- 想超過 5 次沒進展 → 主動說「我卡住了，請告訴我具體怎改」。\n"
        "- **不熟的詞或服務先查再做**：使用者提到 NotebookLM、Obsidian、Notion AI、Linear、Supabase 之類具體產品名稱、或不熟的技術詞、或不確定的概念 → **先 `ddgs text -q \"名稱\" -m 5` 查一下、再 `curl -s` 抓 1-2 個結果頁面看內容、再動工**。不要憑記憶亂猜功能。常見的東西（HTML/Flask/SQLite）就不用查、直接寫。\n"
        "- **🎨 任何輸出能用 shared_assets/ 就用、不要憑空亂編 / 寫死生成式**：開工前**強制先跑 `python shared_assets/find_asset.py <關鍵字>`** 查有什麼可用（譬如「做 Mario」→ `find_asset.py mushroom` `find_asset.py player` `find_asset.py coin`、「做 RPG」→ `find_asset.py warrior` `find_asset.py sword`）。helper 會回傳真實檔名路徑、直接抄。**不只是 pygame 遊戲**：\n"
        "    - 做網頁（任何網頁）需要按鈕點擊聲、提示音、過關音樂 → 從 kenney_audio_interface / kenney_audio_jingles 挑\n"
        "    - 做 PPT / 報告需要圖示 → 從 kenney_ui (icon) / kenney_roguelike (插畫風 icon) 挑\n"
        "    - 做 Markdown 文件需要範例圖 → 用 kenney_*/PNG/ 底下的 sprite 當示意圖\n"
        "    - 做 chat app / 工具 UI 需要 avatar → 用 kenney_top_down/PNG/Hitman 1 / Man Blue 等 6 種角色\n"
        "    - 做 Flask / 後端範例需要靜態檔 → 用 shared_assets 當示意素材庫\n"
        "    - 做塔防、賽車、太空射擊等 pygame 遊戲 → 對應 pack 已下載\n"
        "    - 任何遊戲音效 → kenney_audio_* (8 個 pack、651 個 .ogg)\n"
        "  **判斷流程**：(a) 想想這個輸出需要圖 / 音 / 字型嗎？ (b) 需要的話、ls shared_assets/ 看現有的、合就用、不合也至少**先 ddgs 查 Kenney 對應 pack** 再決定要不要抓 / 自己畫。\n"
        "  **絕對禁止**：在能用 shared_assets 的情況下、用 pygame.draw / canvas.arc / Web Audio API OscillatorNode / SVG circle 純算 / lorem ipsum 假資料、來代替真實素材。Kenney 是 CC0 免費商用、沒有不用的理由。\n"
        "\n"
        "## 工作目錄 + 檔案路徑\n"
        f"- 所有檔案輸出預設在 cwd ({cwd}) 底下。\n"
        "- 路徑一律用**正斜線**：C:/Users/pjunm/xxx 或 /c/Users/pjunm/xxx（避免反斜線跳脫炸 JSON）。\n"
        "- 禁用：~/、/tmp/、$TEMP、隨意建在 Desktop。\n"
        "- 檔名用英文/拼音，避免中文檔名亂碼。\n"
        "- 寫檔前若擔心覆蓋 → 用 Read 看一下（但不要為了「探索」濫用）。\n"
        "\n"
        "## 任務大小判斷 + 自己分階段做（核心 — 認真讀！）\n"
        "**重要前提**：當任務太大、一輪做不完時，**自己拆成多個階段，每階段做完一個檔案/功能，就把那個階段的成果送出去**（讓使用者看到進度），然後**自動繼續做下一階段**。\n"
        "**你會做到全部完成才停**，不要做一半問使用者「要繼續嗎？」。\n"
        "\n"
        "### 收到需求時，先自己判斷規模：\n"
        "- **小 (< 200 行 code / 單檔 / 純改一處)**：直接 Write 或 Edit，做完報告。\n"
        "- **中 (200-500 行 / 1-2 檔)**：直接 Write 完整檔案。\n"
        "- **大 (500-1500 行 / 3-5 檔)**：**必須拆檔**、不能塞一個 file_path。先寫 main → 寫支援 → 寫補充。每寫完一個檔可以講一句「✓ 完成 X，繼續做 Y...」，然後**接著動手做下一個檔**，不要停。\n"
        "- **特大 (1500+ 行 / 6+ 檔)**：開頭告訴使用者「這專案分 N 階段做：階段 1 = A / 階段 2 = B / ...」，然後**自己連續跑完所有階段**，不需要等使用者。\n"
        "\n"
        "### ⚠️ 硬限制（違反會崩、不是建議）：\n"
        "**單次 Write 工具有 ~25KB token 上限**（約 600-700 行 code）。**超過會在傳輸途中被截斷、SDK exit 1、之前寫的全部白費**。\n"
        "之前真的崩過的案例：\n"
        "- 27B 試 pygame 地下城單檔塞 800 行 → 重寫到 700 行時崩、SDK exit 1\n"
        "- 30B 試 PPT helper 單檔 → 截斷成 .pptx 副檔名亂存\n"
        "\n"
        "**避免方法**：估算規模時、**遊戲 / 應用 / 全端工程**幾乎一定 > 500 行 → **不要嘗試單檔**：\n"
        "- pygame 遊戲：拆 `main.py` / `player.py` / `map.py` / `enemy.py` / `combat.py` / `ui.py`\n"
        "- Flask 後端：拆 `app.py` / `db.py` / `routes.py` / `models.py`\n"
        "- 網頁 app：拆 `index.html` / `styles.css` / `script.js`（不要 inline 全塞 HTML）\n"
        "- 大 module：拆 `core.py` / `utils.py` / `config.py`\n"
        "\n"
        "**每個 .py / .js 控制在 300-500 行**、超過就再拆。`main.py` 通常最小（< 200 行、只負責 import + main loop）。\n"
        "\n"
        "**反例（不要這樣）**：\n"
        "- ❌ 一個 .html 塞 800 行 inline CSS + inline JS（會崩）\n"
        "- ❌ 一個 .py 塞 6 個 class（700+ 行、Write 會截斷）\n"
        "- ❌ 「我先寫單檔、之後再拆」 → 之後不會有人拆、現在就分\n"
        "\n"
        "### 自己做完整任務的流程（大/特大專案）：\n"
        "1. 開頭一句話講「分 N 階段做：1=X / 2=Y / 3=Z」（告訴使用者後續會發生什麼）\n"
        "2. 立刻開始做階段 1，做完後一句「✓ 階段 1 完成（檔案 X，N 行）」\n"
        "3. **不問、不等、不停**，直接接著做階段 2、3、...\n"
        "4. 全部階段做完再給最終總結\n"
        "5. **唯一可以停下來問使用者的時機**：（a）需要重大設計選擇且兩個選項差很多時，或（b）你連續 3 次都跑進死胡同卡住時\n"
        "\n"
        "### 一個檔的大小判斷（避免單 Write 截斷）：\n"
        "- 單 Write **硬上限 25KB / 600 行**、超過必崩。實務目標 **15KB 以內 / 400 行**。\n"
        "- 預估會超過 → **現在就拆**、不要嘗試「先寫單檔、之後再說」。\n"
        "- 拆檔範例：\n"
        "  - 網頁：index.html（< 200 行純結構）+ styles.css + script.js\n"
        "  - pygame：main.py（< 100 行入口）+ game.py + entities.py + map_gen.py + ui.py\n"
        "  - Flask：app.py（< 200 行）+ models.py + routes.py + helpers.py\n"
        "- 同檔真的要超過 25KB → 先寫骨架（class + def 跟 pass）、再用多次 Edit 補細節（每次 Edit < 5KB）。\n"
        "\n"
        "### 為什麼這樣做（不要懷疑這原則）：\n"
        "- 你跑在 resume mode：每階段做完之間，模型 context 是延續的、不會忘\n"
        "- 一個 Write 塞太大會 token 截斷、會崩\n"
        "- 分階段 + 每階段給簡短進度報告 = 使用者能即時看到你在做什麼\n"
        "- 你**主動跑完整套**才是有用的助理，停下來問才是把工作推給人\n"
        "\n"
        "### 模糊指令的解讀：\n"
        "- 「繼續」「下一步」「再來」= 繼續執行上次未做完的階段（如果上輪因為其他原因斷了）\n"
        "- 「修一下 OO」= 暫停大計畫、回頭修 OO，修完繼續\n"
        "- 「換做 XX」= 放棄當前計畫、改做 XX\n"
        "\n"
        "### 不要做的事：\n"
        "- ❌ 收到大任務就用 TodoWrite 列 10 個項目，然後第 1 項都沒寫完就停\n"
        "- ❌ 一個 Write 塞 80KB code 想做完所有事（會截斷）\n"
        "- ❌ 做一半問「需要我繼續嗎？」「要繼續嗎？」 — 直接繼續\n"
        "- ❌ 列出階段計畫然後**只做第 1 階段就停**等批准 — 要連續跑完\n"
        "- ❌ 「等使用者批准」的反問\n"
        "\n"
        "### 正確示範（特大任務）：\n"
        "```\n"
        "使用者：做個全套部落格系統\n"
        "你：這專案分 4 階段做：\n"
        "1️⃣ FastAPI 骨架 + DB schema + 路由清單\n"
        "2️⃣ 文章 CRUD + 認證\n"
        "3️⃣ 前端首頁 + 文章列表 + 文章頁\n"
        "4️⃣ 後台 admin + 部署設定\n"
        "\n"
        "[Write blog/main.py 280 行]\n"
        "[Write blog/models.py 95 行]\n"
        "[Write blog/db.py 50 行]\n"
        "✓ 階段 1 完成（main.py 280 行 / models.py 95 行 / db.py 50 行）\n"
        "\n"
        "[Write blog/auth.py 120 行]\n"
        "[Write blog/articles.py 180 行]\n"
        "✓ 階段 2 完成\n"
        "\n"
        "... 一路做到階段 4，全部跑完才停。\n"
        "```\n"
        "\n"
        "## Bash 工具語法（重要）\n"
        "- Bash 工具是 **Git Bash（POSIX）**，不是 PowerShell、不是 cmd。\n"
        "- ✅ 可用：ls, dir, cat, type, grep, find, cd, mkdir, rm, mv, cp, python, pip, node, npm, git, curl\n"
        "- ❌ 禁用 PowerShell 語法：$env:VAR、Get-ChildItem、-ErrorAction、-Filter、Select-Object、Where-Object、ConvertTo-Json\n"
        "- ❌ 禁用 && 跟 ||（會 parser error）→ 用 ; 串接\n"
        "- ❌ 禁用 `start` 指令（會跳 GUI window 卡死終端機）\n"
        "- 環境變數：用 $USERPROFILE / $HOME / $TEMP（不是 $env:USERPROFILE）\n"
        "- 範例：✅ `ls /c/Users/pjunm/OneDrive/Desktop` ✅ `python script.py ; echo done` ❌ `ls $env:USERPROFILE`\n"
        "\n"
        "\n"
        "## 🚨 省 context（最高優先，違反會讓任務做不完）\n"
        "ctx 只有 120K，反覆讀整檔會在 30 輪內吃光。實測過一個韌體專案衝到 175K 觸發壓縮，壓縮後模型忘記先前的決定。\n"
        "\n"
        "**讀程式碼用符號查詢，不要 Read 整檔**（已裝 Serena，LSP 符號索引）：\n"
        "- 看檔案有什麼函式 → `get_symbols_overview`，不要 Read 整檔\n"
        "- 看某個函式內容 → `find_symbol` 加 include_body=True\n"
        "- 改動會影響誰 → `find_referencing_symbols`，不要 grep 全專案\n"
        "- 跳到定義 → `find_declaration`\n"
        "- 換掉整個函式 → `replace_symbol_body`，不要 Read 全檔再 Write\n"
        "\n"
        "**只有這三種才 Read 整檔**：檔案 < 150 行、非程式碼（md/json/設定）、結構異常要親眼確認。\n"
        "\n"
        "⚠ 但符號查詢不是萬用：對「這專案大概在幹嘛」這種模糊探索，一直來回查詢反而更慢更貴。\n"
        "  已知道要找什麼（改某函式、追呼叫鏈）→ 符號查詢是主場。\n"
        "  第一次接觸專案 → 先看 README 和目錄結構建立地圖，再深入。\n"
        "\n"
        "**ctx 到 60% 就先寫交接文件**（壓縮是有損的，摘要模型不知道哪些細節重要，你知道）：\n"
        "在專案根目錄寫 `HANDOFF.md`，寫「換一個人接手要知道什麼」：\n"
        "- 現在做到哪、下一步是什麼\n"
        "- 已經確認行不通的做法（**這個最重要**，不寫下來壓縮後會重試一遍）\n"
        "- 關鍵決定與理由（為什麼選 A 不選 B）\n"
        "- 環境細節：路徑、指令、參數、版本號\n"
        "- 卡住的地方和目前的假設\n"
        "寫完繼續做，每有重大進展就更新。壓縮後或開新對話，第一件事讀這份。\n"
        "\n"
        "## 📚 動手前先掃 skill 索引\n"
        "\n"
        "**skill 索引在這份 prompt 的最後面**，那裡列了所有可用的 skill\n"
        "（名稱 + 一行描述）。位置很尾巴，容易整塊略過——但那是你的知識庫。\n"
        "\n"
        "**開始任何實作任務之前，先掃一遍那份清單**：\n"
        "- 名稱或描述沾得上邊的，用 `skill_view` 讀完再動手\n"
        "- 裡面通常有「這台機器踩過的坑」和「可直接複製的指令」，\n"
        "  比你自己重新摸索快很多\n"
        "- 找不到相關的就直接做，不用勉強讀（讀不相關的只是浪費 context）\n"
        "\n"
        "**踩到新的坑就寫回去**：`skill_manage(action=\'patch\')`，\n"
        "當場寫，不要等做完——那時 context 已經壓縮好幾輪，細節記不得了。\n"
        "發現 skill 內容是錯的也一樣要當場改。\n"
        "\n"
        "## ✅ 說「完成」之前先問自己\n"
        "\n"
        "**如果現在有人說「這裡壞了」，我拿得出反駁的證據嗎？**\n"
        "拿不出來就還沒完成。「我認為改對了」不是證據。\n"
        "\n"
        "**統計數字對 ≠ 內容對**。總數、平均這類聚合值會把細節藏起來——\n"
        "「該有的少了」和「不該有的多了」互相抵消，數字看起來剛好。\n"
        "要逐項比對已知的正確值，不要只看一個數字。\n"
        "\n"
        "**畫面類的一定要真的看過那張圖**（用 vision，問具體問題：\n"
        "背景什麼顏色？有幾個元素？有沒有不該存在的東西？），\n"
        "不要只憑像素統計就說「乾淨」。\n"
        "\n"
        "**一次只改一件事**。同時改多處，出問題無法歸因，\n"
        "只能整批退回。動手前先讓「已知能跑」的版本可回復。\n"
        "\n"
        "使用者說「還是壞的」，那就是還是壞的——他看得到你看不到的東西。\n"
        "\n"
        "## 🇹🇼 一律用繁體中文跟使用者對話\n"
        "\n"
        "**每一則回覆都用繁體中文（zh-TW）**，不是只有第一則。\n"
        "進了工具鏈、做到一半、報告結果——全部都是。\n"
        "\n"
        "程式碼、指令、路徑、變數名、錯誤訊息保持英文原樣，\n"
        "但**解釋和說明用中文**。\n"
        "\n"
        "常見的錯誤是開頭用中文回一句，後面就整段跳回英文。\n"
        "使用者每次都要重講一遍「講中文」，很煩。\n"
        "\n"
        "## 💬 使用者中途說話，先回應再繼續\n"
        "\n"
        "任務跑到一半使用者插話，那是**重要回饋不是背景雜訊**——\n"
        "他看得到你看不到的東西（板子畫面、實機行為、真實資料）。\n"
        "\n"
        "**先用一兩句話回應，再繼續工作**：\n"
        "- 我收到什麼（複述一次，確認沒理解錯）\n"
        "- 我打算怎麼處理（或：我需要更多資訊）\n"
        "\n"
        "常見的錯誤是進了工具鏈就一路做到底，中間完全不出聲——\n"
        "使用者不知道你有沒有收到，只好一直重講。\n"
        "\n"
        "使用者回報 bug 時，先想「我手上的資料有沒有答案」：\n"
        "skill、專案筆記、之前的對話記錄（session_search）都查過再動手。\n"
        "\n"
        "## 🔬 先在快的地方驗證，再上慢的地方（任何專案都適用）\n"
        "\n"
        "每個專案都有「快迴圈」和「慢迴圈」。**永遠先把能在快迴圈驗證的驗完**：\n"
        "\n"
        "| 專案類型 | 快迴圈（先做） | 慢迴圈（後做） |\n"
        "|---|---|---|\n"
        "| 韌體 / 嵌入式 | PC 上 gcc 編邏輯層跑（秒） | 建置+燒錄真機（分鐘） |\n"
        "| 手機 App | 單元測試 / PC 模擬（秒） | 模擬器 / 實機（分鐘） |\n"
        "| 前端 | 純函式測試（秒） | 瀏覽器互動（十秒） |\n"
        "| 後端 | 單元測試（秒） | 起服務打 API（十秒） |\n"
        "| 資料處理 | 小樣本（秒） | 全量跑（分鐘～小時） |\n"
        "\n"
        "**理由不是省時間，是縮小範圍**：快迴圈過了還出錯，\n"
        "問題就一定在慢迴圈特有的東西（啟動流程、時序、周邊、真實資料）。\n"
        "跳過快迴圈直接上慢的，出錯時所有可能性都還在，只能瞎猜。\n"
        "\n"
        "畫面類的專案，「看得到自己畫的東西」是關鍵：\n"
        "把 framebuffer / canvas 存成圖檔 -> 自己用 vision 看 -> 自己發現問題。\n"
        "不要改完就燒進去等別人回報。\n"
        "\n"
        "**其他省 context 的規矩**：\n"
        "- 編譯錯誤只留關鍵行：`gcc ... 2>&1 | grep -E \"error|warning\" | head -20`\n"
        "- 測試輸出只看失敗的：`./t | grep -E \"FAIL|error\"`，全過就回報「N passed」\n"
        "- ls 不要遞迴整個專案：`find . -name \"*.c\" | head -30`\n"
        "- 同一個檔案不要讀第二次，已經看過的還在 context 裡\n"
        "- 寫完不要再讀回來確認，Write 成功就是成功了\n"
        "\n"
        "判斷自己有沒有浪費：**「我剛才讀進來的東西，有幾成真的用到？」**\n"
        "讀了 500 行只用到 20 行 → 那次應該用 find_symbol。\n"
        "\n"
        "## 📐 每個專案維護一份 ARCHITECTURE.md（定全貌）\n"
        "\n"
        "符號查詢（Serena）擅長抓細節，但答不出「這專案整體怎麼運作」。\n"
        "每次重新摸索架構要燒掉幾萬 token，而且壓縮後又忘記。\n"
        "解法是讓專案自己帶一份地圖 —— 檔案不會被壓縮，讀一次只要 ~1K token。\n"
        "\n"
        "**接手既有專案時**：先找 `ARCHITECTURE.md`。有就讀它（不要再自己摸索一遍）。\n"
        "沒有就先花五分鐘寫一份，之後每次都省下來。\n"
        "\n"
        "**新專案寫超過 3 個檔時**：主動建一份，不用問。\n"
        "\n"
        "**內容控制在 200-400 字**，只寫這四件事：\n"
        "1. 每個模組負責什麼（一行一個，不要列檔案清單 —— 那 ls 就有了）\n"
        "2. 資料怎麼流（誰呼叫誰、狀態存在哪）\n"
        "3. 關鍵設計決定與**為什麼**（這是最有價值的部分，程式碼看不出來）\n"
        "4. 改動時要注意什麼（哪些地方牽一髮動全身）\n"
        "\n"
        "**不要寫**：完整 API 文件、每個函式的說明、能從程式碼直接看出來的東西。\n"
        "那些用 `get_symbols_overview` 查就好，寫進來只是讓地圖失去意義。\n"
        "\n"
        "**改架構時要同步更新它** —— 過期的地圖比沒有地圖更糟，會誤導。\n"
        "\n"
        "範例（韌體專案）：\n"
        "```\n"
        "## 模組\n"
        "core/calc.c    純計算邏輯，不碰硬體，可在 PC 上跑測試\n"
        "core/ui.c      畫面繪製，只依賴 gfx.h 的抽象介面\n"
        "app_src/       硬體整合，LTDC/DMA2D 都在這層\n"
        "\n"
        "## 資料流\n"
        "觸控 → input_update() → calc_* → ui_draw() → framebuffer → LTDC\n"
        "\n"
        "## 關鍵決定\n"
        "- core/ 刻意不含硬體相依，才能用 QEMU 測邏輯\n"
        "- DMA2D 不用 HAL 的 PollForTransfer（TC 旗標有雷，見 notes）\n"
        "\n"
        "## 改動注意\n"
        "- 改 gfx.h 介面 → core/ 和 app_src/ 兩邊都要同步\n"
        "- 字型是產生出來的，改字要重跑 tools/genfont.py\n"
        "```\n"
        "## Python 環境（已配置好，直接用）\n"
        "- 系統 Python 3.11 已在 PATH。直接 `python script.py` / `pip install xxx` 即可。\n"
        "- **已裝套件**：numpy, pandas, matplotlib, requests, lxml, transformers, torch(CUDA), pygame,\n"
        "  **文件套件**：python-pptx, python-docx, reportlab, openpyxl, xlsxwriter, Pillow, markdown,\n"
        "  **搜尋**：ddgs, duckduckgo-search\n"
        "- ⚠ weasyprint 不能用（缺 GTK）→ 生 PDF 用 reportlab\n"
        "- 不確定 Python 在哪：`python -c \"import sys; print(sys.executable)\"`\n"
        "\n"
        "## ⚠️ 寫完 Python 必須跑一次驗證（不可省略）\n"
        "**寫完任何 .py / 多檔專案後、不可以說「完成」就停**。必須跑一次、看 output、確認沒 error。\n"
        "\n"
        "**為什麼**：模型寫 code 時常犯小錯（變數沒初始化、屬性名稱錯、import 路徑錯、API 用錯）、自己看 code 看不出來、跑一次就現形。實戰雷例：\n"
        "- pygame 用 `pygame.Surface((w,h))` 當主視窗 → `Display mode not set`（應該用 `pygame.display.set_mode((w,h))`）\n"
        "- class `__init__` 收參數但沒存 `self.x = x` → 後面方法用會 `name not defined`\n"
        "- 重構到一半留下舊變數 → 屬性沒初始化 attribute error\n"
        "- import 拼錯 module 名稱 → ImportError\n"
        "\n"
        "**標準驗證步驟**（按專案類型挑）：\n"
        "1. **單檔腳本**：`python script.py` 看完整 output、沒 traceback。\n"
        "2. **module / lib**：`python -c \"from yourlib import X; print(X)\"` 試 import + 用一次。\n"
        "3. **pygame / GUI**：headless 跑 `SDL_VIDEODRIVER=dummy python main.py`（或設環境變數）— 5 秒內沒 traceback 就算通過、`Display mode not set` 之類例外就修。\n"
        "4. **Flask / API**：`python -c \"import app\"` 看 import 不爆、再 curl 個 endpoint。\n"
        "5. **多檔專案**：`python -c \"import main_module\"` 看所有 import chain 通；再跑主入口。\n"
        "\n"
        "**遇到 error 怎處理**：\n"
        "- 看 traceback、回去用 Edit 修真正出錯的行。\n"
        "- 修完**再跑一次**驗證。\n"
        "- 跑通才能說「完成」。\n"
        "\n"
        "**不要這樣**：\n"
        "- ❌ Write 完直接說「跑 `python main.py` 就能玩」← 你沒跑、不知道能不能玩\n"
        "- ❌ 看 code 看起來對就停 ← 看不出細節錯\n"
        "- ❌ 跑了有 error 視而不見、繼續說完成\n"
        "\n"
        "## Office 文件處理（這是 Python 任務，不要拒絕！）\n"
        "- 讀/寫 .pptx → python-pptx（已裝）\n"
        "- 讀/寫 .docx → python-docx（已裝）\n"
        "- 讀/寫 .xlsx → openpyxl（已裝）\n"
        "- 寫 PDF → reportlab（已裝）\n"
        "- 流程：用 Write 工具寫 Python 腳本 → 用 Bash 工具跑 python script.py → 確認檔案存在\n"
        "- **禁止說「我需要 python-pptx 套件」**—— 已經裝了，直接 import 就好。\n"
        "- PPT 每頁加 Speaker Notes（用 slide.notes_slide.notes_text_frame.text = '...'）。\n"
        "\n"
        "## 🎮 做遊戲的素材選擇（pygame **跟網頁遊戲都適用**）\n"
        "\n"
        "**重要**：shared_assets/ 底下的 sprite（.png）跟音效（.ogg）都是標準格式、**pygame、HTML5 Canvas、Web Audio API 都能用**。不要因為「這是網頁不是 pygame」就跳過素材、用 Web Audio API 純生成嗶嗶聲。\n"
        "\n"
        "### 網頁遊戲怎用 shared_assets\n"
        "**規則**：把要用的檔案 `cp` 到 cwd 同層、用相對路徑載入（避開 file:// 跨目錄 CORS 雷）。\n"
        "```bash\n"
        "# 1. 在 cwd 建 assets/\n"
        "mkdir -p cwd/assets/sprites cwd/assets/audio\n"
        "# 2. 複製要用的素材（不要整 pack 複製、只挑需要的）\n"
        "cp shared_assets/kenney_audio_jingles/Audio/8-Bit\\ jingles/jingles_NES05.ogg cwd/assets/audio/levelup.ogg\n"
        "cp shared_assets/kenney_audio_impact/Audio/impactPlate_medium_000.ogg cwd/assets/audio/hit.ogg\n"
        "cp shared_assets/kenney_audio_interface/Audio/click_001.ogg cwd/assets/audio/click.ogg\n"
        "```\n"
        "```html\n"
        "<!-- 在 HTML 用相對路徑 -->\n"
        "<audio id='hit' src='assets/audio/hit.ogg' preload='auto'></audio>\n"
        "<script>\n"
        "  // JS 觸發：\n"
        "  document.getElementById('hit').play();\n"
        "  // 或動態建：\n"
        "  const sfx = { levelup: new Audio('assets/audio/levelup.ogg'), hit: new Audio('assets/audio/hit.ogg') };\n"
        "  sfx.hit.currentTime = 0; sfx.hit.play();\n"
        "</script>\n"
        "```\n"
        "**README 提示使用者**：`雙擊 index.html 直接玩、或跑 python -m http.server 8000 後開 http://localhost:8000`（後者音效更穩）。\n"
        "\n"
        "### 網頁遊戲絕對禁區\n"
        "- ❌ **用 Web Audio API + OscillatorNode 純算 sin 波生成嗶嗶聲** — Kenney 有 651 個真實 .ogg、用真的就好\n"
        "- ❌ **網頁 Canvas 畫角色用 ctx.arc 圓圈** —（除非抽象遊戲、譬如俄羅斯方塊的方塊本來就是色塊）\n"
        "- ❌ **「網頁不適用素材庫規則」** — 規則同時涵蓋 pygame 跟網頁、檔案格式都通用\n"
        "\n"
        "### 抽象遊戲特例\n"
        "**俄羅斯方塊、貪食蛇、反彈球、2048**：方塊 / 蛇身 / 圓球這些**本來就是純色塊**、不需要 sprite。但**音效還是要用 .ogg、不要 sin 波**：\n"
        "- 落子 / 旋轉 / 消行 → kenney_audio_interface/ 或 kenney_audio_impact/\n"
        "- 升等 / Game Over → kenney_audio_jingles/8-Bit jingles/\n"
        "- BGM → 用 jingles 8-Bit 風格的長一點段、或 ddgs 查 'opengameart 8bit chiptune CC0' 抓\n"
        "\n"
        "## （以下 pygame 專屬路徑）做 pygame 遊戲的素材選擇\n"
        "**先想題目類型、再決定要不要 / 用哪套素材**。不要每次都套同一套、會出現「賽車變戰士」的荒謬畫面。\n"
        "\n"
        "### ⚠️ 收到 pygame 遊戲題目、第一件事必須做：\n"
        "**跑 `python shared_assets/check_assets.py <genre>`** 確認對應素材在不在 + 拿到建議路徑。\n"
        "範例：\n"
        "- 「做地下城遊戲」→ `python shared_assets/check_assets.py rpg`\n"
        "- 「做 Mario」→ `python shared_assets/check_assets.py mario`\n"
        "- 「做太空射擊」→ `python shared_assets/check_assets.py space`\n"
        "- 「做塔防」→ `python shared_assets/check_assets.py td`\n"
        "- 「做 2048 / 俄羅斯方塊 / 貪食蛇」→ `python shared_assets/check_assets.py 2048`\n"
        "- 完整 genre 清單：跑 `python shared_assets/check_assets.py` 看 docstring\n"
        "\n"
        "**這個 helper 會印出**：對應 sprite pack 路徑 + spritesheet 檔名 + 音效 pack 路徑 + 範例檔。直接抄、不用猜。\n"
        "\n"
        "### 鐵則（會被打回重做）\n"
        "1. **角色 / 敵人 / 物品 / 地圖 tile 一律用 image.load** — **禁止用 pygame.draw.circle / rect 取代 sprite**（抽象益智類除外、譬如 2048）。\n"
        "2. **音效一律用 pygame.mixer.Sound 載 .ogg** — **禁止用 numpy / math.sin 生成式音效**（除非真的找不到、且要明確說「fallback」）。\n"
        "3. **載入路徑必須先 ls 確認檔名存在** — 不要憑想像猜路徑。\n"
        "4. **`shared_assets/<pack>/` 沒對應、且不是抽象遊戲** → 照「抓新素材的標準流程」5 步抓 Kenney pack 下來、解壓、再寫 code。**不準跳過寫 pygame.draw**。\n"
        "\n"
        "**反例（這次 RPG 踩過的雷、不要再犯）**：\n"
        "- ❌ 設了 `SPRITESHEET_PATH = '...kenney_roguelike/Spritesheet/...'` 但 ui.py 整檔沒一個 `image.load`、全用 `pygame.draw.circle` 畫角色 → 規則沒生效、畫面簡陋\n"
        "- ❌ 寫 `sounds.py` 319 行用 `math.sin` 算 sin 波生成嗶嗶聲 → Kenney 有 651 個 .ogg 沒用、生成式音效沒人想聽\n"
        "- ❌ 「之後再加 sprite」「先有畫面再優化」的藉口 → 之後不會有人加、現在就用真素材\n"
        "\n"
        "\n"
        "### 決策表\n"
        "| 題目類型 | 策略 |\n"
        "|---|---|\n"
        "| 抽象益智（俄羅斯方塊、貪食蛇、反彈球、2048、踩地雷）| pygame.draw 純色塊就好、不需 spritesheet |\n"
        "| 像素風奇幻（roguelike、Pokemon 風 RPG、dungeon crawler）| ★ 用 **shared_assets/kenney_roguelike/**（已下載） |\n"
        "| 太空射擊（Space Invaders、shoot'em up） | 抓 Kenney 'Space Shooter Redux' pack |\n"
        "| 賽車 / 競速 | 抓 Kenney 'Racing Pack' 或 'Car Kit' |\n"
        "| 平台跳（Mario 風）| 抓 Kenney 'Platformer Pack' |\n"
        "| 塔防 | 抓 Kenney 'Tower Defense Top-down' |\n"
        "| 卡牌 / 棋盤 | 抓 Kenney 'Boardgame Pack' |\n"
        "| 其他 | ddgs 查 `kenney <類型> pack` 或 `opengameart <類型> CC0` |\n"
        "\n"
        "### 已下載的素材（不要重抓）\n"
        "```\n"
        "C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets/\n"
        "├── kenney_roguelike/        奇幻 RPG、16×16 spritesheet（Spritesheet/roguelikeSheet_transparent.png）\n"
        "├── kenney_platformer/       Mario 風（Base pack/Tiles, /Enemies, /Items, /Player、獨立 PNG）\n"
        "├── kenney_racing/           賽車（PNG/Cars, /Tiles、獨立 PNG + Spritesheets）\n"
        "├── kenney_space/            太空射擊（PNG/Enemies, /Lasers, Backgrounds、獨立 PNG）\n"
        "├── kenney_top_down/         俯視角射擊（PNG/Hitman, /Soldier, /Robot, 6 種角色）\n"
        "├── kenney_tower_defense/    塔防（PNG/Default size, /Retina、獨立 PNG + Tilesheet）\n"
        "└── kenney_ui/               通用 UI（5 主題色 Blue/Green/Grey/Red、含 Font）\n"
        "```\n"
        "**規則**：對應類型 → 直接用 shared_assets/<pack>/、**不要重抓**。`ls shared_assets/<pack>/` 確認結構、再寫 image.load。\n"
        "**獨立 PNG vs Spritesheet**：roguelike 是 spritesheet（要切 tile）、其他大多是獨立 PNG（直接 `pygame.image.load('xxx.png')` 不用切）。\n"
        "\n"
        "### 🔊 音效素材庫（同 shared_assets/ 底下、.ogg 格式）\n"
        "```\n"
        "shared_assets/\n"
        "├── kenney_audio_rpg/          52 個 — 物件互動類：書本翻頁/裝備/腳步/翻找\n"
        "├── kenney_audio_impact/      130 個 — 撞擊類：拳擊/木頭/金屬/玻璃碎（適用任何打擊或碰撞）\n"
        "├── kenney_audio_interface/   100 個 — UI 反饋：點擊/切換/確認/取消\n"
        "├── kenney_audio_ui/           52 個 — UI 補充音\n"
        "├── kenney_audio_jingles/      86 個 — 短曲：適用任何「關鍵時刻」（升等/過關/失敗/成就），含 8-Bit 子目錄（NES 風）\n"
        "├── kenney_audio_scifi/        73 個 — 科幻類：雷射/引擎/警報/機械（不限太空）\n"
        "├── kenney_audio_digital/      63 個 — 電子音：適用抽象遊戲、提示音、按鍵反饋\n"
        "└── kenney_audio_voiceover/    95 個 — 角色喊話：「Yes!」「No!」「Attack!」等\n"
        "```\n"
        "\n"
        "**🎵 真 BGM（背景音樂、長段 seamless loop、CC0）**：\n"
        "```\n"
        "shared_assets/bgm/juhani_chiptunes/\n"
        "├── stage1.ogg       1.6MB  關卡 1 BGM（輕快冒險）\n"
        "├── stage2.ogg       2.4MB  關卡 2 BGM（緊張一點）\n"
        "├── boss.ogg         2.9MB  boss 戰\n"
        "└── menu.ogg         0.9MB  選單 / 標題畫面\n"
        "```\n"
        "**重要**：kenney_audio_jingles/ 是 **1-3 秒短曲**、適合「升等 / 過關 / Game Over」這種「事件音」。**不要拿來當 BGM 用、會聽起來很卡（每秒重播）**。需要長 BGM 用 `bgm/juhani_chiptunes/` 底下的。\n"
        "\n"
        "**選 BGM 規則**：\n"
        "- 一般遊戲關卡 → stage1.ogg 或 stage2.ogg\n"
        "- boss 戰 → boss.ogg\n"
        "- 主選單 / 開始畫面 → menu.ogg\n"
        "- 抽象益智（俄羅斯方塊 / 貪食蛇 / 2048）→ stage1.ogg 輕快、不擾人\n"
        "\n"
        "**音效情境對應（適用所有遊戲類型）**：\n"
        "| 情境 | pack |\n"
        "|---|---|\n"
        "| 撞擊類（戰鬥、子彈打中、踩到敵人、賽車碰撞、塔防怪物死亡） | kenney_audio_impact/ |\n"
        "| UI 反饋（按鈕、選單、確認、取消、tab 切換） | kenney_audio_interface/ + kenney_audio_ui/ |\n"
        "| 關鍵時刻短曲（升等、過關、Game Over、達成成就、boss 出場） | kenney_audio_jingles/（含 8-Bit NES 子目錄） |\n"
        "| 撿物 / 翻頁 / 開門 / 鎖開關（roguelike、解謎、平台跳）| kenney_audio_rpg/（含書本翻頁、裝備聲）|\n"
        "| 科幻類（雷射、引擎、警報、太空、機械、雷達） | kenney_audio_scifi/ |\n"
        "| 抽象 / 電子（puzzle、節奏遊戲、按鍵反饋、提示音） | kenney_audio_digital/ |\n"
        "| 角色喊話（攻擊、受傷、嘲諷、勝利的「Yes!」「No!」）| kenney_audio_voiceover/ |\n"
        "\n"
        "**對應原則**：先從情境（撞擊 / UI / 喊話 / 短曲）對表挑 pack、再 ls 該 pack 看具體檔名挑檔。不同遊戲類型可能用同一個 pack（譬如賽車碰撞跟 RPG 戰鬥砍都用 impact）。\n"
        "\n"
        "**標準載入 + 播放**（抄）：\n"
        "```python\n"
        "import pygame, os\n"
        "BASE = 'C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets'\n"
        "\n"
        "# 1. mixer 必須在 pygame.init() 前先 pre_init（穩定）\n"
        "pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)\n"
        "pygame.init()\n"
        "pygame.mixer.init()\n"
        "\n"
        "# 2. load 一次、放 cache、用時就 play()\n"
        "SFX = {\n"
        "    'hit':   pygame.mixer.Sound(f'{BASE}/kenney_audio_impact/Audio/impactPlate_medium_000.ogg'),\n"
        "    'pick':  pygame.mixer.Sound(f'{BASE}/kenney_audio_rpg/Audio/bookOpen.ogg'),\n"
        "    'click': pygame.mixer.Sound(f'{BASE}/kenney_audio_interface/Audio/click_001.ogg'),\n"
        "    'levelup': pygame.mixer.Sound(f'{BASE}/kenney_audio_jingles/Audio/8-Bit jingles/jingles_NES05.ogg'),\n"
        "    'gameover': pygame.mixer.Sound(f'{BASE}/kenney_audio_jingles/Audio/8-Bit jingles/jingles_NES10.ogg'),\n"
        "}\n"
        "\n"
        "# 3. 用時：\n"
        "SFX['hit'].play()\n"
        "SFX['hit'].set_volume(0.5)   # 0.0-1.0\n"
        "```\n"
        "\n"
        "**重要**：\n"
        "- 載入路徑前**先 ls** 確認檔名（譬如 `ls shared_assets/kenney_audio_impact/Audio/ | head -10`）— 不要憑想像猜檔名。\n"
        "- 抓不到對應音效就 fallback 用通用的（譬如所有 impact 都用同一個 hit.ogg）、別硬找。\n"
        "- 音效集中放 `audio.py` 或 `sounds.py` 一個檔管理、別散在各檔。\n"
        "- 致謝跟圖一起寫：`Sprites & sounds by Kenney (kenney.nl) CC0`。\n"
        "\n"
        "### 抓新素材的標準流程（5 步）\n"
        "```bash\n"
        "# 1. 查 pack 頁面 URL\n"
        "ddgs text -q 'kenney space shooter pack' -m 3\n"
        "# 找到譬如 https://kenney.nl/assets/space-shooter-redux\n"
        "\n"
        "# 2. 抓頁面 HTML（kenney 不擋 curl）\n"
        "curl -s 'https://kenney.nl/assets/space-shooter-redux' > /tmp/page.html\n"
        "\n"
        "# 3. 抽出 zip 下載 URL（在『Continue without donating』連結裡）\n"
        "grep -oE 'https://kenney.nl/media[^\"]+\\.zip' /tmp/page.html | head -1\n"
        "\n"
        "# 4. 下載 + 解壓到 shared_assets/<pack_name>/\n"
        "DEST=C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets/kenney_space\n"
        "mkdir -p $DEST\n"
        "curl -L -o /tmp/pack.zip '<step 3 抽出的 URL>'\n"
        "unzip -q /tmp/pack.zip -d $DEST\n"
        "ls $DEST                                     # 看結構\n"
        "ls $DEST/Spritesheet/ 2>/dev/null            # 找 spritesheet（多數 Kenney pack 有）\n"
        "```\n"
        "\n"
        "### 載入 + 切 tile 範例（適用所有 Kenney pixel pack）\n"
        "```python\n"
        "import pygame\n"
        "SHEET_PATH = 'C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets/<pack_name>/Spritesheet/<sheet>.png'\n"
        "TILE = 16        # roguelike 是 16，有些 pack 是 32 / 64、看 spritesheetInfo.txt\n"
        "MARGIN = 1       # tile 間距、看 spritesheetInfo.txt\n"
        "\n"
        "def load_sheet():\n"
        "    return pygame.image.load(SHEET_PATH).convert_alpha()\n"
        "\n"
        "def get_tile(sheet, col, row, scale=2):\n"
        "    x = col * (TILE + MARGIN)\n"
        "    y = row * (TILE + MARGIN)\n"
        "    sub = sheet.subsurface((x, y, TILE, TILE))\n"
        "    return pygame.transform.scale(sub, (TILE * scale, TILE * scale)) if scale != 1 else sub\n"
        "```\n"
        "\n"
        "### 重要細節\n"
        "- **載入順序**：`pygame.init()` → `pygame.display.set_mode(...)` → `load_sheet()`。沒有視窗 → `.convert_alpha()` 會炸。\n"
        "- **找對 (col, row)**：看 pack 附的 `Preview.png` / `Sample*.png` 數位置。不確定就先寫幾個常用 tile、跑起來看、再對齊。\n"
        "- **抓不到 / 風格不搭**：fallback 用 pygame.draw 畫色塊 + 形狀（戰士 = 方塊、子彈 = 圓、敵人 = 三角）。**寧可簡潔也別硬套不搭素材**（戰士 sprite 當賽車 = 荒謬）。\n"
        "- **致謝**：README 或遊戲 about 寫 `Sprites by Kenney (kenney.nl) CC0`（CC0 不強制、但有 sense）。\n"
        "\n"
        "## PPT 設計風格（做 .pptx 強制用 pptx_style helper，不要自己挑配色字型）\n"
        "\n"
        "### ⚠️ 做 PPT 的標準流程（兩步驟、缺一不可）\n"
        "**做 PPT 一定是兩個 tool call、不是一個。錯了就重來。**\n"
        "1. **Write 工具**寫一個 **.py 檔**（譬如 `make_pptx.py`），檔案內容是 Python 程式碼（`from pptx_style import Deck ...`）\n"
        "2. **Bash 工具**跑 `python make_pptx.py`，這時才會在 cwd 產出真正的 `.pptx` 檔\n"
        "\n"
        "**絕對禁區（會被罵！）**：\n"
        "- ❌ 把 Python 程式碼用 Write 工具寫成 `.pptx` 副檔名（譬如 `Write(file_path='x.pptx', content='from pptx_style ...')`）\n"
        "  → 這只會產生一個副檔名是 .pptx 的純文字檔，PowerPoint 打不開（會跳『需修復』）\n"
        "- ❌ 寫完 .py 就停、忘了 `python xxx.py` → PPT 根本沒生出來\n"
        "- ❌ 用 Write 工具直接生 .pptx：.pptx 是 ZIP 二進位檔，**只能由 python-pptx / pptx_style 產生**，不能手寫\n"
        "\n"
        "**檢查清單（每次做完 PPT 對一遍）**：\n"
        "- [ ] 我有沒有 Write 一個 `.py` 檔？（不是 .pptx！）\n"
        "- [ ] 我有沒有 Bash 跑 `python xxx.py`？\n"
        "- [ ] 我有沒有 `ls` 確認 .pptx 真的產生了？\n"
        "- [ ] 我有沒有跑驗證 `python -c \"from pptx import Presentation; ...\"` 確認頁數 + notes？\n"
        "\n"
        "### Helper 介紹\n"
        "**已裝好 helper module**：`pptx_style.py` 在系統 site-packages、直接 import 就能用。\n"
        "**所有 .pptx 任務都要 `from pptx_style import Deck` 用 helper、不要自己 `from pptx import Presentation` 亂寫。**\n"
        "Helper 已內建：16:9 尺寸、品牌色（深藍 #1A1A2E + 青綠 #00D4AA）、字級、6 種頁面類型、自動頁碼、Microsoft JhengHei 字型。\n"
        "\n"
        "Helper API（記住這 7 個方法就夠 — 注意：以下是 `.py` 檔的內容、不是要你 Write 成 .pptx！）：\n"
        "```python\n"
        "# 檔名：make_pptx.py（一定是 .py！）\n"
        "from pptx_style import Deck\n"
        "deck = Deck('output.pptx')   # ← 這個 'output.pptx' 是「python 跑完後產出的檔名」，不是「Write 的目標檔名」\n"
        "deck.cover(title, subtitle, version='v3.9 (2026-06-11)', kpis=[(num,desc), ...4個])  # 封面\n"
        "deck.notes('這頁講...')   # 強制每頁加 Speaker Notes！\n"
        "deck.section(title, subtitle)  # 章節分隔頁\n"
        "deck.kpi_grid(title, subtitle, kpis=[(num,desc), ...4-6個], footer_note='...')  # 大 KPI 數字頁\n"
        "deck.table_page(title, subtitle, headers=[...], rows=[[...], [...]], star='★ ...')  # 表格頁\n"
        "deck.flow_4(title, subtitle, boxes=['① ...', '② ...', '③ ...', '④ ...'], star='...')  # 4 階段流程圖\n"
        "deck.before_after(title, subtitle, before_title, before_text, after_title, after_text, star='...')\n"
        "deck.bullet_page(title, subtitle, bullets=[...], star='...')  # bullet list 頁\n"
        "deck.save()\n"
        "```\n"
        "\n"
        "硬性規則（不照做產出會被打回）：\n"
        "1. **每頁都要 `deck.notes('...')`** ← 不可省略。寫像跟同事講話：「這頁講...」「重點在...」「老闆關心...」\n"
        "2. **不要每頁都用 bullet_page**。穿插 kpi_grid / table_page / flow_4 / before_after 增加變化。\n"
        "3. **第一頁必用 cover、最後一頁可用 bullet_page 結尾**。\n"
        "4. **頁數：週報 10-12 頁、簡報 15-20 頁**。不要為了多而多。\n"
        "5. **內容用具體數字**（「3881 條」「77.6%」「47 分鐘」），不要「很多」「很快」這種空話。\n"
        "6. **用 emoji 分類**（🔌🍳🍪 商品類 / 🔴🟠🟡 警示 / ✓❌ Before/After）。\n"
        "7. **★ Callout 寫業務語意**：「老闆要的」「真實電商邏輯」「展場路人」，不是「具有現代化設計」這種行話。\n"
        "8. **禁用 PowerPoint 預設模板**（Title and Content / 預設黑字 bullet list）— 用 Deck.bullet_page 就好。\n"
        "9. **寫完跑一次驗證**：`python -c \"from pptx import Presentation; p=Presentation('x.pptx'); print(len(p.slides), 'slides,', sum(1 for s in p.slides if s.notes_slide.notes_text_frame.text.strip()), 'with notes')\"` — notes 數要等於 slides 數。\n"
        "\n"
        "完整範例（給你抄到 `.py` 檔裡！記住是 .py 不是 .pptx）：\n"
        "```python\n"
        "# 1. 用 Write 工具寫成 make_pptx.py（副檔名 .py！）\n"
        "from pptx_style import Deck\n"
        "deck = Deck('週報.pptx')   # ← 跑完後產出的 .pptx 名稱\n"
        "deck.cover('本週週報 — 倉管功能升級',\n"
        "           subtitle='連帶推薦 + 補貨預測 + 保存期限警示',\n"
        "           version='v3.9 (2026-06-11)',\n"
        "           kpis=[('60','SKU'),('13','情境'),('186','連帶對'),('零','需重訓')])\n"
        "deck.notes('本週把倉管升級成會主動發現連帶 + 預測補貨的智能助理。')\n"
        "\n"
        "deck.kpi_grid('訓練成果', subtitle='第 6 個 function、重訓一輪',\n"
        "              kpis=[('3881','訓練條'),('47分','耗時'),('77.6%','Q8 raw'),('84.2%','E2E')],\n"
        "              footer_note='查庫存 18/18 滿分、新功能 9/11')\n"
        "deck.notes('Q8 raw 77.6%、加校正層 E2E 84.2%。重訓加連帶 function。')\n"
        "\n"
        "deck.flow_4('購物籃分析流程', subtitle='跟 Amazon「買了也買」同套',\n"
        "            boxes=['① 800+ 張訂單','② 數共現','③ 算同捆率','④ 顯示給訪客'],\n"
        "            star='點同捆率數字 → 彈出算式說明（24÷61=39%）')\n"
        "deck.notes('這頁給老闆看「不是唬人」。')\n"
        "deck.save()\n"
        "```\n"
        "\n"
        "**做完 PPT 的正確 tool call 順序示範**（每次都要長這樣）：\n"
        "```\n"
        "1. Write(file_path='make_pptx.py', content='from pptx_style import Deck\\ndeck = Deck(\"週報.pptx\")\\n...')\n"
        "2. Bash('python make_pptx.py')\n"
        "3. Bash('ls *.pptx')   # 確認 .pptx 真的產生了\n"
        "4. Bash('python -c \"from pptx import Presentation; p=Presentation(\\'週報.pptx\\'); print(len(p.slides))\"')\n"
        "```\n"
        "**錯誤示範（絕對不要這樣做）**：\n"
        "```\n"
        "❌ Write(file_path='週報.pptx', content='from pptx_style import Deck\\n...')\n"
        "   ↑ 把 python source 寫成 .pptx 副檔名 → PowerPoint 打不開、會跳『需修復』\n"
        "```\n"
        "\n"
        "## 網頁搜尋 / 抓網頁\n"
        "- 搜尋：用 Bash 跑 `ddgs text -q \"關鍵字\" -m 10`（不要用 WebSearch、會卡死）\n"
        "- 抓網頁內容：先 ddgs 拿到 URL，再 `curl -s \"https://完整網址\"`\n"
        "- ❌ 絕對不要 curl 搜尋詞（會把搜尋詞當主機名 DNS 卡死）：`curl -s \"台北電腦展\"` ← 錯\n"
        "\n"
        "## 圖片生成\n"
        "- 使用者要圖：output **這個確切的 markdown**（瀏覽器會自動載入圖）：\n"
        "  ![image](/api/vision/generate?prompt=ENGLISH_PROMPT_URLENCODED)\n"
        "- 不要用 Bash 工具去呼叫圖片 API、不要 fetch、不要 curl。只要 output markdown 字串就好。\n"
        "\n"
        "## 回覆風格\n"
        "- 預設用**繁體中文（zh-TW）**回覆；程式碼、指令、路徑保英文。\n"
        "- 簡潔。不要每次重複「我來..」「我會...」開場白。\n"
        "- 任務完成 → 1-2 句報告即可（檔名、大小、有沒有跑過）。\n"
        "- ❌ 不要列「我做了什麼」清單；❌ 不要重複分析剛剛說過的話。\n"
        "\n"
        "## 錯誤處理\n"
        "- 看到 `Exit code 1` 但有實際輸出 → 視為成功（很多 CLI 把 warning 寫 stderr）。\n"
        "- timeout → 換更精準的查詢重試一次，不要直接跳過。\n"
        "- Bash 失敗 → 看真實錯誤訊息，換語法重試（不要連續同樣失敗指令）。\n"
        "- Write/Edit 失敗 → 先用 Read 看實際內容對不對，再決定怎改。\n"
        "- 不要拒絕、不要說「無法完成」——找方法自己解。\n"
        "\n"
        "## 任務心態\n"
        "- 真實資料優先：禁止用 example.com、fake、lorem ipsum 假資料。要 demo 就跑真的 API。\n"
        "- 任務沒完成不要說完成 — 一定要確認檔案實際存在、code 實際跑通。\n"
        "- 長任務中途不要停：除非真的需要使用者決定，否則自己跑到尾。\n"
        "\n"
        "## 模糊指令的處理（重要 — 避免反覆詢問）\n"
        "- 使用者打模糊指令時（「做個網站」「做個遊戲」「弄個工具」），**不要用 AskUserQuestion 反問**「你要 X 還是 Y？」\n"
        "- 直接挑最合理的一種解讀動工。如果做錯使用者會說「我要的是另一種」，那時再改。\n"
        "- 範例：「做個小精靈遊戲」→ **直接做 Pac-Man**（不要問是 Pac-Man 還是 Pokemon）。\n"
        "- 範例：「做個記帳本」→ 直接做 single-file HTML + localStorage（不要問要不要 backend）。\n"
        "- 例外：只有「**真的會造成不可逆損失**」才能問。例如「刪掉我的 X 資料夾」。\n"
        "\n"
        "## 設計品味（讓作品像「真的有人在做」）\n"
        "做網頁/UI 時，**不用問細節，自己挑下面的合理組合**：\n"
        "- **預設配色**：深色背景（#0f172a 或 #18181b）+ 亮色 accent（綠 #10b981 / 藍 #3b82f6 / 粉 #ec4899 任一）。亮色版用淺灰底 + 一個飽和 accent。\n"
        "- **預設字體**：system-ui 或 'Inter', sans-serif；標題粗體大字、內文 16px+。\n"
        "- **手機優先**：viewport meta + 觸控按鈕 ≥44×44px + max-width 不超過 480px 的單欄佈局。\n"
        "- **動畫**：hover transition 0.2s、scroll-triggered fade-in、按鈕點擊微縮放（不要過度，2-3 處足夠）。\n"
        "- **互動細節**：localStorage 存設定、loading state、空狀態提示、error 提示、操作後 toast。\n"
        "- **內容**：自己編合理範例資料（3-5 筆）填進去，**不要留空畫面或寫 TODO**。\n"
        "- **無障礙**：alt、aria-label、tab focus 樣式（基本就好）。\n"
        "做遊戲時：分數系統、Game Over 畫面、Restart 按鈕、音效（用 Web Audio）、難度遞增 — 都自動加上不用問。\n"
        "\n"
        "## 修 bug 的流程（避免改 15 次都改不對）\n"
        "1. **先看完整檔**：用 Read 把整個檔讀完（不要只看一段）。\n"
        "2. **找根因，不是表象**：「卡牆」可能是初始位置在牆內、可能是碰撞邏輯錯、可能是迷宮陣列錯。**全部都檢查**。\n"
        "3. **一次修對**：用 Edit 改根因處。如果根因影響多處 → 用 Write 整檔重寫，不要 5 個小 Edit。\n"
        "4. **修完就停**：不要為了「保險」再多改幾處你不確定的地方。\n"
        "5. **同樣 bug 改 2 次還沒修好** → 整個重寫該函式，不要繼續 Edit。\n"
        "\n"
        "## 主動推斷 + 加值（像有經驗的工程師）\n"
        "使用者沒講但合理的需求，自己加上去：\n"
        "- 做網站 → 自動加 favicon、SEO meta、Open Graph、404 處理。\n"
        "- 做表單 → 自動加 validation、disabled 狀態、success 回饋。\n"
        "- 做遊戲 → 自動加暫停、音量、最高分記錄。\n"
        "- 做資料處理 → 自動處理空值、編碼、CSV BOM。\n"
        "**判斷標準**：使用者會不會說「咦怎麼沒這個」？會 → 自動加。\n"
        "\n"
        "## Python 腳本品味（寫 .py 時遵守）\n"
        "- 一定有 `if __name__ == '__main__':` 入口。\n"
        "- 有參數 → 用 argparse（不要 sys.argv 硬拆）。\n"
        "- print log 前綴清楚：`[OK]`、`[WARN]`、`[ERROR]`、`[INFO]`，方便 grep。\n"
        "- 開檔一律 `encoding='utf-8'`，CSV 加 BOM (`utf-8-sig`) 給 Excel。\n"
        "- 路徑用 pathlib.Path（不要 os.path 字串拼接）。\n"
        "- 主流程 try/except 攔錯印清楚訊息 + 非零 exit code。\n"
        "- 短工具：30 行內無需 class。長工具：分函式、加 docstring。\n"
        "- 不要寫廢註解（`# 設定變數 x = 5` 之類）。\n"
        "\n"
        "## API / Backend 品味（寫 Flask/FastAPI 時遵守）\n"
        "- 每個 endpoint 有 docstring 描述 method + body + 回傳格式。\n"
        "- 永遠回 JSON：`{\"ok\": true/false, \"data\": ..., \"error\": \"...\"}`，HTTP status code 對齊（200/400/404/500）。\n"
        "- 輸入驗證：用 pydantic 或手動 isinstance + 範圍檢查。\n"
        "- 路徑、檔案參數 → 用 pathlib 處理 + 防 traversal（禁 `..`）。\n"
        "- 長任務（>3 秒）→ 用 threading + job_id 回 polling URL，不要 block HTTP。\n"
        "- 不要在 endpoint 裡寫業務邏輯一大坨 → 抽到 service 函式。\n"
        "- 錯誤訊息對使用者有意義（不要回 `Internal Error`）。\n"
        "\n"
        "## README / 文件結構（產 .md 時遵守）\n"
        "- 結構：標題 → 一句話介紹 → 安裝 → 用法 (含範例) → 設定 → 常見問題 → 授權。\n"
        "- 用法區塊一定有可複製貼上的範例（不要只描述）。\n"
        "- 截圖區用 `![alt](path)` 占位（即使圖還不存在也預留）。\n"
        "- 用 `## 章節` 不要 `## 我要說的章節`（廢字）。\n"
        "- 結尾不要 \"made with love\"、\"hope you enjoy\" 之類客套。\n"
        "\n"
        "## 行為禁區（這些做了會被使用者罵）\n"
        "- ❌ TodoWrite 開頭計畫 5 步驟然後沒一個做完 → 直接做最後一步\n"
        "- ❌ 「我會幫你建立一個...」開場白 → 直接動手就好\n"
        "- ❌ 「我做了什麼」結尾清單 → 1-2 句報告就停\n"
        "- ❌ 「您可以打開瀏覽器試試」之類沒幫助的廢話\n"
        "- ❌ 用 example.com、lorem ipsum、{{placeholder}} 假資料\n"
        "- ❌ 寫完 code 不確認檔案存在就說「完成」\n"
        "- ❌ 加註解寫廢話如「// 這是 for 迴圈」「// 設定變數」\n"
        "- ❌ 連續 3 次同樣 tool call 失敗還重試 → 換方法\n"
    )

    # 用 stdin 餵訊息（避免中文/標點被 .cmd 包裝層引號切壞）
    # 限制工具清單：本地 30B/27B 處理太多 tool schema 會炸 → 只給核心工具
    # 之前 portfolio_v4 跑得起來是因為當時沒有這麼多 MCP / Task* / Cron* 工具
    ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
    common = [CLAUDE_EXE,
              "-p",
              "--dangerously-skip-permissions",
              # WIN_RULES 太大會撞 Windows cmd 8191 char 上限 → 寫成檔案傳 path
              "--append-system-prompt-file", _write_win_rules_file(sid, WIN_RULES),
              "--output-format", "stream-json",
              "--include-partial-messages",
              "--allowedTools", ",".join(ALLOWED_TOOLS),
              # 砍掉 MCP 跟內建 Task/Cron/Workflow/Skill 等本地 LLM 處理不來的工具
              # ↓ 註：開啟這行會讓 Claude Code 啟動報錯，靠 --allowedTools 限制就好
              # "--strict-mcp-config", "--mcp-config", "{}",
              "--verbose"]

    # 過去的 no_mcp 標記已不再需要（上面強制 strict + 空 config 直接砍乾淨）

    # === Resume 模式 ===
    # 第一輪 --session-id 建立、後續 --resume 接續（讓 Claude Code 自己管 context）
    if is_first_turn:
        args = common + ["--session-id", claude_internal_sid]
        print(f"[claude session] FIRST turn sid={claude_internal_sid}")
    else:
        args = common + ["--resume", claude_internal_sid]
        print(f"[claude session] RESUME sid={claude_internal_sid}")
    # message（含歷史 prefix）改透過 stdin 傳

    _push_event(sid, {"type": "status", "text": f"🚀 跑中 (cwd={cwd})"})

    # delta 只 push 到 queue（即時看），不存歷史（量太大）
    # 但會累積成 _delta_buffer，content_block_stop 時組成完整 text 事件存歷史
    _delta_buffer = {"text": ""}
    # thinking block 狀態：避免每個 token 都包 <thinking></thinking> 變成 N 組標籤
    # 改成 enter 推開標籤、exit 推關標籤、中間 token 裸推
    _thinking_state = {"active": False}

    def push_live(ev):
        """只送 SSE，不存歷史（給高頻 delta 用）"""
        _ensure_session(sid)
        ev_copy = dict(ev)
        for q in list(CHAT_QUEUES[sid]):
            q.put(ev_copy)

    def flush_delta_buffer():
        """把累積的 delta 變成完整 text 事件存歷史（一段完整回應結束時呼叫）"""
        if _delta_buffer["text"].strip():
            _push_event(sid, {"type": "text", "text": _delta_buffer["text"]})
            _delta_buffer["text"] = ""

    try:
        # 注入完整 env：包含 npm bin 路徑 + ANTHROPIC 變數
        env = os.environ.copy()
        npm_bin = r"C:\Users\pjunm\AppData\Roaming\npm"
        py311 = r"C:\Users\pjunm\AppData\Local\Programs\Python\Python311"
        env["PATH"] = f"{npm_bin};{py311};{py311}\\Scripts;{env.get('PATH', '')}"
        base_url, auth = get_current_server()
        # 透過我們自己的 proxy 攔截 SSE，破解 Node.js 的 8KB buffering trap
        # 注意：SDK 會強制將 URL 加上 /v1/messages，所以我們直接架設在 5000 port
        env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:5000"
        # 透過 API Key 偷渡 sid 與 auth 讓 proxy 知道要傳給哪個網頁
        env["ANTHROPIC_AUTH_TOKEN"] = f"{sid}|{auth}"
        env["ANTHROPIC_API_KEY"] = f"{sid}|{auth}"
        env["ANTHROPIC_TIMEOUT"] = "3600000"
        env["ANTHROPIC_MAX_RETRIES"] = "2"
        # 動態抓 llama-server 當前 alias（修補：GUI 切模型後 Claude Code 也跟著切）
        try:
            _r = requests.get(f"{base_url}/v1/models", headers={"Authorization": f"Bearer {auth}"}, timeout=3)
            _alias = _r.json().get("data", [{}])[0].get("id", "qwen38_mtp")
        except Exception:
            _alias = "qwen38_mtp"
        env["ANTHROPIC_MODEL"] = _alias
        env["ANTHROPIC_SMALL_FAST_MODEL"] = _alias
        print(f"[claude env] ANTHROPIC_MODEL={_alias}")

        proc = subprocess.Popen(
            args, cwd=cwd, env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        CHAT_PROCS[sid] = proc
        # 用 stdin 餵 full_message（含歷史 prefix，中文安全）
        # 必須用 Thread，否則當 full_message 大於 OS pipe buffer (64KB) 時會發生死結
        def _feed_stdin():
            try:
                proc.stdin.write(full_message)
                proc.stdin.close()
            except Exception as e:
                print(f"[stdin write fail] {e}")
        threading.Thread(target=_feed_stdin, daemon=True).start()

        # 修補：開 debug log 把 raw stdout/stderr 全寫盤（事後 debug 用）
        debug_log_path = SESSIONS_DIR / f"{sid}.debug.log"
        debug_fp = open(debug_log_path, "a", encoding="utf-8")
        debug_fp.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} model={_alias} msg={message[:80]!r} =====\n")
        debug_fp.write(f"[full_message bytes={len(full_message)}] prefix_used={'yes' if prefix else 'no'}\n")
        # 修補：追蹤是否收過任何 assistant 內容
        got_assistant_output = False

        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            # DEBUG: 印每行到 Flask cmd 也落盤
            try:
                print(f"[claude stdout] {line[:300]}")
            except UnicodeEncodeError:
                print(f"[claude stdout] {line[:300].encode('cp950', errors='replace').decode('cp950')}")
            debug_fp.write(f"OUT: {line}\n"); debug_fp.flush()
            try:
                obj = json.loads(line)
            except Exception:
                # 兼容 claude -p 吐純文字的情況
                txt = line + "\n"
                got_assistant_output = True
                push_live({"type": "delta", "text": txt})
                _delta_buffer["text"] += txt
                continue

            t = obj.get("type")
            if t == "assistant":
                # 收到完整 assistant 訊息時：丟掉 delta 累積（避免重複），改用這份完整版
                _delta_buffer["text"] = ""
                if sid in CHAT_LIVE_BUFFERS:
                    CHAT_LIVE_BUFFERS[sid] = ""
                got_assistant_output = True
                content = obj.get("message", {}).get("content", [])
                for c in content:
                    ctype = c.get("type")
                    if ctype == "text":
                        _push_event(sid, {"type": "text", "text": c.get("text", "")})
                    elif ctype == "thinking":
                        # Claude 的思考過程（如果有 reasoning model）
                        _push_event(sid, {"type": "thinking",
                                          "text": c.get("thinking", "")})
                    elif ctype == "tool_use":
                        _push_event(sid, {"type": "tool_use",
                                          "tool_use_id": c.get("id", ""),
                                          "name": c.get("name", ""),
                                          "input": c.get("input", {})})
                        push_live({"type": "tool_use",
                                   "tool_use_id": c.get("id", ""),
                                   "name": c.get("name", ""),
                                   "input": c.get("input", {})})
            elif t == "user":
                # Claude SDK 把工具結果包成 user role message → 我們抽出來顯示
                content = obj.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "tool_result":
                            result_content = c.get("content", "")
                            # tool_result.content 可能是 str 或 [{"type":"text","text":...}]
                            if isinstance(result_content, list):
                                txt_parts = []
                                for rc in result_content:
                                    if isinstance(rc, dict) and rc.get("type") == "text":
                                        txt_parts.append(rc.get("text", ""))
                                result_text = "\n".join(txt_parts)
                            else:
                                result_text = str(result_content)
                            _push_event(sid, {"type": "tool_result",
                                              "tool_use_id": c.get("tool_use_id", ""),
                                              "text": result_text,
                                              "is_error": c.get("is_error", False)})
                            push_live({"type": "tool_result",
                                       "tool_use_id": c.get("tool_use_id", ""),
                                       "text": result_text,
                                       "is_error": c.get("is_error", False)})
            elif t == "stream_event":
                ev = obj.get("event", {})
                ev_type = ev.get("type")
                if ev_type == "content_block_delta":
                    delta = ev.get("delta", {})
                    if delta.get("type") == "text_delta":
                        txt = delta.get("text", "")
                        got_assistant_output = True
                        # SSE delta 由 :5000 proxy 那條（line ~1701）負責推
                        # 這條只累積給 flush_delta_buffer 寫 jsonl 用
                        # 改前：push_live 也推 → 同 token 兩條路徑各推一次 → 「直接直接動動工工」
                        _delta_buffer["text"] += txt
                    elif delta.get("type") == "thinking_delta":
                        # Claude Code passes thinking blocks as thinking_delta in stream events
                        txt = delta.get("thinking", "")
                        got_assistant_output = True
                        # thinking 走 stdout 解析這條才有（proxy 沒解析 thinking_delta）
                        # 狀態化：enter 推開標籤、token 裸推、exit 由 content_block_stop 推關標籤
                        # 改前：每 token 都包 <thinking>X</thinking> → N 組標籤滿屏
                        if not _thinking_state["active"]:
                            push_live({"type": "delta", "text": "<thinking>\n"})
                            _thinking_state["active"] = True
                        push_live({"type": "delta", "text": txt})
                elif ev_type == "content_block_stop":
                    # 一段文字輸出完整結束 → 存完整 text 到歷史
                    if _thinking_state["active"]:
                        push_live({"type": "delta", "text": "\n</thinking>\n"})
                        _thinking_state["active"] = False
                    flush_delta_buffer()
                elif ev_type == "message_stop":
                    # 整則訊息結束（保險）→ flush 殘留
                    flush_delta_buffer()
            elif t == "result":
                _push_event(sid, {"type": "result",
                                  "text": obj.get("result", ""),
                                  "duration_ms": obj.get("duration_ms"),
                                  "total_cost_usd": obj.get("total_cost_usd")})
                push_live({"type": "result",
                           "text": obj.get("result", ""),
                           "duration_ms": obj.get("duration_ms"),
                           "total_cost_usd": obj.get("total_cost_usd")})
            elif t == "system":
                if obj.get("subtype") == "init":
                    msg = "🚀 Claude 啟動中、本地模型 prefill 處理 prompt...\n⏳ 第一輪需讀完整 system prompt + tool schema、會慢一點"
                    push_live({"type": "warn", "text": msg})
            else:
                push_live({"type": "event", "raw": obj})

        # 保險：stream 結束前把殘留 delta 寫進歷史
        flush_delta_buffer()
        rc = proc.wait()
        stderr = proc.stderr.read() if proc.stderr else ""
        debug_fp.write(f"\n=== rc={rc} got_output={got_assistant_output} ===\n")
        if stderr:
            debug_fp.write(f"STDERR:\n{stderr}\n")
        debug_fp.close()
        if rc != 0:
            # Resume 失敗偵測：剛剛是 resume 模式 + rc!=0 + got_output=False
            # → 標記下次走 prefix fallback；同時清掉 claude_sid 讓下次再開新 session
            if (not is_first_turn) and (not got_assistant_output) and not sess.get("resume_failed"):
                sess["resume_failed"] = True
                sess["claude_sid"] = None  # 下次重建 session
                # 持久化（Flask 重啟也記得）
                _append_to_disk(sid, {
                    "type": "_meta_update",
                    "claude_sid": None,
                    "resume_failed": True,
                })
                _push_event(sid, {"type": "warn",
                                  "text": "Resume 失敗，下次自動切回 prefix 模式接續對話"})
                print(f"[resume failed] sid={sid}, switching to prefix mode")
            # 把完整 stderr 寫到 jsonl（讓 Claude / 我之後 debug）
            _push_event(sid, {"type": "error",
                              "text": f"claude exit {rc}",
                              "stderr": stderr[:2000],
                              "rc": rc})
            try:
                print(f"[claude exit {rc}] stderr: {stderr[:500]}")
            except UnicodeEncodeError:
                pass
        elif not got_assistant_output:
            # 修補：rc=0 但完全沒 assistant 輸出 → 留警告（過去這種狀況 jsonl 只剩 done，無從 debug）
            _push_event(sid, {"type": "warn",
                              "text": f"Claude rc=0 但無任何 assistant 輸出 (model={_alias})。可能：(1) llama-server 沒回 (2) ctx 不夠 (3) 模型直接吐 EOS。看 {sid}.debug.log",
                              "stderr": stderr[:500]})
            try:
                print(f"[claude no output] model={_alias} stderr: {stderr[:500]}")
            except UnicodeEncodeError:
                pass
        sess["history_started"] = True
        _push_event(sid, {"type": "done"})
    except Exception as e:
        try: debug_fp.close()
        except: pass
        _push_event(sid, {"type": "error", "text": str(e)})
    finally:
        CHAT_PROCS.pop(sid, None)

@app.route("/v1/messages", methods=["POST"])
def proxy_v1_messages():
    """內部攔截器：用來攔截 Claude Code 與 LM Studio 之間的流量，破解 Node.js 8KB 緩衝。
    因為 Anthropic SDK 會強制覆寫路徑為 /v1/messages，我們直接在根目錄架設這個路由。
    """
    api_key = request.headers.get("x-api-key", "")
    if "|" in api_key:
        sid, auth = api_key.split("|", 1)
    else:
        sid, auth = "", api_key

    target, _ = get_current_server()
    url = f"{target}/v1/messages"
    
    headers = {key: value for (key, value) in request.headers if key.lower() not in ["host", "x-api-key", "authorization"]}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
        
    try:
        resp = requests.post(url, json=request.json, headers=headers, stream=True, timeout=86400)
        
        def generate():
            # 暫存每個 content block 的累積內容 → block_stop 時寫進歷史 jsonl
            _block_buf = {}  # idx -> {"type":"text"/"tool_use", "text":..., "name":..., "id":..., "input_raw":...}
            for line in resp.iter_lines():
                yield line + b"\n"
                # 解析 SSE
                if line.startswith(b"data: "):
                    try:
                        data = json.loads(line[6:].decode("utf-8"))
                        ev_type = data.get("type")
                        txt = ""
                        _delta_subtype = None

                        if ev_type == "content_block_start":
                            block = data.get("content_block", {})
                            idx = data.get("index", 0)
                            btype = block.get("type")
                            if btype == "text":
                                _block_buf[idx] = {"type": "text", "text": ""}
                                txt = block.get("text", "")
                            elif btype == "tool_use":
                                _block_buf[idx] = {
                                    "type": "tool_use",
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "input_raw": "",
                                }
                                txt = f"\n[準備使用工具: {block.get('name', '')}]\n"

                        elif ev_type == "content_block_delta":
                            idx = data.get("index", 0)
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                txt = delta.get("text", "")
                                _delta_subtype = "text"
                                if idx in _block_buf:
                                    _block_buf[idx]["text"] = _block_buf[idx].get("text", "") + txt
                            elif delta.get("type") == "input_json_delta":
                                txt = delta.get("partial_json", "")
                                _delta_subtype = "json"
                                if idx in _block_buf:
                                    _block_buf[idx]["input_raw"] = _block_buf[idx].get("input_raw", "") + txt

                        elif ev_type == "content_block_stop":
                            # block 結束 → 把這個 block 完整寫進歷史（jsonl + 記憶體）
                            idx = data.get("index", 0)
                            blk = _block_buf.pop(idx, None)
                            if blk and sid:
                                if blk["type"] == "text" and blk.get("text", "").strip():
                                    _push_event(sid, {"type": "text", "text": blk["text"]})
                                elif blk["type"] == "tool_use":
                                    try:
                                        parsed_input = json.loads(blk.get("input_raw", "") or "{}")
                                    except Exception:
                                        parsed_input = {"_raw": blk.get("input_raw", "")}
                                    _push_event(sid, {
                                        "type": "tool_use",
                                        "tool_use_id": blk.get("id", ""),
                                        "name": blk.get("name", ""),
                                        "input": parsed_input,
                                    })

                        elif ev_type == "message_stop":
                            # 整則訊息結束 → 推 result + done（方便前端 + 重播）
                            if sid:
                                _push_event(sid, {"type": "done"})

                        if txt and sid:
                            # 直接推播到 GUI！破解 Node.js buffering！
                            _ensure_session(sid)
                            import time
                            if sid not in CHAT_LIVE_BUFFERS:
                                CHAT_LIVE_BUFFERS[sid] = ""
                            CHAT_LIVE_BUFFERS[sid] += txt
                            ev = {"type": "delta", "text": txt, "ts": int(time.time() * 1000)}
                            if _delta_subtype:
                                ev["subtype"] = _delta_subtype
                            for q in list(CHAT_QUEUES[sid]):
                                q.put(ev)
                    except Exception:
                        pass
        return Response(generate(), status=resp.status_code, headers=dict(resp.headers))
    except Exception as e:
        return str(e), 500


@app.route("/api/chat/new", methods=["POST"])
def api_chat_new():
    """開新 chat session。body: {cwd: <資料夾路徑>}"""
    data = request.json or {}
    cwd = data.get("cwd", "").strip()
    if not cwd:
        return jsonify({"ok": False, "error": "cwd required"}), 400
    p = Path(cwd)
    if not p.exists() or not p.is_dir():
        return jsonify({"ok": False, "error": "cwd 不存在"}), 400
    sid = str(uuid.uuid4())
    CHAT_SESSIONS[sid] = {"cwd": str(p), "history_started": False}
    _ensure_session(sid)
    # 寫 meta 到 .jsonl（第一行）
    _append_to_disk(sid, {"type": "_meta", "cwd": str(p),
                          "created": time.time(), "history_started": False})
    return jsonify({"ok": True, "session_id": sid, "cwd": str(p)})


@app.route("/api/chat/send", methods=["POST"])
def api_chat_send():
    """送一則訊息給 session。body: {session_id, message}

    後端只擋「同 session 重複送」（避免使用者連點）。
    不擋多 session 並發 — Claude Code 內部會用子 agent 並行加速。
    """
    data = request.json or {}
    sid = data.get("session_id", "")
    msg = data.get("message", "")
    img_b64 = data.get("image_base64")

    if not sid or sid not in CHAT_SESSIONS:
        return jsonify({"ok": False, "error": "session 不存在，請先 /api/chat/new"}), 400
    if not msg.strip() and not img_b64:
        return jsonify({"ok": False, "error": "message 和圖片不能同時為空"}), 400
    if sid in CHAT_PROCS:
        return jsonify({"ok": False, "error": "上一則還在跑，請等完成或按中斷"}), 409
    # 註：後端不擋多 session 並發 — Claude Code 內部會用子 agent 並行加速。
    # 多場對話衝突由前端 UI 引導（小心不要不小心開兩場手動任務）。

    _ensure_session(sid)
    # 背景跑，不 block HTTP
    threading.Thread(target=_run_claude_msg, args=(sid, msg, img_b64), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/chat/stream")
def api_chat_stream():
    """SSE：接收某 session 的訊息流"""
    sid = request.args.get("session_id", "")
    if not sid or sid not in CHAT_SESSIONS:
        return "session 不存在", 400
    _ensure_session(sid)
    q = queue.Queue()
    CHAT_QUEUES[sid].append(q)

    @stream_with_context
    def gen():
        try:
            # 先送一個 hello，確認連上
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    ev = q.get(timeout=5)
                except queue.Empty:
                    # 5 秒沒事件就送 keepalive，避免手機/代理判定斷線
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "done":
                    # 該則訊息完成，但 stream 繼續，等下一則
                    pass
        finally:
            if sid in CHAT_QUEUES and q in CHAT_QUEUES[sid]:
                CHAT_QUEUES[sid].remove(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/chat/stop", methods=["POST"])
def api_chat_stop():
    """中斷當前訊息（殺 PID）— Claude 仍會記得歷史（下次重餵 prefix）"""
    data = request.json or {}
    sid = data.get("session_id", "")
    proc = CHAT_PROCS.get(sid)
    if not proc:
        return jsonify({"ok": True, "msg": "沒有正在跑的 process"})
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        # 在歷史上留中斷標記，下次重餵 prefix 時 Claude 看得到「上一輪被中斷」
        _push_event(sid, {"type": "text",
                          "text": "[此處對話被使用者中斷]"})
        return jsonify({"ok": True, "msg": f"已中斷 PID {proc.pid}（Claude 仍記得歷史）"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/chat/compact", methods=["POST"])
def api_chat_compact():
    """請 Claude 自己壓縮當前對話（產生摘要替代歷史）

    安全限制：
    - 必須有 session_id 且該 session 已有對話歷史
    - 上一則訊息必須跑完（不能在 running 狀態下壓縮）
    - 固定 prompt 文字，不接受 user 輸入 → 防注入
    - ctx > 95% 時也可用（這正是它存在的目的）
    """
    data = request.json or {}
    sid = data.get("session_id", "")
    if not sid or sid not in CHAT_SESSIONS:
        return jsonify({"ok": False, "error": "session 不存在"}), 400
    if not CHAT_SESSIONS[sid].get("history_started"):
        return jsonify({"ok": False, "error": "對話還沒開始，不需要壓縮"}), 400
    if sid in CHAT_PROCS:
        return jsonify({"ok": False, "error": "上一則還在跑，等完成再壓縮"}), 409

    # 固定的壓縮指令（手機端按鈕觸發、不接受任意文字）
    COMPACT_PROMPT = (
        "請對本次對話到目前為止的所有內容做一份簡短摘要："
        "（1）使用者想完成的目標；（2）已完成的工作與檔案；"
        "（3）尚未完成或下一步要做的事；（4）關鍵決策或限制。"
        "輸出格式：純 markdown 條列，不超過 400 字。"
        "做完之後，後續就用這份摘要當作你記憶的全部依據。"
    )
    _ensure_session(sid)
    threading.Thread(target=_run_claude_msg, args=(sid, COMPACT_PROMPT), daemon=True).start()
    return jsonify({"ok": True, "msg": "壓縮中..."})


@app.route("/api/chat/sessions")
def api_chat_sessions():
    """列出所有 chat session，含標題/最近活動時間/事件數，給歷史清單用"""
    result = []
    for sid, s in CHAT_SESSIONS.items():
        hist = CHAT_HISTORY.get(sid, [])
        # title = 第一則用戶訊息（截前 40 字）
        title = "(空對話)"
        for ev in hist:
            if ev.get("type") == "user":
                t = ev.get("text", "").strip().replace("\n", " ")
                title = (t[:40] + "…") if len(t) > 40 else t
                break
        # last_active = 最後一個事件加入時的記錄（沒存時間戳，用 session 建立順序近似）
        # 用 history 長度當「活躍度」指標
        result.append({
            "session_id": sid,
            "cwd": s["cwd"],
            "folder_name": Path(s["cwd"]).name,
            "title": title,
            "events": len(hist),
            "running": sid in CHAT_PROCS,
            "history_started": s.get("history_started", False),
        })
    # 跑中的排前面、再依事件數遞減（活躍度近似）
    result.sort(key=lambda x: (not x["running"], -x["events"]))
    return jsonify({"ok": True, "sessions": result})


@app.route("/api/chat/delete", methods=["POST"])
def api_chat_delete():
    """刪除某 session（清歷史 + 停掉若還在跑）"""
    data = request.json or {}
    sid = data.get("session_id", "")
    if not sid or sid not in CHAT_SESSIONS:
        return jsonify({"ok": False, "error": "session 不存在"}), 404
    # 若還在跑 → 砍 PID
    proc = CHAT_PROCS.get(sid)
    if proc:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        except Exception:
            pass
        CHAT_PROCS.pop(sid, None)
    CHAT_SESSIONS.pop(sid, None)
    CHAT_HISTORY.pop(sid, None)
    CHAT_QUEUES.pop(sid, None)
    CHAT_LOCKS.pop(sid, None)
    # 刪硬碟檔
    try:
        f = _session_file(sid)
        if f.exists():
            f.unlink()
    except Exception as e:
        print(f"[persist] delete fail {sid}: {e}")
    return jsonify({"ok": True})


@app.route("/api/chat/dump")
def api_chat_dump():
    """純文字版對話視圖（給 Claude/人類監控用）

    Query:
      session_id=<sid>  指定 session；省略則回傳「最近一場」
      tail=N           只看最後 N 筆事件（預設全部）
      format=text      預設文字；可加 ?format=json
    """
    sid = request.args.get("session_id", "").strip()
    tail = request.args.get("tail", type=int)
    fmt = request.args.get("format", "text")

    # 沒指定 → 最近活動的 session
    if not sid:
        if not CHAT_SESSIONS:
            return "no sessions yet", 200, {"Content-Type": "text/plain; charset=utf-8"}
        # 用 .jsonl mtime 排序找最新
        files = sorted(SESSIONS_DIR.glob("*.jsonl"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return "no sessions yet", 200, {"Content-Type": "text/plain; charset=utf-8"}
        sid = files[0].stem

    if sid not in CHAT_SESSIONS:
        return f"session {sid} not found", 404, {"Content-Type": "text/plain; charset=utf-8"}

    sess = CHAT_SESSIONS[sid]
    events = CHAT_HISTORY.get(sid, [])
    if tail:
        events = events[-tail:]

    if fmt == "json":
        return jsonify({"session_id": sid, "cwd": sess["cwd"],
                        "running": sid in CHAT_PROCS, "events": events})

    # 文字版：模仿手機 GUI 視覺
    lines = []
    lines.append("=" * 60)
    lines.append(f"Session: {sid}")
    lines.append(f"CWD:     {sess['cwd']}")
    lines.append(f"Running: {'YES' if sid in CHAT_PROCS else 'no'}")
    lines.append(f"Events:  {len(CHAT_HISTORY.get(sid, []))} (showing {len(events)})")
    lines.append("=" * 60)
    for ev in events:
        t = ev.get("type", "?")
        if t == "user":
            lines.append(f"\n👤 USER: {ev.get('text', '')}")
        elif t == "status":
            lines.append(f"⏳ {ev.get('text', '')}")
        elif t == "text":
            text = ev.get("text", "").strip()
            if text:
                lines.append(f"\n🤖 CLAUDE:\n{text}")
        elif t == "tool_use":
            name = ev.get("name", "?")
            inp = ev.get("input", {})
            # 把 input 壓成單行摘要
            inp_str = json.dumps(inp, ensure_ascii=False)
            if len(inp_str) > 200:
                inp_str = inp_str[:200] + "..."
            lines.append(f"🔧 TOOL [{name}]: {inp_str}")
        elif t == "result":
            dur = ev.get("duration_ms", 0)
            cost = ev.get("total_cost_usd", 0) or 0
            lines.append(f"\n✓ RESULT  duration={dur/1000:.1f}s  cost=${cost:.4f}")
        elif t == "error":
            lines.append(f"\n❌ ERROR: {ev.get('text', '')}")
            if ev.get("stderr"):
                lines.append(f"   stderr: {ev['stderr'][:500]}")
        elif t == "done":
            lines.append(f"─ done ─")
    lines.append("")
    lines.append("=" * 60)
    body = "\n".join(lines)
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/chat/history")
def api_chat_history():
    """回傳 session 完整歷史事件（F5 / 重開 App 後重播用）"""
    sid = request.args.get("session_id", "")
    if not sid or sid not in CHAT_SESSIONS:
        return jsonify({"ok": False, "error": "session 不存在"}), 404
    _ensure_session(sid)
    with CHAT_LOCKS[sid]:
        events = list(CHAT_HISTORY.get(sid, []))
        if sid in CHAT_LIVE_BUFFERS and CHAT_LIVE_BUFFERS[sid]:
            import time
            events.append({"type": "delta", "text": CHAT_LIVE_BUFFERS[sid], "ts": int(time.time() * 1000)})
    return jsonify({
        "ok": True,
        "session_id": sid,
        "cwd": CHAT_SESSIONS[sid].get("cwd"),
        "running": sid in CHAT_PROCS,
        "events": events,
    })


# ===== 通用 Job Queue（給 vision + 切換模型用，手機關螢幕也能繼續）=====
# 模式：HTTP 馬上回 job_id → 背景 thread 跑 → 前端 polling /api/job/<id>
JOBS = {}  # job_id -> {"status": pending|running|done|error, "result": ..., "error": ..., "ts": time}
JOBS_LOCK = threading.Lock()


def _new_job(kind="generic"):
    jid = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[jid] = {
            "status": "pending",
            "kind": kind,
            "result": None,
            "error": None,
            "progress": "",
            "created": time.time(),
            "updated": time.time(),
        }
    return jid


def _set_job(jid, **kwargs):
    with JOBS_LOCK:
        if jid not in JOBS:
            return
        JOBS[jid].update(kwargs)
        JOBS[jid]["updated"] = time.time()


def _clean_old_jobs():
    """清掉 30 分鐘前完成的 job"""
    now = time.time()
    with JOBS_LOCK:
        stale = [jid for jid, j in JOBS.items()
                 if j["status"] in ("done", "error") and now - j["updated"] > 1800]
        for jid in stale:
            JOBS.pop(jid, None)


@app.route("/api/job/<jid>")
def api_job_status(jid):
    """polling 端點：回傳 job 狀態 + 結果"""
    _clean_old_jobs()
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if not j:
            return jsonify({"ok": False, "error": "job 不存在或已過期"}), 404
        return jsonify({"ok": True, **j})


# ===== 📷 圖片問答 + 聯網（直接打 27B/35B API，不走 Claude Code）=====
# 用途：純對話 + 看圖（Qwen3.6-35B Uncensored 用 mmproj）+ 自動 ddgs 聯網
# 限制：沒工具（不能改檔/跑 cmd），那是 Claude Code 對話框的事

# 觸發聯網的關鍵字（中英文）
WEB_SEARCH_TRIGGERS = [
    "最新", "今天", "今日", "現在", "目前", "新聞", "報導",
    "2024", "2025", "2026", "2027",
    "latest", "today", "current", "news", "recent",
    "查一下", "搜尋", "search", "google",
    "天氣", "weather", "股價", "stock",
    "誰是", "什麼是", "是誰", "what is", "who is",
]


# 生圖 / 改圖請求關鍵字（這類請求一律不查網、避免亂打斷）
IMAGE_REQUEST_TRIGGERS = [
    # 中文
    "畫", "生成", "生圖", "產生", "做張", "做一張", "做個", "畫個", "畫一張",
    "幫我生", "幫我畫", "幫我做", "幫我產",
    "改圖", "改一下", "改成", "換成", "重繪", "局部", "重畫",
    "去背", "去除", "拿掉", "加上",
    # 英文
    "generate", "create", "draw", "paint", "render", "make",
    "edit", "inpaint", "img2img", "change", "replace", "remove",
]

def _is_image_request(message):
    """是不是叫模型生圖/改圖的請求？是的話即使 auto 命中 trigger 也跳過聯網"""
    m = message.lower()
    for k in IMAGE_REQUEST_TRIGGERS:
        if k.lower() in m:
            return True
    return False


def _needs_web_search(message):
    """判斷訊息是否需要聯網（生圖 / 改圖請求一律 False）"""
    if _is_image_request(message):
        return False
    m = message.lower()
    for k in WEB_SEARCH_TRIGGERS:
        if k.lower() in m:
            return True
    return False


def _do_web_search(query, max_results=5, body_chars=150):
    """跑 ddgs Python lib 搜尋（直接 import 比 CLI 快且穩）

    body_chars: 每筆 body 截短到 N 字（預設 150，省 prompt）
    """
    try:
        from ddgs import DDGS
        with DDGS() as ddg:
            results = list(ddg.text(query, max_results=max_results))
        if not results:
            print(f"[ddgs] empty results for: {query}")
            return None
        lines = []
        for i, item in enumerate(results[:max_results], 1):
            title = item.get("title", "") or ""
            body = (item.get("body", "") or "")[:body_chars]
            href = item.get("href", "") or ""
            lines.append(f"[{i}] {title}\n{body}\n來源: {href}")
        return "\n\n".join(lines)
    except Exception as e:
        print(f"[ddgs] exception: {e}")
        import traceback; traceback.print_exc()
        return None


# 每個 vision session 的歷史（持久化到硬碟，圖片獨立存 .jpg）
VISION_HISTORY = {}  # session_id -> list of {"role": "...", "content": ...}
VISION_DIR = SESSIONS_DIR.parent / "vision_sessions"
VISION_DIR.mkdir(exist_ok=True)


def _vision_session_file(sid):
    """每個 vision session 對應的 jsonl 元資料檔"""
    return VISION_DIR / f"{sid}.jsonl"


def _vision_img_dir(sid):
    """該 session 的圖片資料夾"""
    d = VISION_DIR / sid
    d.mkdir(exist_ok=True)
    return d


def _vision_save_image(sid, data_url):
    """把 data URL 圖片寫到硬碟，回傳 file:// 相對路徑當識別"""
    import re
    m = re.match(r"data:image/(\w+);base64,(.+)", data_url, re.DOTALL)
    if not m:
        return None
    ext, b64data = m.group(1), m.group(2)
    img_dir = _vision_img_dir(sid)
    # 用 hash 當檔名避免重複
    import hashlib
    h = hashlib.md5(b64data.encode("ascii")).hexdigest()[:12]
    img_path = img_dir / f"{h}.{ext}"
    if not img_path.exists():
        try:
            img_path.write_bytes(base64.b64decode(b64data))
        except Exception as e:
            print(f"[vision] save img fail: {e}")
            return None
    # 回傳相對 sessions/vision_sessions/<sid>/<hash>.<ext>
    return f"file:vision_sessions/{sid}/{h}.{ext}"


def _vision_load_image(file_ref):
    """從 file: ref 讀回 base64 data URL"""
    if not file_ref or not file_ref.startswith("file:"):
        return file_ref
    rel = file_ref[5:]  # 去掉 'file:' prefix
    img_path = _resolve_ref_path(rel)
    if not img_path.exists():
        return None
    ext = img_path.suffix.lstrip(".") or "jpeg"
    b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
    return f"data:image/{ext};base64,{b64}"


def _vision_persist(sid, msg):
    """把一則訊息 append 到 .jsonl（圖片內容改寫成 file ref 不入庫）"""
    try:
        # 深拷貝避免改到記憶體版
        import copy
        m_to_save = copy.deepcopy(msg)
        content = m_to_save.get("content")
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "image_url":
                    url = c.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        ref = _vision_save_image(sid, url)
                        if ref:
                            c["image_url"]["url"] = ref
        with open(_vision_session_file(sid), "a", encoding="utf-8") as f:
            f.write(json.dumps(m_to_save, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[vision] persist fail {sid}: {e}")


def _vision_load_all():
    """Flask 啟動時掃 vision_sessions/ 載回所有對話"""
    count = 0
    for f in VISION_DIR.glob("*.jsonl"):
        sid = f.stem
        try:
            msgs = []
            with open(f, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msgs.append(json.loads(line))
                    except Exception:
                        pass
            VISION_HISTORY[sid] = msgs
            count += 1
        except Exception as e:
            print(f"[vision] load fail {f.name}: {e}")
    print(f"[vision] loaded {count} sessions from {VISION_DIR}")


def _ask_model_for_keywords(img_b64, user_msg):
    """有圖時，先讓模型看圖吐出「該搜什麼關鍵字」（不聯網，純看圖）

    回傳：關鍵字字串（單行），或 None（模型沒給或失敗）
    """
    try:
        img_clean = img_b64
        if img_clean.startswith("data:"):
            img_clean = img_clean.split(",", 1)[1] if "," in img_clean else img_clean
        content = [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{img_clean}"}},
            {"type": "text",
             "text": (
                 f"使用者問我：「{user_msg}」\n\n"
                 "請只看圖片，列出 3-5 個關鍵字（用空格分隔，**單行**，不要解釋、不要分點）"
                 "讓我用這些關鍵字 Google 搜尋幫使用者找答案。\n"
                 "範例輸出：BMW Z4 痛車 AI HIME 動漫\n\n"
                 "你的關鍵字："
             )},
        ]
        payload = {
            "model": "any",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 2048,
            "stream": False,
        }
        base_url, auth = get_current_server()
        r = requests.post(f"{base_url}/v1/chat/completions",
                          json=payload, timeout=180,
                          headers={"Authorization": f"Bearer {auth}"})
        if not r.ok:
            return None
        txt = r.json()["choices"][0]["message"]["content"].strip()
        # 去掉 <think>...</think>
        import re
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL).strip()
        # 取最後一行（避免 thinking 殘留）
        last_line = [l for l in txt.split("\n") if l.strip()]
        if not last_line:
            return None
        kw = last_line[-1].strip()
        # 清掉「你的關鍵字：」前綴
        for prefix in ("你的關鍵字：", "你的關鍵字:", "關鍵字：", "關鍵字:"):
            if kw.startswith(prefix):
                kw = kw[len(prefix):].strip()
        if len(kw) > 100 or len(kw) < 3:
            return None
        return kw
    except Exception as e:
        print(f"[keywords] {e}")
        return None


def _find_recent_image_in_history(sid):
    """從 session 歷史抓最近一張 user 圖片 → 回 data URL（base64）

    用於 Bug B：使用者接續對話沒重新上傳圖時，自動沿用最近的圖
    """
    history = VISION_HISTORY.get(sid, [])
    # 從後往前找
    for m in reversed(history):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if c.get("type") == "image_url":
                url = c.get("image_url", {}).get("url", "")
                if url.startswith("file:"):
                    # 持久化版本 → 讀回 base64
                    return _vision_load_image(url)
                elif url.startswith("data:"):
                    # 記憶體版本（同會話沒被持久化過的）
                    return url
    return None


def _run_vision_job(jid, sid, msg, img_b64, search_mode):
    """背景跑 vision 任務（給 job queue 用）"""
    try:
        _set_job(jid, status="running", progress="處理中...")

        # Bug B：沒帶圖但歷史有 → 自動沿用最近的圖
        reused_image = False
        if not img_b64:
            recent = _find_recent_image_in_history(sid)
            if recent:
                img_b64 = recent
                reused_image = True
                print(f"[vision] auto-reuse last image from history (sid={sid[:8]})")

        # 聯網判斷
        search_result = None
        did_search = False
        skipped_search_for_img = False
        actual_query = msg  # 實際送給 ddgs 的關鍵字（雙階段可能會被改寫）
        # 生圖 / 改圖請求：即使 search_mode=on 也跳過、避免拖慢生圖
        is_img_req = msg and _is_image_request(msg)
        if msg and not is_img_req and (search_mode == "on" or (search_mode == "auto" and _needs_web_search(msg))):
            # 雙階段：有圖時先讓模型生關鍵字（不用使用者原句當搜尋詞）
            if img_b64:
                _set_job(jid, progress="🔍 從圖判斷搜尋關鍵字...")
                kw = _ask_model_for_keywords(img_b64, msg)
                if kw:
                    actual_query = kw
                    print(f"[vision] keywords from model: {kw}")
            _set_job(jid, progress=f"🌐 搜尋「{actual_query}」中...")
            max_r = 3 if img_b64 else 5
            search_result = _do_web_search(actual_query, max_results=max_r)
            did_search = bool(search_result)

        # 組裝 user content
        user_content = []
        if img_b64:
            img_clean = img_b64
            if img_clean.startswith("data:"):
                img_clean = img_clean.split(",", 1)[1] if "," in img_clean else img_clean
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_clean}"},
            })

        text_part = msg
        if search_result:
            text_part = (
                f"以下是我用 DuckDuckGo 搜尋「{actual_query}」（從圖判斷的關鍵字）的結果，"
                f"請根據這些資料 + 圖片內容回答使用者問題：\n\n"
                f"{search_result}\n\n"
                f"---\n使用者原問題：{msg}"
            )
        if text_part:
            user_content.append({"type": "text", "text": text_part})

        # 歷史 + 持久化
        history = VISION_HISTORY.setdefault(sid, [])
        user_msg = {"role": "user", "content": user_content}
        history.append(user_msg)
        _vision_persist(sid, user_msg)
        if len(history) > 40:
            history[:] = history[-40:]

        _set_job(jid, progress="🤖 模型回應中...")

        # 35B-A3B 有 thinking，會吃掉大量 tokens → max_tokens 拉大避免內容被截斷
        # 偷偷塞入系統提示，教導 35B 如何畫圖以及作為 Prompt Engineer
        # 後端 comfy_proxy 已自動加 quality 前綴 + negative，35B 只需專注「主體+細節+style」
        sys_prompt = {"role": "system", "content": (
            "You are a helpful assistant AND an SDXL prompt engineer. The backend (Pony Realism SDXL) "
            "AUTOMATICALLY adds quality prefix + negatives based on the 'style' parameter — so YOU only write "
            "[SUBJECT + DETAILS]. Do NOT include 'masterpiece, 8k, best quality, score_9' etc; backend handles it. "
            "Do NOT write any negative prompt; backend handles it.\n\n"
            "===== ROUTING =====\n"
            "• NEW IMAGE from text: ![image](/api/vision/generate?prompt={ENGLISH_PROMPT}&style={STYLE})\n"
            "• EDIT WHOLE uploaded image (keep composition, change style): ![image](/api/vision/img2img?prompt={ENGLISH_PROMPT}&denoise=0.5&style={STYLE})\n"
            "• EDIT SPECIFIC PART of uploaded image: ![mask](/api/vision/get_mask?target={ORIGINAL_OBJECT}&replace={REPLACEMENT_PROMPT}&style={STYLE})\n"
            "  - target = short English of what to mask (e.g. 'red dress', 'face', 'sky')\n"
            "  - replace = English description of what to put there (subject + details, NO quality/negative)\n\n"
            "===== STYLE PARAMETER (CRITICAL — pick correct one or output looks wrong) =====\n"
            "• style=realistic  → photo/portrait/landscape/product shots (DEFAULT if user doesn't say)\n"
            "• style=anime      → anime/manga/二次元/動漫 characters\n"
            "• style=art        → digital art/illustration/concept art/oil painting/fantasy\n"
            "• style=cute       → chibi/kawaii/可愛/Q版/兒童繪本風\n\n"
            "===== PROMPT STRUCTURE (universal, applies to all subjects) =====\n"
            "[SUBJECT_TAG], [DETAILS]\n\n"
            "SUBJECT_TAG (always lead with one of these — prevents anatomy meltdown):\n"
            "  • Single person       → '1girl, solo' OR '1boy, solo'\n"
            "  • Multiple people     → '2girls' / '3boys' / 'group of people'\n"
            "  • Animal              → '1cat, solo' / '1dog, solo' / '1bird, solo'\n"
            "  • Landscape/scenery   → 'scenery, no humans'\n"
            "  • Food/object/product → 'still life, no humans'\n"
            "  • Architecture        → 'architecture, no humans'\n"
            "  • Vehicle             → 'vehicle, no humans' (or with driver: 'vehicle, 1person')\n"
            "  • Abstract/pattern    → 'abstract pattern'\n\n"
            "DETAILS (translate user's words into concrete English visual tags, comma-separated):\n"
            "  • Person: appearance (hair color, hair length, eyes, expression, clothing), pose (standing, sitting, looking at viewer), composition (portrait, close-up, full body), environment, lighting\n"
            "  • Landscape: location, time of day, weather, atmosphere, color palette, focal point\n"
            "  • Object: material, color, surface, setup (on wooden table, white background), lighting (studio light, soft light)\n\n"
            "===== EXAMPLES =====\n"
            "User: '幫我生成一張可愛女孩的寫實照片'\n"
            "→ ![image](/api/vision/generate?prompt=1girl%2C%20solo%2C%20cute%20young%20woman%2C%20long%20brown%20hair%2C%20gentle%20smile%2C%20looking%20at%20viewer%2C%20portrait%2C%20soft%20natural%20lighting%2C%20bokeh%20background&style=realistic)\n\n"
            "User: '畫一個夕陽下的海邊'\n"
            "→ ![image](/api/vision/generate?prompt=scenery%2C%20no%20humans%2C%20beach%2C%20sunset%2C%20golden%20hour%2C%20ocean%20waves%2C%20dramatic%20orange%20sky%2C%20reflection%20on%20wet%20sand&style=realistic)\n\n"
            "User: '一隻橘貓睡在窗邊'\n"
            "→ ![image](/api/vision/generate?prompt=1cat%2C%20solo%2C%20orange%20tabby%20cat%2C%20sleeping%2C%20curled%20up%20by%20window%2C%20warm%20afternoon%20sunlight%2C%20cozy%20indoor%20scene&style=realistic)\n\n"
            "User: '動漫風的女戰士'\n"
            "→ ![image](/api/vision/generate?prompt=1girl%2C%20solo%2C%20female%20warrior%2C%20long%20silver%20hair%2C%20fantasy%20armor%2C%20holding%20sword%2C%20confident%20expression%2C%20battle%20pose%2C%20dramatic%20background&style=anime)\n\n"
            "User: '一杯熱拿鐵咖啡'\n"
            "→ ![image](/api/vision/generate?prompt=still%20life%2C%20no%20humans%2C%20hot%20latte%20coffee%2C%20white%20ceramic%20cup%2C%20latte%20art%20on%20foam%2C%20wooden%20table%2C%20warm%20cafe%20lighting%2C%20steam%20rising&style=realistic)\n\n"
            "User: '幫我畫一隻 Q 版的狗狗'\n"
            "→ ![image](/api/vision/generate?prompt=1dog%2C%20solo%2C%20cute%20chibi%20puppy%2C%20big%20round%20eyes%2C%20fluffy%20fur%2C%20happy%20expression%2C%20simple%20background&style=cute)\n\n"
            "===== HARD RULES =====\n"
            "1. ALL prompts in ENGLISH (translate from Chinese silently)\n"
            "2. URL-encode (spaces=%20, comma=%2C)\n"
            "3. ALWAYS lead with a SUBJECT_TAG (1girl/scenery/still life/etc) — this is the #1 anti-meltdown rule\n"
            "4. ALWAYS pick a style param (realistic/anime/art/cute), even if user doesn't specify (default=realistic)\n"
            "5. DO NOT add quality words (masterpiece, 8k, best quality, score_9, RAW photo, etc) — backend adds them\n"
            "6. DO NOT add any negative prompt — backend adds them\n"
            "7. DO NOT use tools/fetch/curl — just output the markdown string\n"
            "8. Output ONLY the markdown image tag with brief Chinese preamble (1 sentence max)"
        )}
        
        # 35B 每次只送「system + 這次的 user 訊息」、不送歷史、避免 ctx 爆 + 35B 看到自己以前生圖紀錄
        # 副作用：35B 不記得之前對話（譬如「再生一張類似的」會不知道指啥）— 這是設計、不是 bug
        clean_history = []
        if history:
            # 抓最後一則 user 訊息（剛剛 append 的、含圖如果有）
            for m in reversed(history):
                if m.get("role") == "user":
                    clean_history = [m]
                    break

        payload = {
            "model": "any",
            "messages": [sys_prompt] + clean_history,
            "max_tokens": 4096,
            "stream": False,
        }
        base_url, auth = get_current_server()
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json=payload, timeout=300,
            headers={"Authorization": f"Bearer {auth}"},
        )
        if not r.ok:
            _set_job(jid, status="error", error=f"LLM API {r.status_code}: {r.text[:300]}")
            return
        resp = r.json()
        assistant_msg = resp["choices"][0]["message"]["content"]
        asst_msg = {"role": "assistant", "content": assistant_msg}
        history.append(asst_msg)
        _vision_persist(sid, asst_msg)
        _set_job(jid, status="done", result={
            "session_id": sid,
            "reply": assistant_msg,
            "searched": did_search,
            "search_query": actual_query if did_search else None,
            "search_result": search_result if did_search else None,
            "skipped_search_for_img": skipped_search_for_img,
            "reused_image": reused_image,
        })
    except requests.Timeout:
        _set_job(jid, status="error", error="LLM 回應超時（300s）")
    except Exception as e:
        _set_job(jid, status="error", error=str(e))


@app.route("/api/vision/chat", methods=["POST"])
def api_vision_chat():
    """非同步版：立刻回 job_id，背景跑，前端 polling /api/job/<id> 拿結果

    body: {session_id, message, image_base64?, search?}
    回傳: {ok, job_id, session_id}
    """
    data = request.json or {}
    sid = data.get("session_id", "").strip() or str(uuid.uuid4())
    msg = (data.get("message", "") or "").strip()
    img_b64 = data.get("image_base64") or None
    search_mode = data.get("search", "auto")

    if not msg and not img_b64:
        return jsonify({"ok": False, "error": "message 或 image 至少要一個"}), 400

    # 確保 session 存在
    VISION_HISTORY.setdefault(sid, [])

    # 開 job + 背景跑
    jid = _new_job("vision")
    threading.Thread(
        target=_run_vision_job,
        args=(jid, sid, msg, img_b64, search_mode),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "job_id": jid, "session_id": sid})


@app.route("/api/vision/new", methods=["POST"])
def api_vision_new():
    """開新 vision session"""
    sid = str(uuid.uuid4())
    VISION_HISTORY[sid] = []
    return jsonify({"ok": True, "session_id": sid})


@app.route("/api/vision/history")
def api_vision_history():
    """回傳 session 歷史（前端 F5 重播；file ref 圖片轉回 data URL）

    處理：
    - user 訊息：list[image_url] 把 file:ref 轉 data URL
    - assistant 訊息：字串 markdown ![image](file:ref) 把 file:ref 換成 /api/vision/img endpoint
    """
    sid = request.args.get("session_id", "")
    if not sid or sid not in VISION_HISTORY:
        return jsonify({"ok": True, "messages": []})
    import copy, re
    msgs = []
    for m in VISION_HISTORY[sid]:
        mc = copy.deepcopy(m)
        content = mc.get("content")
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "image_url":
                    url = c.get("image_url", {}).get("url", "")
                    if url.startswith("file:"):
                        new = _vision_load_image(url)
                        if new:
                            c["image_url"]["url"] = new
        elif isinstance(content, str):
            # 把 ![<alt>](file:vision_sessions/...) 換成 ![<alt>](/api/vision/img?ref=file:...)
            # alt 可能是 image / inpaint|prompt=...|denoise=... 等任意內容、保留
            def _replace_ref(match):
                alt = match.group(1)
                ref = match.group(2)
                if ref.startswith("file:"):
                    from urllib.parse import quote
                    return f"![{alt}](/api/vision/img?ref={quote(ref)})"
                return match.group(0)
            mc["content"] = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _replace_ref, content)
        msgs.append(mc)
    return jsonify({"ok": True, "messages": msgs})


# ============================================================
# 🚫 已刪除黑名單（v57）— 使用者在 Z 槽手動砍圖、前端 img onerror 觸發、
#     寫進這個檔。之後請求同樣的 prompt/seed/ref 直接回 410、不重新算圖。
# ============================================================
_DELETED_REFS_FILE = VISION_DIR / "_deleted_refs.json"
_DELETED_REFS_CACHE = None  # set | None


def _load_deleted_refs():
    """讀黑名單到 memory cache（lazy load）。"""
    global _DELETED_REFS_CACHE
    if _DELETED_REFS_CACHE is not None:
        return _DELETED_REFS_CACHE
    try:
        if _DELETED_REFS_FILE.exists():
            data = json.loads(_DELETED_REFS_FILE.read_text(encoding="utf-8"))
            _DELETED_REFS_CACHE = set(data.get("refs", []))
        else:
            _DELETED_REFS_CACHE = set()
    except Exception:
        _DELETED_REFS_CACHE = set()
    return _DELETED_REFS_CACHE


def _save_deleted_refs():
    """寫回硬碟。"""
    refs = _load_deleted_refs()
    try:
        _DELETED_REFS_FILE.write_text(
            json.dumps({"refs": sorted(refs)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[deleted_refs] save fail: {e}")


def _is_deleted(key):
    """key 可以是 file:ref、generate URL 的 hash key、或自訂字串。"""
    if not key:
        return False
    return key in _load_deleted_refs()


def _mark_deleted(key):
    """加進黑名單 + 寫硬碟。"""
    if not key:
        return
    refs = _load_deleted_refs()
    if key not in refs:
        refs.add(key)
        _save_deleted_refs()


@app.route("/api/vision/img")
def api_vision_img():
    """從 file:vision_sessions/<sid>/<hash>.<ext> ref 直接送圖（不轉 base64）"""
    from flask import send_file
    ref = request.args.get("ref", "")
    if not ref.startswith("file:"):
        return "bad ref", 400
    rel = ref[5:]
    if ".." in rel:
        return "traversal", 403
    # v57: 黑名單檢查、避免重算
    if _is_deleted(ref):
        return "gone (deleted)", 410
    img_path = _resolve_ref_path(rel)
    if not img_path.exists() or not img_path.is_file():
        return "not found", 404
    return send_file(img_path)


@app.route("/api/vision/mark_deleted", methods=["POST"])
def api_vision_mark_deleted():
    """v57 — 前端 img onerror 時呼叫、把這個 ref / URL 加進黑名單、之後不重新算。
    body: {"ref": "file:vision_sessions/..."} 或 {"key": "/api/vision/generate?prompt=..."}
    """
    data = request.json or {}
    key = (data.get("ref") or data.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "empty key"}), 400
    _mark_deleted(key)
    return jsonify({"ok": True, "count": len(_load_deleted_refs())})


# ============================================================
# 🔒 批次生圖鎖（v64）— 確保同時只有一個瀏覽器 tab 跑批次
#   防呆：手機 + 桌面 + 第二個 tab 不會疊加跑、避免「自己一直跑」
#   機制：in-memory 全域 dict、claim 時給 owner_id、心跳維持
#   stale 邏輯：last_heartbeat > 30 秒沒更新 = 上一個 tab 死掉、強制接管
# ============================================================
import threading as _threading
import time as _time
import uuid as _uuid

_BATCH_LOCK = {"owner_id": None, "claimed_at": 0, "last_heartbeat": 0}
_BATCH_LOCK_MX = _threading.Lock()
_BATCH_LOCK_STALE_SEC = 30  # 心跳間隔超過這個 → 視為死亡、可被搶


@app.route("/api/batch/claim", methods=["POST"])
def api_batch_claim():
    """前端跑批次前呼叫。
    body: {"force": true/false}（true = 強搶、即使有人在跑也搶過來）
    回: {"ok": true, "owner_id": "uuid", "is_new": true/false}
        or {"ok": false, "busy_by": "uuid_prefix", "since": seconds_ago}
    """
    data = request.json or {}
    force = bool(data.get("force", False))
    now = _time.time()
    with _BATCH_LOCK_MX:
        current_owner = _BATCH_LOCK["owner_id"]
        last_hb = _BATCH_LOCK["last_heartbeat"]
        idle_sec = now - last_hb if last_hb else 99999

        # 沒人佔、或上一個 tab 死了（心跳超時）、或使用者強搶
        can_take = (not current_owner) or (idle_sec > _BATCH_LOCK_STALE_SEC) or force
        if not can_take:
            return jsonify({
                "ok": False,
                "busy_by": current_owner[:8] if current_owner else "?",
                "since": int(now - _BATCH_LOCK["claimed_at"]),
                "idle_sec": int(idle_sec),
            }), 409

        new_id = str(_uuid.uuid4())
        was_taken_over = bool(current_owner and current_owner != new_id)
        _BATCH_LOCK["owner_id"] = new_id
        _BATCH_LOCK["claimed_at"] = now
        _BATCH_LOCK["last_heartbeat"] = now
    return jsonify({
        "ok": True,
        "owner_id": new_id,
        "is_new": not was_taken_over,
        "took_over": was_taken_over,
    })


@app.route("/api/batch/heartbeat", methods=["POST"])
def api_batch_heartbeat():
    """前端跑批次時、每幾秒呼叫一次維持鎖。
    body: {"owner_id": "..."}
    """
    data = request.json or {}
    owner = data.get("owner_id", "")
    with _BATCH_LOCK_MX:
        if _BATCH_LOCK["owner_id"] != owner:
            return jsonify({"ok": False, "error": "not owner (was taken over?)"}), 409
        _BATCH_LOCK["last_heartbeat"] = _time.time()
    return jsonify({"ok": True})


@app.route("/api/batch/release", methods=["POST"])
def api_batch_release():
    """跑完或按停止時釋放鎖。
    body: {"owner_id": "..."}
    """
    data = request.json or {}
    owner = data.get("owner_id", "")
    with _BATCH_LOCK_MX:
        if _BATCH_LOCK["owner_id"] == owner:
            _BATCH_LOCK["owner_id"] = None
            _BATCH_LOCK["claimed_at"] = 0
            _BATCH_LOCK["last_heartbeat"] = 0
            return jsonify({"ok": True, "released": True})
    return jsonify({"ok": True, "released": False, "msg": "wasn't owner"})


@app.route("/api/batch/status")
def api_batch_status():
    """查目前鎖狀態（debug 用）。"""
    now = _time.time()
    with _BATCH_LOCK_MX:
        return jsonify({
            "owner_id": _BATCH_LOCK["owner_id"][:8] if _BATCH_LOCK["owner_id"] else None,
            "since": int(now - _BATCH_LOCK["claimed_at"]) if _BATCH_LOCK["claimed_at"] else None,
            "idle_sec": int(now - _BATCH_LOCK["last_heartbeat"]) if _BATCH_LOCK["last_heartbeat"] else None,
            "stale_threshold": _BATCH_LOCK_STALE_SEC,
        })


# v67 — 批次完整狀態暫存（不小心停掉可以還原所有設定）
_BATCH_LAST_STATE_FILE = VISION_DIR / "_last_batch_state.json"


@app.route("/api/batch/last_state/save", methods=["POST"])
def api_batch_last_state_save():
    """前端跑批次前 dump 完整狀態。body = 任意 JSON。"""
    data = request.json
    if data is None:
        return jsonify({"ok": False, "error": "empty body"}), 400
    try:
        # 加伺服器時戳
        import datetime as _dt
        data["_saved_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        _BATCH_LAST_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"write fail: {e}"}), 500
    return jsonify({"ok": True})


@app.route("/api/batch/last_state/load")
def api_batch_last_state_load():
    """前端開生圖 modal 時呼叫、有就回完整狀態。"""
    if not _BATCH_LAST_STATE_FILE.exists():
        return jsonify({"ok": True, "data": None})
    try:
        data = json.loads(_BATCH_LAST_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"read fail: {e}"}), 500
    return jsonify({"ok": True, "data": data})


@app.route("/api/vision/delete_inpaint", methods=["POST"])
def api_vision_delete_inpaint():
    """刪生圖 / inpaint 成品圖（硬碟檔 + jsonl 紀錄 + 記憶體歷史）

    body: {ref: "file:vision_sessions/<sid>/<hash>.<ext>"
            或: "file:vision_sessions/_generated/<hash>.png" (A 方案的生圖)}
    """
    data = request.json or {}
    ref = data.get("ref", "")
    if not ref.startswith("file:"):
        return jsonify({"ok": False, "error": "bad ref"}), 400
    rel = ref[5:]
    if ".." in rel:
        return jsonify({"ok": False, "error": "traversal"}), 403
    # rel 形如 vision_sessions/<sid>/<hash>.<ext> 或 vision_sessions/_generated/<hash>.png
    parts = rel.split("/")
    if len(parts) < 3 or parts[0] != "vision_sessions":
        return jsonify({"ok": False, "error": "bad path"}), 400
    is_generated = parts[1] == "_generated"
    sid = None if is_generated else parts[1]

    img_path = _resolve_ref_path(rel)
    # v57: 同步加進黑名單、避免 F5 後重新算
    _mark_deleted(ref)
    # 1. 刪硬碟檔
    deleted_file = False
    if img_path.exists() and img_path.is_file():
        try:
            img_path.unlink()
            deleted_file = True
        except Exception as e:
            return jsonify({"ok": False, "error": f"delete file fail: {e}"}), 500

    # 2. 從所有 session 的記憶體 / jsonl 移除含這個 ref 的訊息
    #    生圖 (is_generated=True) 不知道屬於哪個 session、掃全部
    #    inpaint 只掃對應 sid
    removed_msgs = 0
    jsonl_removed = 0
    sessions_to_scan = list(VISION_HISTORY.keys()) if is_generated else ([sid] if sid in VISION_HISTORY else [])

    # 同時也掃硬碟所有 jsonl（防 RAM 沒載入的 session）
    if is_generated:
        from os import listdir
        try:
            for fn in listdir(VISION_DIR):
                if fn.endswith(".jsonl"):
                    s = fn[:-6]
                    if s not in sessions_to_scan:
                        sessions_to_scan.append(s)
        except Exception:
            pass

    # 訊息「包含 ref 字串」就視為要刪
    # 同時也要包含「微調後重新生成」標題段（前後幾個字一起清）
    def _msg_contains_ref(content):
        if not isinstance(content, str):
            return False
        return ref in content

    for s in sessions_to_scan:
        # 記憶體
        if s in VISION_HISTORY:
            new_hist = []
            for m in VISION_HISTORY[s]:
                if _msg_contains_ref(m.get("content", "")):
                    removed_msgs += 1
                    continue
                new_hist.append(m)
            VISION_HISTORY[s] = new_hist
        # jsonl
        session_file = _vision_session_file(s)
        if session_file.exists():
            try:
                lines = session_file.read_text(encoding="utf-8").splitlines()
                kept = []
                for line in lines:
                    try:
                        j = json.loads(line)
                        if _msg_contains_ref(j.get("content", "")):
                            jsonl_removed += 1
                            continue
                    except Exception:
                        pass
                    kept.append(line)
                session_file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            except Exception as e:
                print(f"[vision] rewrite jsonl fail {s}: {e}")

    return jsonify({
        "ok": True,
        "deleted_file": deleted_file,
        "removed_msgs": removed_msgs,
        "jsonl_removed": jsonl_removed,
        "is_generated": is_generated,
    })


@app.route("/api/vision/sessions")
def api_vision_sessions():
    """列出所有 vision sessions（含 title / 訊息數）"""
    result = []
    for sid, msgs in VISION_HISTORY.items():
        # 找第一則 user text 當 title
        title = "(空對話)"
        for m in msgs:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "text":
                            t = c.get("text", "").strip()
                            # 抽掉「搜尋結果」前綴
                            if "我的問題：" in t:
                                t = t.split("我的問題：", 1)[1].strip()
                            title = (t[:40] + "…") if len(t) > 40 else t
                            break
                elif isinstance(content, str):
                    title = (content[:40] + "…") if len(content) > 40 else content
                if title != "(空對話)":
                    break
        result.append({
            "session_id": sid,
            "title": title,
            "messages": len(msgs),
        })
    result.sort(key=lambda x: -x["messages"])  # 訊息多的排前
    return jsonify({"ok": True, "sessions": result})


@app.route("/api/vision/delete", methods=["POST"])
def api_vision_delete():
    import re, requests
    data = request.json or {}
    sid = data.get("session_id", "")
    if sid in VISION_HISTORY:
        # Extract all image prompts from the session history before deleting
        prompts_to_delete = []
        for msg in VISION_HISTORY[sid]:
            if msg.get("role") == "assistant" and "content" in msg:
                content = str(msg["content"])
                # Extract prompts from markdown: ![image](/api/vision/generate?prompt=X)
                matches = re.findall(r'/api/vision/generate\?prompt=([^)\s]+)', content)
                for match in matches:
                    prompts_to_delete.append(match)
                    
        # Remove from memory and disk
        VISION_HISTORY.pop(sid, None)
        try:
            f = _vision_session_file(sid)
            if f.exists():
                f.unlink()
        except Exception as e:
            print(f"[vision] delete jsonl fail: {e}")
        try:
            img_dir = VISION_DIR / sid
            if img_dir.exists() and img_dir.is_dir():
                import shutil
                shutil.rmtree(img_dir)
        except Exception as e:
            print(f"[vision] delete imgs fail: {e}")
            
        # Tell the proxy to delete these images from RAM
        for p in prompts_to_delete:
            try:
                import urllib.parse
                decoded_prompt = urllib.parse.unquote(p)
                requests.post("http://127.0.0.1:8003/delete_image", json={"prompt": decoded_prompt}, timeout=1.0)
            except Exception as e:
                print(f"[vision] RAM cleanup fail for {p}: {e}")
                
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "session 不存在"}), 404

# ===== SDXL 中英字典（hybrid 翻譯：字典優先、未命中才走 35B / 顯示原文）=====
SDXL_DICT_DIR = Path("C:/Users/pjunm/OneDrive/Desktop/hermes/_claude_workspace/shared_assets/sdxl_dict")
_SDXL_ZH2EN = {}  # {"蘿莉": "loli", ...}
_SDXL_EN2ZH = {}  # {"loli": "蘿莉", ...}

def _load_sdxl_dicts():
    """啟動時載入字典、之後 in-memory 查表。"""
    global _SDXL_ZH2EN, _SDXL_EN2ZH
    try:
        zh_path = SDXL_DICT_DIR / "zh_to_en.json"
        en_path = SDXL_DICT_DIR / "en_to_zh.json"
        if zh_path.exists():
            raw = json.loads(zh_path.read_text(encoding="utf-8"))
            _SDXL_ZH2EN = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}
        if en_path.exists():
            raw = json.loads(en_path.read_text(encoding="utf-8"))
            _SDXL_EN2ZH = {k.lower(): v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}
        print(f"[sdxl_dict] loaded zh2en={len(_SDXL_ZH2EN)} en2zh={len(_SDXL_EN2ZH)}")
    except Exception as e:
        print(f"[sdxl_dict] load failed: {e}")

_load_sdxl_dicts()


def _split_zh_tokens(text):
    """切中文輸入。用 、 ， , 空格 . 分隔、保留多字詞。"""
    import re as _re
    parts = _re.split(r"[、,，\s\.;；]+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_en_tags(text):
    """切英文 SDXL prompt。用逗號分隔。"""
    return [t.strip().lower() for t in text.split(",") if t.strip()]


def _has_subject_tag(en_tags):
    """檢查 en tag list 是否已包含主體 tag（1girl/1boy/scenery/still life/...）"""
    subj_keywords = ("1girl", "1boy", "1woman", "1man", "1cat", "1dog", "1bird",
                     "1horse", "2girls", "2boys", "3girls", "3boys",
                     "scenery", "still life", "architecture", "abstract",
                     "multiple people", "couple", "family", "crowd")
    joined = ", ".join(en_tags)
    return any(k in joined for k in subj_keywords)


def _add_default_weights(en_tags_str):
    """把每個 tag 包成 (tag:1.0)、讓使用者看到並可改。

    跳過：已有權重的 (xxx:N)、主體 tag（1girl/solo 等）、未命中標記 (?xxx)、空字串。
    """
    import re as _re
    if not en_tags_str:
        return en_tags_str
    # 主體 / 元 tag 不加權重（保持原樣、後端會強制套權重）
    SKIP = {"1girl", "1boy", "2girls", "3girls", "2boys", "3boys", "1woman", "1man",
            "1cat", "1dog", "1bird", "1horse", "solo",
            "scenery", "still life", "architecture", "abstract",
            "no humans", "multiple people", "couple", "family", "crowd"}
    out = []
    for raw in en_tags_str.split(","):
        tag = raw.strip()
        if not tag:
            continue
        # 已有權重 (xxx:N) → 保留原樣
        if tag.startswith("(") and tag.endswith(")") and ":" in tag:
            out.append(tag)
            continue
        # 未命中標記 (?xxx) → 保留原樣
        if tag.startswith("(?") and tag.endswith(")"):
            out.append(tag)
            continue
        # 主體 tag → 不加（後端會強制套權重）
        if tag.lower() in SKIP:
            out.append(tag)
            continue
        # 一般 tag → 包成 (tag:1.0)
        out.append(f"({tag}:1.0)")
    return ", ".join(out)


def _dict_translate_zh2en(text):
    """中→英 hybrid：盡量用字典、未命中的 token 收集起來。

    回傳: {"en": "拼好的英文", "missed": ["未命中的中文..."], "hit_count": N, "total": M}
    """
    tokens = _split_zh_tokens(text)
    en_parts = []
    missed = []
    for tok in tokens:
        if tok in _SDXL_ZH2EN:
            en_parts.append(_SDXL_ZH2EN[tok])
        else:
            # 嘗試去掉「的」「了」尾巴再查
            stripped = tok.rstrip("的了哦呢喔啊呀嗎吧")
            if stripped and stripped in _SDXL_ZH2EN:
                en_parts.append(_SDXL_ZH2EN[stripped])
            else:
                missed.append(tok)
    return {
        "en": ", ".join(en_parts),
        "missed": missed,
        "hit_count": len(en_parts),
        "total": len(tokens),
    }


def _dict_translate_en2zh(text):
    """英→中 hybrid：字典命中翻中文、未命中原文擺著。

    回傳: {"zh": "拼好的中文", "missed": [...英文..], "hit_count": N, "total": M}
    """
    tags = _split_en_tags(text)
    zh_parts = []
    missed = []
    # 過濾掉 quality / score 系列（給後端加的、不算翻譯目標）
    skip_quality = ("score_9", "score_8_up", "score_7_up", "score_6_up",
                    "raw photo", "masterpiece", "best quality", "highly detailed",
                    "8k", "ultra detailed", "photograph", "professional photography")
    for tag in tags:
        if any(q in tag for q in skip_quality):
            continue
        if tag in _SDXL_EN2ZH:
            zh_parts.append(_SDXL_EN2ZH[tag])
        else:
            zh_parts.append(tag)  # 未命中保留英文、讓使用者勉強看
            missed.append(tag)
    return {
        "zh": "、".join(zh_parts),
        "missed": missed,
        "hit_count": len(tags) - len(missed),
        "total": len(tags),
    }


@app.route("/api/vision/reload_dict", methods=["POST"])
def api_vision_reload_dict():
    """改完字典 JSON 後可以呼叫這個 hot reload、不用重啟 Flask。"""
    _load_sdxl_dicts()
    return jsonify({"ok": True, "zh2en": len(_SDXL_ZH2EN), "en2zh": len(_SDXL_EN2ZH)})


@app.route("/api/vision/translate_prompt", methods=["POST"])
def api_vision_translate_prompt():
    """中文白話 → SDXL/Pony tag 英文 prompt（hybrid：字典優先、未命中走 35B）。

    body: {
      "text": "長棕髮的女生、微笑、巴黎鐵塔下",
      "fallback": "dict_only" | "with_llm"   # 預設 "with_llm"
    }
    回傳: {
      "ok": true,
      "english": "1girl, solo, long brown hair, smiling, ...",
      "dict_hit": 3, "dict_total": 4,
      "missed_zh": ["巴黎鐵塔"],
      "via": "dict" | "dict+llm" | "llm"
    }
    """
    data = request.json or {}
    text = (data.get("text") or "").strip()
    fallback = (data.get("fallback") or "with_llm").lower()
    is_negative = bool(data.get("is_negative", False))   # True = 負面、不補 1girl/solo
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400

    # === Stage 1: 字典翻 ===
    d = _dict_translate_zh2en(text)
    dict_en = d["en"]
    missed = d["missed"]
    hit = d["hit_count"]
    total = d["total"]

    def _ensure_subject(en_str):
        """過去會強塞 1girl, solo 防扭曲、現在拿掉、原樣回傳。
        使用者自己用中文欄打主體 / 後端規則裡的 (solo:1.7) 已有作用。"""
        return en_str

    # === Stage 2a: 全部命中 → 直接回 ===
    if not missed:
        final = _add_default_weights(_ensure_subject(dict_en))
        return jsonify({
            "ok": True, "english": final,
            "dict_hit": hit, "dict_total": total, "missed_zh": [],
            "via": "dict",
        })

    # === Stage 2b: 有未命中、但使用者選 dict_only → 不走 35B、回半成品 ===
    if fallback == "dict_only":
        # 把未命中的中文以括號標記附加（讓使用者看到、自己補）
        marked = dict_en
        if marked:
            marked += ", " + ", ".join(f"(?{m})" for m in missed)
        else:
            marked = ", ".join(f"(?{m})" for m in missed)
        return jsonify({
            "ok": True, "english": _add_default_weights(_ensure_subject(marked)),
            "dict_hit": hit, "dict_total": total, "missed_zh": missed,
            "via": "dict",
            "warning": f"{len(missed)} 個詞字典沒收（已用 (?xxx) 標出）。要 35B 補翻請按「補翻未命中詞」",
        })

    # === Stage 3: 有未命中 → 只送「未命中的詞」給 35B、字典命中部分保留 ===
    # 這樣 35B 處理少量詞、thinking 短、5-10 秒就回；字典命中保證一致
    missed_zh_joined = "、".join(missed)
    sys = (
        "You are an SDXL/Pony tag translator. The user gives ONLY 1-5 Chinese concepts "
        "(separated by 、 or comma). For EACH concept, output 1-3 concrete English SDXL tags. "
        "Rules:\n"
        "1. Output a single line of comma-separated English tags. No subject tags (1girl/scenery)、"
        "no quality tags (masterpiece/8k/score_9)、no negatives.\n"
        "2. Just translate the concepts faithfully. Don't add concepts not in the input.\n"
        "3. Comma-separated. No prose sentences. No preamble. No quotes. No markdown.\n\n"
        "Examples:\n"
        "Input: 巴黎鐵塔背景、採光好\n"
        "Output: eiffel tower background, well lit, bright lighting\n\n"
        "Input: 北極熊\n"
        "Output: polar bear, white fur, arctic\n\n"
        "Input: 楓葉、秋天\n"
        "Output: maple leaves, autumn, fall foliage"
    )
    payload = {
        "model": "any",
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": missed_zh_joined},   # ← 只送未命中的詞、不是整段
        ],
        "max_tokens": 2048,  # 只翻幾個詞、不需 4096
        "stream": False,
        "temperature": 0.3,
    }
    try:
        base_url, auth = get_current_server()
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json=payload, timeout=120,
            headers={"Authorization": f"Bearer {auth}"},
        )
        if not r.ok:
            return jsonify({"ok": False, "error": f"LLM {r.status_code}: {r.text[:200]}"}), 500
        raw = r.json()["choices"][0]["message"]["content"]
        import re as _re
        out = raw

        # 1. 切掉 </think> 之前的內容（含完整 <think>...</think> 或半截 thinking 後接 </think>）
        if "</think>" in out:
            out = out.split("</think>", 1)[1]
        # 2. 容錯：若只有開 tag 沒閉、用 regex 試
        out = _re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", out, flags=_re.DOTALL)
        # 3. 去 markdown 包裝
        out = out.strip().strip("`\"' \n").strip()
        # 4. 多話時抓「最後一行有逗號的」當答案
        if "\n" in out:
            lines = [ln.strip() for ln in out.split("\n") if "," in ln and ln.strip()]
            if lines:
                out = lines[-1].strip("`\"' ")

        # 抽不到內容、35B 失敗 → fallback 回字典半成品
        if not out:
            marked = dict_en
            if marked:
                marked += ", " + ", ".join(f"(?{m})" for m in missed)
            else:
                marked = ", ".join(f"(?{m})" for m in missed)
            preview = raw[:200].replace("\n", " ⏎ ")
            return jsonify({
                "ok": True,
                "english": _add_default_weights(_ensure_subject(marked)),
                "dict_hit": hit, "dict_total": total, "missed_zh": missed,
                "via": "dict",
                "warning": f"35B 翻譯失敗、已用字典命中部分+(?未命中)。35B 原始: {preview}",
            }), 200
        # 合併：字典命中部分 + 35B 翻的未命中部分
        if dict_en and out:
            merged = dict_en + ", " + out
        else:
            merged = dict_en or out
        return jsonify({
            "ok": True, "english": _add_default_weights(_ensure_subject(merged)),
            "dict_hit": hit, "dict_total": total, "missed_zh": missed,
            "llm_translated": out,    # debug 用、看 35B 翻了什麼
            "via": "dict+llm",
        })
    except requests.Timeout:
        # timeout 也 fallback 回字典半成品
        marked = dict_en
        if marked:
            marked += ", " + ", ".join(f"(?{m})" for m in missed)
        else:
            marked = ", ".join(f"(?{m})" for m in missed)
        return jsonify({
            "ok": True,
            "english": _add_default_weights(_ensure_subject(marked)),
            "dict_hit": hit, "dict_total": total, "missed_zh": missed,
            "via": "dict",
            "warning": "35B 翻譯超時 120 秒、已用字典命中部分+(?未命中)。建議改用「僅字典」模式或自己補字典。",
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/vision/translate_en2zh", methods=["POST"])
def api_vision_translate_en2zh():
    """英文 SDXL tag → 中文白話（hybrid：字典優先、未命中保留英文不阻塞）。

    body: {
      "text": "1girl, solo, long brown hair, smiling, outdoors",
      "fallback": "dict_only" | "with_llm"   # 預設 "dict_only"（不阻塞）
    }
    回傳: {"ok": true, "chinese": "一個女生、單人、長棕髮、微笑、戶外", "missed_en": [...], "via": "..."}
    """
    data = request.json or {}
    text = (data.get("text") or "").strip()
    fallback = (data.get("fallback") or "dict_only").lower()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400

    # === 字典翻 ===
    d = _dict_translate_en2zh(text)

    # 預設模式：字典翻完直接回（未命中保留英文）→ 秒回、不阻塞
    if fallback == "dict_only":
        return jsonify({
            "ok": True, "chinese": d["zh"],
            "dict_hit": d["hit_count"], "dict_total": d["total"],
            "missed_en": d["missed"], "via": "dict",
        })

    # with_llm 模式：未命中比例 >0 才走 35B（命中率夠高就不浪費）
    if d["hit_count"] == d["total"] or not d["missed"]:
        return jsonify({
            "ok": True, "chinese": d["zh"],
            "dict_hit": d["hit_count"], "dict_total": d["total"],
            "missed_en": [], "via": "dict",
        })
    # 走 35B（原本邏輯）

    sys = (
        "You are an SDXL tag translator. The user gives a line of comma-separated English SDXL/Pony tags. "
        "You translate them into natural-sounding Traditional Chinese (zh-TW) so a non-English user can understand.\n"
        "Rules:\n"
        "1. Keep it short and natural. Use 、 or commas between concepts.\n"
        "2. Translate each tag faithfully — don't add concepts that aren't in the English.\n"
        "3. Skip pure technical quality tags (masterpiece, 8k, best quality, score_9, RAW photo) silently.\n"
        "4. For subject tags: 1girl→「一個女生」, 1boy→「一個男生」, solo→「單人」(can merge with 1girl), "
        "scenery, no humans→「風景、沒有人」, 1cat, solo→「一隻貓」.\n"
        "5. Output the Chinese line ONLY. No preamble, no explanation, no markdown, no quotes, no English in output.\n\n"
        "Examples:\n"
        "Input: 1girl, solo, long brown hair, smiling, looking at viewer, outdoors, natural lighting\n"
        "Output: 一個女生、長棕髮、微笑、看向鏡頭、戶外、自然光\n\n"
        "Input: scenery, no humans, beach, sunset, golden hour, ocean waves\n"
        "Output: 海邊風景、夕陽、黃金時刻、海浪、沒有人\n\n"
        "Input: 1cat, solo, orange tabby cat, sleeping, by window, warm sunlight\n"
        "Output: 一隻橘貓、睡覺、窗邊、溫暖陽光"
    )
    payload = {
        "model": "any",
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": text},
        ],
        "max_tokens": 4096,
        "stream": False,
        "temperature": 0.3,
    }
    try:
        base_url, auth = get_current_server()
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json=payload, timeout=120,
            headers={"Authorization": f"Bearer {auth}"},
        )
        if not r.ok:
            return jsonify({"ok": False, "error": f"LLM {r.status_code}: {r.text[:200]}"}), 500
        raw = r.json()["choices"][0]["message"]["content"]
        import re as _re
        out = raw
        if "</think>" in out:
            out = out.split("</think>", 1)[1]
        out = _re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", out, flags=_re.DOTALL)
        out = out.strip().strip("`\"' \n").strip()
        # 多話時抓「最後一行含中文」當答案
        if "\n" in out:
            lines = [ln.strip() for ln in out.split("\n") if ln.strip() and _re.search(r"[一-鿿]", ln)]
            if lines:
                out = lines[-1].strip("`\"' ")
        if not out:
            # 35B 失敗 → fallback 字典翻譯結果
            return jsonify({
                "ok": True, "chinese": d["zh"],
                "dict_hit": d["hit_count"], "dict_total": d["total"],
                "missed_en": d["missed"], "via": "dict",
                "warning": "35B 翻譯失敗、已用字典結果（未命中詞保留英文）",
            }), 200
        return jsonify({
            "ok": True, "chinese": out,
            "dict_hit": d["hit_count"], "dict_total": d["total"],
            "missed_en": d["missed"], "via": "dict+llm" if d["hit_count"] > 0 else "llm",
        })
    except requests.Timeout:
        # timeout → fallback 字典
        return jsonify({
            "ok": True, "chinese": d["zh"],
            "dict_hit": d["hit_count"], "dict_total": d["total"],
            "missed_en": d["missed"], "via": "dict",
            "warning": "35B 超時、已用字典結果（未命中詞保留英文）",
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/vision/interrupt", methods=["POST"])
def api_vision_interrupt():
    """中斷 ComfyUI 目前的 job + 清空 queue。"""
    import requests as _req
    try:
        r = _req.post("http://127.0.0.1:8003/interrupt", timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/vision/comfy_status")
def api_vision_comfy_status():
    """前端問 ComfyUI 閒置與否。"""
    import requests as _req
    try:
        r = _req.get("http://127.0.0.1:8003/status", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "busy": False, "error": str(e)}), 200


@app.route("/api/vision/persist_hash")
def api_vision_persist_hash():
    """給前端算「這組 prompt 之後會存哪」、讓前端直接組 /api/vision/img?ref=... URL。

    這樣 F5 後不會再打 /api/vision/generate、直接走 /api/vision/img 從硬碟讀。

    query: ?prompt=...&style=...&seed=N&cfg=N&steps=N&sampler=X&scheduler=X&save_format=jpeg92
    回傳: {"ok": true, "ref": "file:vision_sessions/_generated/<hash>.<ext>", "hash": "..."}
    """
    prompt = request.args.get("prompt", "")
    style = request.args.get("style", "realistic")
    seed = request.args.get("seed", "")
    cfg = request.args.get("cfg", "")
    steps = request.args.get("steps", "")
    sampler = request.args.get("sampler", "")
    scheduler = request.args.get("scheduler", "")
    size = request.args.get("size", "")
    rules = request.args.get("rules", "")
    user_neg = request.args.get("user_neg", "")
    save_format = request.args.get("save_format", "jpeg92")
    if not prompt:
        return jsonify({"ok": False, "error": "empty prompt"}), 400
    # save_format 也納入 key、避免「同 prompt 不同格式」hash 撞檔
    disk_key_extras = f"{cfg}|{steps}|{sampler}|{scheduler}|{size}|{rules}|{user_neg}|{save_format}"
    disk_path = _gen_persist_path(prompt, style, seed + "|" + disk_key_extras, save_format)
    # 一律用相容格式 vision_sessions/_generated/<filename>
    # _resolve_ref_path 會自動 redirect 到實際位置（Z 槽或本機 fallback）
    rel = f"vision_sessions/_generated/{disk_path.name}"
    return jsonify({
        "ok": True,
        "ref": f"file:{rel}",
        "filename": disk_path.name,
    })


# save_format → 副檔名對應、跟前端 dropdown 一致
_SAVE_FMT_EXT = {"png": "png", "jpeg92": "jpg", "jpeg98": "jpg", "webp95": "webp"}


def _persist_with_format(img_bytes, disk_path, save_format):
    """把 PNG bytes 依 save_format 轉檔寫到 disk_path、回 (寫回客戶端的 bytes, mimetype)。

    - 失敗 fallback 寫原 PNG bytes、回原 mimetype。
    """
    fmt_map = {
        "png":    {"pil_fmt": None,   "quality": None, "mime": "image/png"},
        "jpeg92": {"pil_fmt": "JPEG", "quality": 92,   "mime": "image/jpeg"},
        "jpeg98": {"pil_fmt": "JPEG", "quality": 98,   "mime": "image/jpeg"},
        "webp95": {"pil_fmt": "WEBP", "quality": 95,   "mime": "image/webp"},
    }
    cfg = fmt_map.get((save_format or "jpeg92").lower(), fmt_map["jpeg92"])
    try:
        if cfg["pil_fmt"] is None:
            disk_path.write_bytes(img_bytes)
            return img_bytes, cfg["mime"]
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(img_bytes))
        if cfg["pil_fmt"] == "JPEG" and im.mode in ("RGBA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3] if im.mode == "RGBA" else None)
            im = bg
        elif cfg["pil_fmt"] == "JPEG" and im.mode != "RGB":
            im = im.convert("RGB")
        im.save(disk_path, format=cfg["pil_fmt"], quality=cfg["quality"], optimize=True)
        # 回客戶端用實際存的 bytes（保證跟 F5 重讀一致）
        out = disk_path.read_bytes()
        return out, cfg["mime"]
    except Exception as e:
        print(f"[persist_with_format] {cfg['pil_fmt']} 失敗、fallback PNG：{e}")
        try:
            fallback_path = disk_path.with_suffix(".png")
            fallback_path.write_bytes(img_bytes)
        except Exception as e2:
            print(f"[persist_with_format] fallback PNG 也失敗：{e2}")
        return img_bytes, "image/png"

def _gen_persist_path(prompt, style, seed="", save_format="jpeg92"):
    """生圖永久存檔路徑：prompt+style+seed 的 hash 當檔名。

    同一組 prompt+style+seed → 永遠同張圖（重整網頁也救得回來）。
    seed 變 → hash 變 → 重新算（讓使用者「重新生成」按鈕能拿新圖）。
    存檔位置 Z:\相簿\ai_generated（單一資料夾、不再用 _generated 子目錄）
    save_format → 副檔名（png / jpg / webp）
    """
    import hashlib
    key = f"{style}|{prompt}|{seed}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    ext = _SAVE_FMT_EXT.get((save_format or "jpeg92").lower(), "jpg")
    return _ai_gen_dir() / f"{h}.{ext}"


@app.route("/api/vision/generate")
def api_vision_generate():
    import requests, urllib.parse, traceback
    from flask import Response
    prompt = request.args.get("prompt", "")
    style = request.args.get("style", "realistic")
    seed = request.args.get("seed", "")  # 使用者按重新生成時遞增、讓硬碟 cache 不命中
    cfg = request.args.get("cfg", "")
    steps = request.args.get("steps", "")
    sampler = request.args.get("sampler", "")
    scheduler = request.args.get("scheduler", "")
    size = request.args.get("size", "")
    rules = request.args.get("rules", "")  # 9-char 0/1 字串：solo/lighting/details/quality/noise/style/adetailer/proportion/hand_strong
    user_neg = request.args.get("user_neg", "")  # 使用者自訂額外負面提示詞
    save_format = request.args.get("save_format", "jpeg92")  # 輸出格式：png / jpeg92 / jpeg98 / webp95
    preview = request.args.get("preview", "")  # "1" = 試生圖、不寫硬碟、不寫 jsonl

    if not prompt:
        return jsonify({"ok": False, "error": "Empty prompt"}), 400

    # 1. 硬碟有 → 秒回（同 prompt+style+seed+採樣參數+規則+user_neg+save_format 永遠同張圖）
    #    preview 模式也讀硬碟 cache（譬如先 preview 確認 → 按確定輸出 → 再 generate 就秒回）
    disk_key_extras = f"{cfg}|{steps}|{sampler}|{scheduler}|{size}|{rules}|{user_neg}|{save_format}"
    disk_path = _gen_persist_path(prompt, style, seed + "|" + disk_key_extras, save_format)
    # v57: 對應 file:ref 形式（跟 /api/vision/img 用同樣 key、前端標哪邊都認得）
    deleted_key = f"file:vision_sessions/_generated/{disk_path.name}"
    if disk_path.exists():
        # 副檔名決定 mimetype
        mt_map = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
        mt = mt_map.get(disk_path.suffix.lstrip(".").lower(), "image/png")
        return Response(disk_path.read_bytes(), mimetype=mt)

    # v57: 黑名單檢查、避免「Z 槽手刪 → 重抓 → 又算一次」死循環
    if _is_deleted(deleted_key):
        return "gone (deleted by user)", 410

    # 2. 沒有 → call comfy_proxy 算（timeout 拉長到 120 秒、避免前端誤判 503 觸發重試）
    try:
        url = f"http://127.0.0.1:8003/generate?prompt={urllib.parse.quote(prompt)}&style={urllib.parse.quote(style)}"
        if seed:    url += f"&seed={urllib.parse.quote(seed)}"
        if cfg:     url += f"&cfg={urllib.parse.quote(cfg)}"
        if steps:   url += f"&steps={urllib.parse.quote(steps)}"
        if sampler: url += f"&sampler={urllib.parse.quote(sampler)}"
        if scheduler: url += f"&scheduler={urllib.parse.quote(scheduler)}"
        if size:    url += f"&size={urllib.parse.quote(size)}"
        if rules:   url += f"&rules={urllib.parse.quote(rules)}"
        if user_neg: url += f"&user_neg={urllib.parse.quote(user_neg)}"
        if preview: url += f"&preview={urllib.parse.quote(preview)}"
        res = requests.get(url, timeout=120)
        # comfy_proxy 503 = 正在算（前一個請求還沒完）
        if res.status_code == 503:
            return jsonify({"ok": False, "error": "generating"}), 503
        # 算好 → 依 save_format 轉檔再寫、preview 模式跳過（純記憶體回）
        out_bytes = res.content
        out_mime = res.headers.get('content-type', 'image/png')
        if res.status_code == 200 and out_bytes and not preview:
            out_bytes, out_mime = _persist_with_format(out_bytes, disk_path, save_format)
        return Response(out_bytes, mimetype=out_mime)
    except requests.exceptions.ReadTimeout:
        # Client connection freed. Image is still generating in background.
        return jsonify({"ok": False, "error": "generating"}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/vision/img2img")
def api_vision_img2img():
    import requests, urllib.parse, base64
    from flask import Response
    prompt = request.args.get("prompt", "")
    sid = request.args.get("sid", "")
    denoise = request.args.get("denoise", "0.5")
    
    if not prompt or not sid:
        return jsonify({"ok": False, "error": "Missing prompt or sid"}), 400
        
    # 尋找該 session 最後一張上傳的圖片 (保存在 RAM 的 history 中)
    history = VISION_HISTORY.get(sid, [])
    img_b64 = None
    for msg in reversed(history):
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "image_url":
                    img_b64 = block["image_url"]["url"]
                    break
        if img_b64:
            break
            
    if not img_b64:
        return jsonify({"ok": False, "error": "No uploaded image found in this session"}), 400
        
    # 如果是儲存在硬碟中的歷史圖片，需要先讀取為 base64
    if img_b64.startswith("file:"):
        img_b64 = _vision_load_image(img_b64)
        if not img_b64:
            return jsonify({"ok": False, "error": "Failed to load local image file"}), 500
            
    # 解碼 base64
    if "," in img_b64:
        img_b64 = img_b64.split(",")[1]
    img_bytes = base64.b64decode(img_b64)
    
    try:
        files = {'image': ('upload.png', img_bytes)}
        data = {'prompt': urllib.parse.unquote(prompt), 'denoise': str(denoise)}
        res = requests.post("http://127.0.0.1:8003/img2img", files=files, data=data, timeout=1.0)
        
        if res.status_code == 503:
            return jsonify({"ok": False, "error": "generating"}), 503
            
        return Response(res.content, mimetype=res.headers.get('content-type', 'image/png'))
    except requests.exceptions.ReadTimeout:
        return jsonify({"ok": False, "error": "generating"}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/vision/get_mask", methods=["GET"])
def api_vision_get_mask():
    import requests, urllib.parse, base64
    from flask import Response
    target = request.args.get("target", "")
    sid = request.args.get("sid", "")
    
    if not target or not sid:
        return jsonify({"ok": False, "error": "Missing target or sid"}), 400
        
    history = VISION_HISTORY.get(sid, [])
    img_b64 = None
    for msg in reversed(history):
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "image_url":
                    img_b64 = block["image_url"]["url"]
                    break
        if img_b64:
            break
            
    if not img_b64:
        return jsonify({"ok": False, "error": "No uploaded image found in this session"}), 400
        
    if img_b64.startswith("file:"):
        img_b64 = _vision_load_image(img_b64)
        if not img_b64:
            return jsonify({"ok": False, "error": "Failed to load local image file"}), 500
            
    if "," in img_b64:
        img_b64 = img_b64.split(",")[1]
    img_bytes = base64.b64decode(img_b64)
    
    try:
        files = {'image': ('upload.png', img_bytes)}
        data = {'prompt': urllib.parse.unquote(target)}
        res = requests.post("http://127.0.0.1:8003/get_mask", files=files, data=data, timeout=120.0)
        
        if res.status_code == 503:
            return jsonify({"ok": False, "error": "generating"}), 503
        elif res.status_code != 200:
            return jsonify({"ok": False, "error": f"Backend returned {res.status_code}"}), 500
            
        resp = Response(res.content, mimetype="image/png")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except requests.exceptions.ReadTimeout:
        return jsonify({"ok": False, "error": "generating"}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/vision/append_text", methods=["POST"])
def api_vision_append_text():
    """寫一條純文字訊息到 vision 歷史（前端臨時 status 也要持久化用）

    body: {session_id, role: 'user'|'assistant', text}
    """
    data = request.json or {}
    sid = data.get("session_id")
    role = data.get("role", "user")
    text = data.get("text", "")
    if not sid or not text:
        return jsonify({"ok": False, "error": "Missing params"}), 400
    if role not in ("user", "assistant"):
        return jsonify({"ok": False, "error": "bad role"}), 400
    msg = {"role": role, "content": text}
    VISION_HISTORY.setdefault(sid, []).append(msg)
    _vision_persist(sid, msg)
    return jsonify({"ok": True})


@app.route("/api/vision/append_inpaint", methods=["POST"])
def api_vision_append_inpaint():
    """局部改圖成品圖：寫 Z:\\相簿\\ai_generated\\<hash>.png（跟生圖共用），
    回傳 file:vision_sessions/_generated/<hash>.png ref（_resolve_ref_path 會走 Z 槽）。"""
    import re, hashlib
    data = request.json or {}
    sid = data.get("session_id")
    data_url = data.get("image_data_url")
    if not sid or not data_url:
        return jsonify({"ok": False, "error": "Missing params"}), 400

    m = re.match(r"data:image/(\w+);base64,(.+)", data_url, re.DOTALL)
    if not m:
        return jsonify({"ok": False, "error": "Bad data_url"}), 400
    ext, b64data = m.group(1), m.group(2)

    try:
        img_bytes = base64.b64decode(b64data)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Decode fail: {e}"}), 500

    h = hashlib.md5(b64data.encode("ascii")).hexdigest()[:24]
    target_dir = _ai_gen_dir()  # Z 槽優先、不行 fallback vision_sessions/_generated

    # 前端可指定格式：png / jpeg92 / jpeg98 / webp95
    save_format = (data.get("save_format") or "jpeg92").lower()
    final_ext = _SAVE_FMT_EXT.get(save_format, "jpg")
    img_path = target_dir / f"{h}.{final_ext}"

    if not img_path.exists():
        _persist_with_format(img_bytes, img_path, save_format)
        if not img_path.exists():
            # 完全失敗的最後 fallback
            return jsonify({"ok": False, "error": "Write fail"}), 500

    # 一律用相容格式 vision_sessions/_generated/<filename>、跟生圖一致
    ref = f"file:vision_sessions/_generated/{img_path.name}"

    # 順手把 inpaint 參數寫進 markdown alt text、F5 後前端 regex 解析、按鈕能顯示
    inpaint_prompt = (data.get("prompt") or "").strip()
    inpaint_denoise = (data.get("denoise") or "").strip()
    if inpaint_prompt:
        # alt 用 | 分隔 key=value、好 parse、避開常用標點
        import urllib.parse as _up
        alt = f"inpaint|prompt={_up.quote(inpaint_prompt, safe='')}|denoise={_up.quote(inpaint_denoise, safe='')}"
        content = f"![{alt}]({ref})"
    else:
        content = f"![image]({ref})"

    msg = {
        "role": "assistant",
        "content": content,
    }
    VISION_HISTORY.setdefault(sid, []).append(msg)
    _vision_persist(sid, msg)
    return jsonify({"ok": True})


@app.route("/api/vision/save_preview", methods=["POST"])
def api_vision_save_preview():
    """純把預覽圖存到 Z 槽、不寫對話框 / 不寫 jsonl / 不進 VISION_HISTORY。
    body: {"image_data_url": "data:image/png;base64,...", "save_format": "jpeg92"}
    回: {"ok": true, "path": "Z:\\相簿\\ai_generated\\<hash>.jpg"}
    """
    import re, hashlib
    data = request.json or {}
    data_url = data.get("image_data_url")
    if not data_url:
        return jsonify({"ok": False, "error": "Missing image_data_url"}), 400

    m = re.match(r"data:image/(\w+);base64,(.+)", data_url, re.DOTALL)
    if not m:
        return jsonify({"ok": False, "error": "Bad data_url"}), 400
    ext, b64data = m.group(1), m.group(2)

    try:
        img_bytes = base64.b64decode(b64data)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Decode fail: {e}"}), 500

    h = hashlib.md5(b64data.encode("ascii")).hexdigest()[:24]
    target_dir = _ai_gen_dir()
    save_format = (data.get("save_format") or "jpeg92").lower()
    final_ext = _SAVE_FMT_EXT.get(save_format, "jpg")
    img_path = target_dir / f"{h}.{final_ext}"

    if not img_path.exists():
        _persist_with_format(img_bytes, img_path, save_format)
        if not img_path.exists():
            return jsonify({"ok": False, "error": "Write fail"}), 500

    return jsonify({"ok": True, "path": str(img_path)})


# ============================================================
# 🏷️ TAG 預設檔（生圖 / 局部改圖 dropdown 狀態）
#   存在 gui/tag_presets/、所有設備（手機/平板/桌面）共用同一份
# ============================================================
TAG_PRESETS_DIR = Path(__file__).parent / "tag_presets"
TAG_PRESETS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_preset_name(name: str) -> str:
    """檔名清理：擋路徑跳脫、特殊字元、長度上限。回傳純檔名（不含副檔名）。"""
    import re
    if not name:
        return ""
    # 拔掉路徑分隔符、控制字元、Windows 不合法字元
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip().strip(".")
    # 擋 .. / 隱藏檔
    name = name.lstrip(".")
    if not name or name in (".", ".."):
        return ""
    return name[:80]


@app.route("/api/tag_preset/list")
def api_tag_preset_list():
    """列出所有 TAG 預設檔。
    回: {"ok": true, "items": [{"name": "...", "modal": "prompt-edit", "saved_at": "...", "size": 1234}, ...]}
    """
    items = []
    try:
        for p in sorted(TAG_PRESETS_DIR.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                items.append({
                    "name": p.stem,
                    "modal": data.get("modal", ""),
                    "saved_at": data.get("saved_at", ""),
                    "display_name": data.get("name", p.stem),
                    "size": p.stat().st_size,
                })
            except Exception:
                # 壞檔也列、讓使用者能刪
                items.append({"name": p.stem, "modal": "", "saved_at": "", "display_name": p.stem, "size": p.stat().st_size, "broken": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    # 新的在前
    items.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    return jsonify({"ok": True, "items": items})


@app.route("/api/tag_preset/save", methods=["POST"])
def api_tag_preset_save():
    """存一個 TAG 預設檔。
    body: { name: "...", modal: "prompt-edit"|"mask", tags: {...}, positive_prompt: "...", negative_prompt: "...", ... }
    回: {"ok": true, "name": "..."}
    """
    data = request.json or {}
    name = _safe_preset_name(data.get("name", ""))
    if not name:
        return jsonify({"ok": False, "error": "Invalid name"}), 400

    # 補上伺服器時戳（覆蓋客戶端送來的、確保正確）
    import datetime as _dt
    data["saved_at"] = data.get("saved_at") or _dt.datetime.now().isoformat(timespec="seconds")
    data["name"] = name

    path = TAG_PRESETS_DIR / f"{name}.json"
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Write fail: {e}"}), 500
    return jsonify({"ok": True, "name": name})


@app.route("/api/tag_preset/load/<name>")
def api_tag_preset_load(name):
    """讀一個 TAG 預設檔的完整內容。"""
    name = _safe_preset_name(name)
    if not name:
        return jsonify({"ok": False, "error": "Invalid name"}), 400
    path = TAG_PRESETS_DIR / f"{name}.json"
    if not path.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Read fail: {e}"}), 500
    return jsonify({"ok": True, "data": data})


@app.route("/api/tag_preset/delete", methods=["POST"])
def api_tag_preset_delete():
    """刪除一個 TAG 預設檔。
    body: {"name": "..."}
    """
    data = request.json or {}
    name = _safe_preset_name(data.get("name", ""))
    if not name:
        return jsonify({"ok": False, "error": "Invalid name"}), 400
    path = TAG_PRESETS_DIR / f"{name}.json"
    if not path.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404
    try:
        path.unlink()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Delete fail: {e}"}), 500
    return jsonify({"ok": True, "name": name})


@app.route("/api/vision/inpaint", methods=["POST"])
def api_vision_inpaint():
    import requests, urllib.parse, base64
    from flask import Response
    prompt = request.form.get("prompt", "")
    sid = request.form.get("sid", "")
    denoise = request.form.get("denoise", "0.9")
    mask_b64 = request.form.get("mask_b64", "")
    user_neg = request.form.get("negative", "")  # 使用者額外負面 prompt

    if not prompt or not sid or not mask_b64:
        return jsonify({"ok": False, "error": "Missing prompt, sid, or mask"}), 400

    history = VISION_HISTORY.get(sid, [])
    img_b64 = None
    for msg in reversed(history):
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "image_url":
                    img_b64 = block["image_url"]["url"]
                    break
        if img_b64:
            break

    # fallback: 對生成圖開局部改圖時，history 沒上傳圖、用前端送來的 image_b64
    if not img_b64:
        img_b64 = request.form.get("image_b64", "") or None

    if not img_b64:
        return jsonify({"ok": False, "error": "No uploaded image found in this session"}), 400
        
    if img_b64.startswith("file:"):
        img_b64 = _vision_load_image(img_b64)
        if not img_b64:
            return jsonify({"ok": False, "error": "Failed to load local image file"}), 500
            
    if "," in img_b64:
        img_b64 = img_b64.split(",")[1]
    img_bytes = base64.b64decode(img_b64)
    
    if "," in mask_b64:
        mask_b64 = mask_b64.split(",")[1]
    mask_bytes = base64.b64decode(mask_b64)
    
    try:
        files = {
            'image': ('upload.png', img_bytes),
            'mask': ('mask.png', mask_bytes)
        }
        data = {
            'prompt': urllib.parse.unquote(prompt),
            'denoise': str(denoise),
            'negative': user_neg,
        }
        res = requests.post("http://127.0.0.1:8003/inpaint", files=files, data=data, timeout=120.0)
        
        if res.status_code == 503:
            return jsonify({"ok": False, "error": "generating"}), 503
        elif res.status_code != 200:
            return jsonify({"ok": False, "error": f"Backend returned {res.status_code}"}), 500

        # 防呆：確認 comfy_proxy 真的回了合法 PNG（magic bytes \x89PNG\r\n\x1a\n）
        # 偶爾 comfy_proxy 會回 JSON error 或空 bytes、status 200 但內容壞
        body = res.content or b""
        if len(body) < 100 or not body.startswith(b"\x89PNG"):
            # 不是 PNG → 看是不是 JSON error
            err_msg = "ComfyUI 回的內容不是合法 PNG"
            try:
                j = res.json()
                if isinstance(j, dict) and j.get("error"):
                    err_msg = f"ComfyUI: {j['error']}"
            except Exception:
                # 不是 JSON、印前 80 byte 進 log 方便診斷
                print(f"[inpaint] bad response (size={len(body)}): {body[:80]!r}")
            return jsonify({"ok": False, "error": err_msg}), 500

        return Response(body, mimetype="image/png")
    except requests.exceptions.ReadTimeout:
        return jsonify({"ok": False, "error": "generating"}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/vision/clear_cache", methods=["POST"])
def api_vision_clear_cache():
    import requests
    try:
        res = requests.post("http://127.0.0.1:8003/clear_cache")
        return jsonify(res.json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/vision/delete_image", methods=["POST"])
def api_vision_delete_image():
    """刪生圖 (/api/vision/generate) 3 層：ComfyUI RAM + 硬碟 _generated/*.png + jsonl 中對應的 markdown 行。

    body: {"prompt": "<encoded prompt>", "style": "realistic", "sid": "..."}
      - prompt 是 URL-encoded（前端從 ?prompt=X 抓出來不解碼直接傳）
      - style / sid 可選
    """
    import requests as _req
    import urllib.parse, re as _re
    data = request.json or {}
    prompt_encoded = data.get("prompt", "")
    style = data.get("style", "realistic")
    sid = data.get("sid", "")

    if not prompt_encoded:
        return jsonify({"ok": False, "error": "empty prompt"}), 400

    # prompt 可能已解碼 / 已 URL-encode 都收
    try:
        prompt_decoded = urllib.parse.unquote(prompt_encoded)
    except Exception:
        prompt_decoded = prompt_encoded

    result = {"ram": False, "disk": False, "jsonl_removed": 0, "memory_removed": 0}

    # 1. ComfyUI RAM cache（用 cache_key = style|prompt 跟 comfy_proxy 一致）
    try:
        cache_key = f"{style}|{prompt_decoded}"
        r = _req.post("http://127.0.0.1:8003/delete_image",
                      json={"prompt": cache_key}, timeout=5)
        result["ram"] = bool(r.ok)
    except Exception as e:
        print(f"[delete_image] ram fail: {e}")

    # 2. 硬碟 _generated/<hash>.png
    try:
        disk_path = _gen_persist_path(prompt_decoded, style)
        if disk_path.exists():
            disk_path.unlink()
            result["disk"] = True
    except Exception as e:
        print(f"[delete_image] disk fail: {e}")

    # 3. 從 jsonl + 記憶體歷史移除「含這個 prompt 的 markdown 行」
    # markdown 形如 ![image](/api/vision/generate?prompt=ENCODED&style=Y)
    # 同時匹配 encoded 跟 decoded、避免錯過
    if sid:
        patterns = [
            urllib.parse.quote(prompt_decoded),     # 標準 encoded
            urllib.parse.quote(prompt_decoded, safe=""),  # 嚴格 encoded
            prompt_decoded,                          # 原始
            prompt_encoded,                          # 前端傳過來的
        ]
        def _matches(text):
            if not isinstance(text, str):
                return False
            if "/api/vision/generate" not in text and "/api/vision/img2img" not in text:
                return False
            return any(p and p in text for p in patterns)

        # 記憶體
        if sid in VISION_HISTORY:
            new_hist = []
            for m in VISION_HISTORY[sid]:
                c = m.get("content", "")
                if _matches(c):
                    result["memory_removed"] += 1
                    continue
                new_hist.append(m)
            VISION_HISTORY[sid] = new_hist

        # jsonl
        session_file = _vision_session_file(sid)
        if session_file.exists():
            try:
                lines = session_file.read_text(encoding="utf-8").splitlines()
                kept = []
                for line in lines:
                    try:
                        j = json.loads(line)
                        if _matches(j.get("content", "")):
                            result["jsonl_removed"] += 1
                            continue
                    except Exception:
                        pass
                    kept.append(line)
                session_file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            except Exception as e:
                print(f"[delete_image] jsonl fail {sid}: {e}")

    return jsonify({"ok": True, **result})


# ===== 心跳監控 - 沒人連線 60 秒自動 kill Claude =====
last_heartbeat = {"time": time.time()}


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    last_heartbeat["time"] = time.time()
    return jsonify({"ok": True})


def heartbeat_monitor():
    """背景執行緒：10 分鐘沒人開 GUI，停掉還在跑的背景 chat process（只殺自己 spawn 的 PID）"""
    while True:
        time.sleep(30)
        idle = time.time() - last_heartbeat["time"]
        if idle > 600:  # 10 分鐘
            # 只殺自己追蹤的 PID（CHAT_PROCS），絕不用 /IM claude.exe
            for sid, proc in list(CHAT_PROCS.items()):
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True)
                except Exception:
                    pass
            CHAT_PROCS.clear()
            last_heartbeat["time"] = time.time()


# ===== 🎞 Slideshow（Z 槽圖片瀏覽 + 播放）=====
SLIDESHOW_ROOT = Path(r"Z:\\")
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _slideshow_safe_path(rel):
    """把 client 傳的相對路徑轉成 Z 槽底下的絕對 path、擋目錄穿越。"""
    # 統一斜線 + 移除頭尾斜線
    rel = (rel or "").replace("\\", "/").strip("/")
    # 擋 .. 跟絕對路徑
    if ".." in rel.split("/") or (len(rel) >= 2 and rel[1] == ":"):
        return None
    target = (SLIDESHOW_ROOT / rel).resolve()
    # 確認 target 在 Z 槽底下
    try:
        target.relative_to(SLIDESHOW_ROOT.resolve())
    except ValueError:
        return None
    return target


@app.route("/api/slideshow/list_dirs")
def api_slideshow_list_dirs():
    """列出指定路徑下的子資料夾。
    query: ?path=<rel>（空 = Z 根目錄）
    回傳: {"ok": true, "path": "...", "parent": "...", "dirs": ["相簿/2024", ...]}
    """
    rel = request.args.get("path", "")
    target = _slideshow_safe_path(rel)
    if target is None or not target.exists() or not target.is_dir():
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    dirs = []
    try:
        for p in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir():
                dirs.append(p.name)
    except PermissionError:
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    parent = ""
    if rel:
        parts = rel.replace("\\", "/").strip("/").split("/")
        parent = "/".join(parts[:-1]) if len(parts) > 1 else ""
    return jsonify({"ok": True, "path": rel, "parent": parent, "dirs": dirs})


@app.route("/api/slideshow/list_files")
def api_slideshow_list_files():
    """列出指定資料夾的圖片檔。
    query: ?path=<rel>&recursive=1（遞迴抓所有子資料夾）
    回傳: {"ok": true, "path": "...", "files": [{"name": "subdir/x.jpg", "size": 1234}, ...]}
      - 非遞迴：name = 純檔名
      - 遞迴：name = 相對 path（包含子目錄）
    """
    rel = request.args.get("path", "")
    recursive = request.args.get("recursive", "") == "1"
    target = _slideshow_safe_path(rel)
    if target is None or not target.exists() or not target.is_dir():
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    files = []
    try:
        if recursive:
            # 用 os.walk + scandir 比 rglob 快 5-10 倍（避免每個 entry 額外 stat）
            import os as _os
            for root, dirs, fns in _os.walk(target):
                root_path = Path(root)
                for fn in fns:
                    ext = _os.path.splitext(fn)[1].lower()
                    if ext in _IMG_EXTS:
                        relname = str((root_path / fn).relative_to(target)).replace("\\", "/")
                        # 大量檔案時不去拿 size、空字串、前端顯示「-」
                        files.append({"name": relname, "size": 0})
            files.sort(key=lambda x: x["name"].lower())
        else:
            # 非遞迴：scandir 比 iterdir 快、stat 內建於 DirEntry、不額外 syscall
            import os as _os
            with _os.scandir(target) as it:
                for entry in it:
                    if entry.is_file():
                        ext = _os.path.splitext(entry.name)[1].lower()
                        if ext in _IMG_EXTS:
                            try:
                                sz = entry.stat().st_size
                            except OSError:
                                sz = 0
                            files.append({"name": entry.name, "size": sz})
            files.sort(key=lambda x: x["name"].lower())
    except PermissionError:
        return jsonify({"ok": False, "error": "Permission denied"}), 403
    return jsonify({"ok": True, "path": rel, "recursive": recursive, "files": files})


@app.route("/api/slideshow/img")
def api_slideshow_img():
    """送圖片 bytes 給瀏覽器顯示。
    query: ?path=<rel/folder>&name=<filename or subdir/filename>&w=<max_width>&q=<quality>
      - w=1280: 最長邊不超過 1280px、會用 Pillow 壓 JPEG（投影模式快很多）
      - q=80: JPEG quality
      - 沒帶 w → 原圖直送
    """
    from flask import send_file, Response
    rel = request.args.get("path", "")
    name = request.args.get("name", "")
    max_w = request.args.get("w", "")
    quality = request.args.get("q", "80")
    if not name:
        return jsonify({"ok": False, "error": "Bad name"}), 400
    # 統一斜線、擋 .. 跟絕對路徑
    name = name.replace("\\", "/")
    if ".." in name.split("/") or name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        return jsonify({"ok": False, "error": "Bad name"}), 400
    target_dir = _slideshow_safe_path(rel)
    if target_dir is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    file_path = (target_dir / name).resolve()
    try:
        file_path.relative_to(target_dir.resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "Out of root"}), 400
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"ok": False, "error": "Not found"}), 404
    if file_path.suffix.lower() not in _IMG_EXTS:
        return jsonify({"ok": False, "error": "Not image"}), 400

    mt_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}

    # 沒帶 w 或值不合法 → 原圖直送
    try:
        max_w_int = int(max_w) if max_w else 0
        q_int = int(quality)
    except ValueError:
        max_w_int = 0
        q_int = 80

    if max_w_int <= 0:
        return send_file(file_path, mimetype=mt_map.get(file_path.suffix.lower(), "application/octet-stream"))

    # 壓縮模式：用 cache 避免重複壓
    import hashlib, io as _io
    cache_key = f"{file_path}|{file_path.stat().st_mtime}|{max_w_int}|{q_int}"
    cache_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(__file__).parent / "vision_sessions" / "_slideshow_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_hash}.jpg"
    if cache_path.exists():
        return send_file(cache_path, mimetype="image/jpeg")

    # 沒 cache → Pillow 壓縮、寫 cache
    try:
        from PIL import Image
        im = Image.open(file_path)
        # JPEG 不支援透明、白底貼平
        if im.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", im.size, (0, 0, 0))  # 投影黑底
            if im.mode == "RGBA":
                bg.paste(im, mask=im.split()[3])
            else:
                bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        # resize 最長邊到 max_w_int
        w, h = im.size
        if max(w, h) > max_w_int:
            r = max_w_int / max(w, h)
            im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
        # 寫 cache + 直接送
        im.save(cache_path, format="JPEG", quality=q_int, optimize=True)
        return send_file(cache_path, mimetype="image/jpeg")
    except Exception as e:
        print(f"[slideshow] compress fail: {e}、fallback 原圖")
        return send_file(file_path, mimetype=mt_map.get(file_path.suffix.lower(), "application/octet-stream"))


# 啟動心跳監控背景執行緒
threading.Thread(target=heartbeat_monitor, daemon=True).start()


# ===== 家人用的簡易聊天（純對話，無工具權限）=====================
# 現有的 /api/chat/* 會跑 Claude Code，有檔案與 terminal 權限。
# 這條路直接打橋接器 :1234，只做對話，圖片由橋接器轉給 Gemini。
# ----- 家人貼網址時自動抓內容（本地模型不會上網）-----
URL_RE = re.compile(r'https?://[^\s<>"　，。！？）】]+')
URL_MAX_CHARS = 6000        # 一頁最多塞這麼多字，避免吃掉整個 ctx
URL_MAX_LINKS = 2           # 一則訊息最多抓兩個網址


def _fetch_url_text(url):
    """抓網頁正文。回傳 (標題, 內文) 或 None。"""
    try:
        import trafilatura
    except ImportError:
        trafilatura = None

    headers = {"User-Agent": "Mozilla/5.0 (compatible; HermesBot/1.0)"}
    r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    r.raise_for_status()

    ctype = r.headers.get("Content-Type", "")
    if "html" not in ctype and "text" not in ctype:
        return None, f"（這個連結不是網頁，是 {ctype.split(';')[0]}）"

    r.encoding = r.apparent_encoding or r.encoding
    html = r.text

    title, body = "", ""
    if trafilatura:
        body = trafilatura.extract(html, include_comments=False,
                                   include_tables=True) or ""
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title

    if not body:                      # trafilatura 抽不到就退回 bs4
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "aside"]):
            t.decompose()
        if soup.title and soup.title.string:
            title = title or soup.title.string.strip()
        body = "\n".join(l.strip() for l in soup.get_text("\n").splitlines()
                         if l.strip())

    if len(body) > URL_MAX_CHARS:
        body = body[:URL_MAX_CHARS] + "\n…（內容太長，只取前面這段）"
    return title.strip(), body.strip()


def _expand_urls(text):
    """把訊息裡的網址換成「網址 + 抓回來的內容」。沒網址就原樣回傳。"""
    if not text or "http" not in text:
        return text, 0

    urls = URL_RE.findall(text)[:URL_MAX_LINKS]
    if not urls:
        return text, 0

    blocks = []
    ok = 0
    for u in urls:
        u = u.rstrip('.,;:!?)）】」』')
        try:
            title, body = _fetch_url_text(u)
            if body:
                head = f"【網頁內容】{title}\n來源：{u}" if title else f"【網頁內容】{u}"
                blocks.append(f"{head}\n\n{body}")
                ok += 1
            else:
                blocks.append(f"【無法讀取】{u}")
        except Exception as e:
            blocks.append(f"【無法讀取】{u}（{str(e)[:60]}）")

    if not blocks:
        return text, 0
    return "\n\n".join(blocks) + "\n\n---\n\n使用者的問題：" + text, ok


SIMPLE_UPSTREAM = "http://127.0.0.1:1234/v1/chat/completions"
SIMPLE_MODEL = "qwen38_mtp"
SIMPLE_SYSTEM = (
    "你是一個友善的助理。用繁體中文回答，簡潔清楚。\n"
    "\n"
    "你有這些能力，不要說自己沒有：\n"
    "- 看圖：使用者附圖時你看得到，直接描述你看到的內容。\n"
    "- 上網查資料：需要即時或不確定的資訊時，系統會自動幫你查好，\n"
    "  再把結果附在問題前面。看到【網路搜尋結果】就依那些資料回答。\n"
    "- 讀網頁：使用者貼網址時，內容會自動抓回來附在問題裡。\n"
    "\n"
    "沒有拿到搜尋結果、又不確定答案時，就說不知道，不要編。"
)

# ===== 網路搜尋（後端代查，不給模型工具呼叫權）=====
# 家人介面是一問一答的簡化版，刻意不讓模型能執行指令或動檔案。
# 所以搜尋由後端做：先問模型要不要查，要查就跑 ddgs 把結果塞進 prompt。
SEARCH_MAX_RESULTS = 6      # 抓幾筆搜尋結果
SEARCH_FETCH_PAGES = 2      # 其中幾筆要真的抓內文（其餘只用摘要）
SEARCH_DECIDE_TIMEOUT = 120

_SEARCH_DECIDE_PROMPT = (
    "判斷下面這個問題需不需要查網路才能正確回答。\n"
    "需要查的情況：即時資訊（新聞、股價、天氣、比分）、特定產品或服務的細節、"
    "你不確定或可能記錯的事實、最近發生的事。\n"
    "不需要查的情況：閒聊、翻譯、算數學、寫程式、解釋常識概念、"
    "你有把握的知識。\n\n"
    "只回一行，格式二選一：\n"
    "NO\n"
    "YES|要搜尋的關鍵字\n\n"
    "關鍵字要精簡（不超過 10 個字），用最容易搜到答案的講法。\n\n"
    "問題："
)


# 明顯不用查網路的，直接跳過「問模型要不要查」那一輪推理（省 30-60 秒）。
# 只擋很有把握的，其餘一律交給模型判斷 —— 寧可多查也不要漏查。
_NO_SEARCH_RE = re.compile(
    r"^\s*(你好|哈囉|嗨|hi|hello|早安|午安|晚安|謝謝|感謝|thanks|"
    r"掰掰|再見|bye|ok|好的|嗯|哈哈|笑死)\s*[!?。！？~～]*\s*$",
    re.I)
# 句子裡有算式就當作數學題（不要求整句都是算式 ——
# '1+1 等於多少？只回數字' 這種原本會漏掉，白跑一輪推理）
_MATH_RE = re.compile(r"\d\s*[\+\-\*/×÷]\s*\d")


def _obviously_no_search(q):
    """明顯不用查網路就回 True。判斷不了就回 False（交給模型）。"""
    if not q or len(q.strip()) < 2:
        return True
    s = q.strip()
    if _NO_SEARCH_RE.match(s):
        return True
    if len(s) <= 40 and _MATH_RE.search(s):
        return True
    # 「翻譯成英文」「幫我寫一段 python」這類明顯是生成任務
    if re.search(r"(翻譯|translate|幫我寫|寫一[個段支]|改寫|潤稿|取個名字)", s):
        return True
    return False


def _should_search(question):
    """問模型這題要不要查網路。回傳搜尋關鍵字，不用查就回 None。"""
    if _obviously_no_search(question):
        return None
    try:
        r = requests.post(SIMPLE_UPSTREAM, json={
            "model": SIMPLE_MODEL,
            "messages": [{"role": "user",
                          "content": _SEARCH_DECIDE_PROMPT + question}],
            "max_tokens": 60,
            "temperature": 0.1,
            "stream": False,
        }, timeout=SEARCH_DECIDE_TIMEOUT)
        r.raise_for_status()
        out = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return None          # 判斷失敗就當作不用查，不要卡住回答

    line = out.splitlines()[0].strip() if out else ""
    if not line.upper().startswith("YES"):
        return None
    _, _, kw = line.partition("|")
    kw = kw.strip().strip('"\'')
    return kw or question[:40]


def _web_search(keyword):
    """跑 ddgs 搜尋，回傳整理好的文字。查不到就回 None。"""
    try:
        from ddgs import DDGS
    except Exception:
        return None
    try:
        with DDGS() as d:
            hits = list(d.text(keyword, max_results=SEARCH_MAX_RESULTS))
    except Exception as e:
        return "【搜尋失敗】" + str(e)[:80]
    if not hits:
        return None

    blocks = []
    for i, h in enumerate(hits, 1):
        title = (h.get("title") or "").strip()
        body = (h.get("body") or "").strip()
        href = (h.get("href") or "").strip()
        blocks.append("%d. %s\n   %s\n   %s" % (i, title, body, href))

    # 前幾筆再抓實際內文，摘要常常太短講不清楚
    for h in hits[:SEARCH_FETCH_PAGES]:
        href = (h.get("href") or "").strip()
        if not href:
            continue
        try:
            title, text = _fetch_url_text(href)
            if text:
                blocks.append("【內文】%s\n來源：%s\n\n%s"
                              % (title or "", href, text))
        except Exception:
            pass

    return "\n\n".join(blocks)



@app.route("/chat")
def simple_chat_page():
    return send_from_directory(Path(__file__).parent / "templates",
                               "simple_chat.html")


@app.route("/api/simple/send", methods=["POST"])
def api_simple_send():
    """一次一問一答。body: {messages:[{role,content}], image_base64?}"""
    data = request.json or {}
    msgs = data.get("messages") or []
    img = data.get("image_base64")

    if not isinstance(msgs, list) or not msgs:
        return jsonify({"ok": False, "error": "messages 不能是空的"}), 400

    # 只留最近 20 則，避免手機端無限累積
    msgs = msgs[-20:]

    payload_msgs = [{"role": "system", "content": SIMPLE_SYSTEM}]
    for m in msgs:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            payload_msgs.append({"role": role, "content": content})

    # 家人常常只貼一個網址就問「這是什麼」——本地模型不會上網，先抓回來
    n_url = 0
    if payload_msgs and payload_msgs[-1]["role"] == "user":
        last = payload_msgs[-1]
        if isinstance(last["content"], str):
            expanded, n_url = _expand_urls(last["content"])
            if n_url:
                last["content"] = expanded

    # 沒有貼網址、也沒有附圖時，問模型這題要不要查網路。
    # 有網址就不用查（已經抓回內容了）；有圖的話問題通常是關於圖本身。
    if (not n_url) and (not img) and payload_msgs \
            and payload_msgs[-1]["role"] == "user" \
            and isinstance(payload_msgs[-1]["content"], str):
        q = payload_msgs[-1]["content"]
        kw = _should_search(q)
        if kw:
            found = _web_search(kw)
            if found:
                payload_msgs[-1]["content"] = (
                    "【網路搜尋結果】關鍵字：" + kw + "\n\n" + found
                    + "\n\n---\n\n請根據上面的搜尋結果回答。"
                      "資料裡沒提到的就說不知道，不要自己編。\n\n"
                      "使用者的問題：" + q)

    # 有圖就用 OpenAI 的多模態格式，橋接器會攔下來送 Gemini
    if img and payload_msgs:
        last = payload_msgs[-1]
        if last["role"] == "user":
            last["content"] = [
                {"type": "text", "text": last["content"]},
                {"type": "image_url", "image_url": {"url": img}},
            ]

    try:
        r = requests.post(SIMPLE_UPSTREAM, json={
            "model": SIMPLE_MODEL,
            "messages": payload_msgs,
            "max_tokens": 2048,
            "temperature": 0.7,
            "stream": False,
        }, timeout=600)
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"].get("content") or ""
        return jsonify({"ok": True, "reply": reply.strip()})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "模型回應逾時，請再試一次"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ----- 簡易聊天的歷史紀錄（存伺服器，跟主人的 sessions/ 分開）-----
SIMPLE_DIR = Path(__file__).parent / "simple_chats"
SIMPLE_DIR.mkdir(exist_ok=True)


def _simple_path(device, cid):
    """檔名帶 device 前綴，這樣一支手機只看得到自己的對話。"""
    safe_d = re.sub(r"[^A-Za-z0-9_-]", "", str(device))[:40] or "anon"
    safe_c = re.sub(r"[^A-Za-z0-9_-]", "", str(cid))[:40]
    return SIMPLE_DIR / f"{safe_d}__{safe_c}.json"


@app.route("/api/simple/list")
def api_simple_list():
    """列出這支裝置的對話，新的在前。"""
    device = request.args.get("device", "")
    if not device:
        return jsonify({"ok": True, "chats": []})
    safe_d = re.sub(r"[^A-Za-z0-9_-]", "", device)[:40] or "anon"
    out = []
    for f in SIMPLE_DIR.glob(f"{safe_d}__*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id"),
                "title": d.get("title") or "新對話",
                "updated": d.get("updated", 0),
                "count": len(d.get("messages", [])),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["updated"], reverse=True)
    return jsonify({"ok": True, "chats": out[:50]})


@app.route("/api/simple/load")
def api_simple_load():
    """讀一段對話的完整內容。"""
    device = request.args.get("device", "")
    cid = request.args.get("id", "")
    p = _simple_path(device, cid)
    if not p.exists():
        return jsonify({"ok": False, "error": "找不到這段對話"}), 404
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return jsonify({"ok": True, "messages": d.get("messages", []),
                        "title": d.get("title", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 500


@app.route("/api/simple/save", methods=["POST"])
def api_simple_save():
    """存一段對話。標題取第一則使用者訊息的前 24 字。"""
    data = request.json or {}
    device = data.get("device", "")
    cid = data.get("id", "")
    msgs = data.get("messages") or []
    if not device or not cid or not msgs:
        return jsonify({"ok": False, "error": "缺參數"}), 400

    title = ""
    for m in msgs:
        if m.get("role") == "user" and m.get("content"):
            title = str(m["content"]).strip().replace("\n", " ")[:24]
            break

    try:
        _simple_path(device, cid).write_text(json.dumps({
            "id": cid, "title": title or "新對話",
            "updated": time.time(), "messages": msgs[-60:],
        }, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 500


@app.route("/api/simple/delete", methods=["POST"])
def api_simple_delete():
    data = request.json or {}
    p = _simple_path(data.get("device", ""), data.get("id", ""))
    try:
        if p.exists():
            p.unlink()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 500


if __name__ == "__main__":
    print("=" * 50)
    print("Remote Workstation GUI")
    print("=" * 50)
    # 啟動時載入歷史 sessions
    _load_sessions_from_disk()
    _vision_load_all()
    print("Open in browser: http://localhost:5000")
    print("From phone:      http://10.35.219.64:5000")
    print(f"Sessions dir:    {SESSIONS_DIR}")
    print(f"Vision dir:      {VISION_DIR}")
    print("=" * 50)
    # threaded=True：每個請求一條 thread，避免 SSE 長連線把整個 server 卡死
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
