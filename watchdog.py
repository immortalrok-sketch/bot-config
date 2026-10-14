"""
Агент ноды: старт Engine + heartbeat на Main.
На Main ещё Telegram (живёт даже когда Engine выключен).
Порт 5001. Консоли свёрнуты.
"""
import os
import sys
import time
import json
import subprocess
import threading
from pathlib import Path

try:
    from flask import Flask, jsonify
except ImportError:
    print("pip install flask")
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None

BASE_DIR = Path(__file__).resolve().parent
NODE_CFG_PATH = BASE_DIR / "local_node.json"

SW_MINIMIZE = 6


def minimize_own_console():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
    except Exception:
        pass


def load_cfg():
    cfg = {
        "pc_name": "Rig-Unknown",
        "server_ip": "127.0.0.1",
        "node_ip": None,
        "is_server_pc": False,
        "engine_script": "Engine_Modul.py",
        "python_exe": sys.executable,
        "watchdog_port": 5001,
        "heartbeat_sec": 15,
        "telegram": {},
        "farm_nodes": [],
    }
    if NODE_CFG_PATH.exists():
        try:
            with open(NODE_CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg["pc_name"] = data.get("pc_name", cfg["pc_name"])
            cfg["server_ip"] = data.get("server_ip", cfg["server_ip"])
            cfg["node_ip"] = data.get("node_ip")
            cfg["is_server_pc"] = bool(data.get("is_server_pc", False))
            cfg["telegram"] = data.get("telegram") or {}
            cfg["farm_nodes"] = data.get("farm_nodes") or []
            w = data.get("watchdog") or {}
            cfg["engine_script"] = w.get("engine_script", cfg["engine_script"])
            cfg["python_exe"] = w.get("python_exe", cfg["python_exe"])
            cfg["watchdog_port"] = int(w.get("port", cfg["watchdog_port"]))
            cfg["heartbeat_sec"] = int(w.get("heartbeat_sec", cfg["heartbeat_sec"]))
        except Exception as e:
            print(f"[Watchdog] cfg: {e}", flush=True)
    return cfg


CFG = load_cfg()
ENGINE_PATH = BASE_DIR / CFG["engine_script"]
app = Flask(__name__)
_lock = threading.Lock()
_engine_proc = None


def _engine_running() -> bool:
    global _engine_proc
    if _engine_proc is not None:
        if _engine_proc.poll() is None:
            return True
        _engine_proc = None
    try:
        import psutil
        target = CFG["engine_script"].lower()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                if target in cmd and "python" in (p.info.get("name") or "").lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return False


def _minimize_pid_console(pid: int):
    if sys.platform != "win32" or not pid:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )
        found = []

        def _cb(hwnd, _lp):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        for hwnd in found:
            user32.ShowWindow(hwnd, SW_MINIMIZE)
    except Exception:
        pass


def start_engine() -> dict:
    global _engine_proc
    with _lock:
        if _engine_running():
            pid = _engine_proc.pid if _engine_proc else None
            return {"status": "ok", "message": "already_running", "pid": pid}
        if not ENGINE_PATH.exists():
            return {"status": "error", "message": f"not_found: {ENGINE_PATH}"}

        creationflags = 0
        startupinfo = None
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_CONSOLE
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = SW_MINIMIZE

        try:
            env = os.environ.copy()
            env["CYBERFARM_NOPAUSE"] = "1"
            _engine_proc = subprocess.Popen(
                [CFG["python_exe"], str(ENGINE_PATH)],
                cwd=str(BASE_DIR),
                creationflags=creationflags,
                startupinfo=startupinfo,
                env=env,
            )
            time.sleep(1.2)
            if _engine_proc.poll() is not None:
                code = _engine_proc.returncode
                _engine_proc = None
                return {"status": "error", "message": f"exited code={code}"}
            _minimize_pid_console(_engine_proc.pid)
            print(f"[Watchdog] Engine pid={_engine_proc.pid}", flush=True)
            return {"status": "ok", "message": "started", "pid": _engine_proc.pid}
        except Exception as e:
            _engine_proc = None
            return {"status": "error", "message": str(e)}


def _heartbeat_loop():
    if not requests:
        print("[Watchdog] нет requests — heartbeat отключён", flush=True)
        return
    server = CFG["server_ip"] or "127.0.0.1"
    if CFG.get("is_server_pc") and server in ("127.0.0.1", "localhost"):
        url = "http://127.0.0.1:5000/api/node_heartbeat"
    else:
        url = f"http://{server}:5000/api/node_heartbeat"
    interval = max(5, int(CFG.get("heartbeat_sec", 15)))
    print(f"[Watchdog] heartbeat → {url} every {interval}s", flush=True)
    while True:
        payload = {
            "pc_name": CFG["pc_name"],
            "ip": CFG.get("node_ip"),
            "engine_running": _engine_running(),
            "watchdog": True,
        }
        try:
            requests.post(url, json=payload, timeout=3)
        except Exception:
            pass
        time.sleep(interval)


def _build_nodes_from_cfg() -> dict:
    """Список из farm_nodes, когда Engine выключен."""
    nodes = {}
    for n in CFG.get("farm_nodes") or []:
        pc = n.get("pc_name")
        if not pc:
            continue
        is_local = bool(n.get("is_local")) or (pc == CFG["pc_name"])
        nodes[pc] = {
            "name": f"{pc} (Сервер)" if is_local else pc,
            "is_local": is_local,
            "status": "idle",
            "windows": [],
            "ip": n.get("ip") or ("127.0.0.1" if is_local else None),
            "engine_running": False,
            "watchdog_port": 5001,
        }
    return nodes


def _get_nodes_provider() -> dict:
    """Сначала живой реестр Engine, иначе farm_nodes."""
    if requests:
        try:
            r = requests.get("http://127.0.0.1:5000/api/nodes", timeout=1.5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data:
                    return data
        except Exception:
            pass
    return _build_nodes_from_cfg()


def start_telegram_if_main():
    """Только Main + telegram.enabled — иначе Conflict с Engine."""
    if not CFG.get("is_server_pc"):
        return
    tg = CFG.get("telegram") or {}
    if not tg.get("enabled"):
        print("[Watchdog] Telegram: enabled=false", flush=True)
        return
    token = tg.get("token")
    chat_id = tg.get("admin_chat_id")
    if not token or not chat_id:
        print("[Watchdog] Telegram: нет token/admin_chat_id", flush=True)
        return
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from services.telegram_bot import init_telegram_bot
        init_telegram_bot(
            token=token,
            admin_chat_id=chat_id,
            window_title="RF",
            get_nodes_provider=_get_nodes_provider,
        )
        print("[Watchdog] Telegram запущен (Main)", flush=True)
    except Exception as e:
        print(f"[Watchdog] Telegram error: {e}", flush=True)


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify(
        {
            "status": "ok",
            "pc_name": CFG["pc_name"],
            "engine_running": _engine_running(),
            "pid": _engine_proc.pid if _engine_running() and _engine_proc else None,
        }
    )


@app.route("/start_engine", methods=["POST", "GET"])
def api_start_engine():
    result = start_engine()
    code = 200 if result.get("status") == "ok" else 500
    return jsonify(result), code


@app.route("/status", methods=["GET"])
def status():
    return ping()


def main():
    minimize_own_console()
    port = CFG["watchdog_port"]
    print(
        f"[Watchdog] {CFG['pc_name']} | :{port} | engine={ENGINE_PATH.name}",
        flush=True,
    )
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    start_telegram_if_main()
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()