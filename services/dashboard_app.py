"""
Модуль локального и сетевого веб-дэшборда (Flask).
Слой SERVICES. Master + Worker.
"""
import sys
import time
import re
import os
import threading
import logging
import json
from pathlib import Path
from typing import Dict
from flask import Flask, render_template_string, jsonify, request, Response
from collections import defaultdict

try:
    import requests
except ImportError:
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

app = Flask(__name__)
START_TIME = time.time()

DIAMOND_PRICE_USD = {
    "rf_next": 0.005,
    "ymir": 0.50,
    "vampir": 0.30,
    "default": 0.50,
}

DEFAULT_STATS = {
    "level": 0,
    "exp_percent": 0.0,
    "combat_power": 0,
    "diamonds": 0,
}

cluster_states = {}
system_states = {}
last_seen = {}       # pc_name -> ts
node_ips = {}        # pc_name -> ip
# pc_name -> {"engine_running": bool, "watchdog": bool, "ts": float}
node_meta = {}
_local_tracked = []  # список окон этой ноды [{hwnd, display_name, ...}]

STALE_AFTER_SEC = 60
STALE_CHECK_EVERY_SEC = 15

# --- управление с Telegram / REST ---
NODE_CONTROL = {
    "farm_running": True,
    "paused_instances": set(),  # display_name на паузе
    "restart_requested": False,
}


def set_local_tracked_windows(windows: list) -> None:
    """Вызывать из Engine после сборки tracked_windows."""
    global _local_tracked
    _local_tracked = list(windows or [])

def _close_hwnd(hwnd) -> bool:
    try:
        import win32gui
        import win32con
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        win32gui.PostMessage(int(hwnd), win32con.WM_CLOSE, 0, 0)
        return True
    except Exception as e:
        print(f"[NODE_CONTROL] close hwnd error: {e}", flush=True)
        return False


def _close_tracked_windows(instance=None) -> int:
    """
    instance=None → все окна из _local_tracked
    instance=display_name → одно окно
    """
    closed = 0
    for w in list(_local_tracked):
        if not isinstance(w, dict):
            continue
        name = w.get("display_name")
        hwnd = w.get("hwnd")
        if instance is not None and str(name) != str(instance):
            continue
        if _close_hwnd(hwnd):
            closed += 1
            print(f"[NODE_CONTROL] closed window {name} hwnd={hwnd}", flush=True)
    return closed

def apply_node_command(action: str, instance=None) -> dict:
    action = (action or "").lower().strip()
    inst = str(instance) if instance not in (None, "", "null") else None

    # алиасы из Telegram
    if action == "pause":
        action = "stop"
    if action in ("exit", "shutdown", "kill"):
        action = "exit"

    # --- закрытие окон: пауза + WM_CLOSE ---
    if action in ("close_windows", "close_window"):
        if action == "close_window" and inst:
            NODE_CONTROL["paused_instances"].add(inst)
            n = _close_tracked_windows(instance=inst)
        else:
            NODE_CONTROL["farm_running"] = False
            NODE_CONTROL["paused_instances"].clear()
            n = _close_tracked_windows(instance=None)
        print(f"[NODE_CONTROL] {action} closed={n}", flush=True)
        return {
            "status": "ok",
            "action": action,
            "instance": inst,
            "closed": n,
            "farm_running": NODE_CONTROL["farm_running"],
            "paused": list(NODE_CONTROL["paused_instances"]),
        }

    if action == "stop":
        if inst:
            NODE_CONTROL["paused_instances"].add(inst)
        else:
            NODE_CONTROL["farm_running"] = False

    elif action == "start":
        if inst:
            NODE_CONTROL["paused_instances"].discard(inst)
        else:
            NODE_CONTROL["farm_running"] = True
            NODE_CONTROL["paused_instances"].clear()

    elif action == "restart":
        if inst:
            NODE_CONTROL["paused_instances"].discard(inst)
        else:
            NODE_CONTROL["farm_running"] = True
            NODE_CONTROL["paused_instances"].clear()
        NODE_CONTROL["restart_requested"] = True

    elif action == "close":
        NODE_CONTROL["farm_running"] = False
        NODE_CONTROL["paused_instances"].clear()

    elif action == "exit":
        NODE_CONTROL["farm_running"] = False
        print("[NODE_CONTROL] exit — завершение процесса через 1с", flush=True)

        def _die():
            time.sleep(1.0)
            os._exit(0)

        threading.Thread(target=_die, daemon=True).start()

    else:
        return {"status": "error", "message": f"unknown action {action}"}

    print(
        f"[NODE_CONTROL] action={action} instance={inst} "
        f"farm={NODE_CONTROL['farm_running']} paused={NODE_CONTROL['paused_instances']}",
        flush=True,
    )
    return {
        "status": "ok",
        "action": action,
        "instance": inst,
        "farm_running": NODE_CONTROL["farm_running"],
        "paused": list(NODE_CONTROL["paused_instances"]),
    }


def load_dashboard_ocr_rules() -> dict:
    """Базовые дефолты — используются, когда для конкретной игры
    ещё не сконфигурированы собственные правила (см. get_game_dashboard_rules)."""
    rules = {
        "level": (1, 3),
        "combat_power": (5, 6),
        "diamonds": (3, 5),
    }
    try:
        config_path = PROJECT_ROOT / "config.json"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                ocr_cfg = cfg.get("ocr_config", cfg)
                if "digit_len" in ocr_cfg:
                    for k, v in ocr_cfg["digit_len"].items():
                        if isinstance(v, (list, tuple)) and len(v) == 2:
                            rules[k] = (int(v[0]), int(v[1]))
    except Exception:
        pass
    return rules


# Дефолты на случай, если для конкретной игры ничего не задано
DEFAULT_DIGIT_LEN = load_dashboard_ocr_rules()
DEFAULT_MIN_DIAMONDS = 100

# Оставлено для обратной совместимости (если где-то ещё используется напрямую)
STAT_DIGIT_LEN = DEFAULT_DIGIT_LEN

# Кэш правил валидации по каждой игре: game_key -> {"digit_len": {...}, "min_diamonds": int}
_dashboard_rules_cache: Dict[str, dict] = {}


def get_game_dashboard_rules(game_key: str) -> dict:
    """
    Загружает правила валидации статов (длина цифр + минимальный порог
    для бриллиантов) конкретно под игру, из games/<game_key>/config.json
    (секция "ocr_config"). Кэшируется после первой загрузки.

    Если игра ещё не сконфигурирована / файла нет / секции нет —
    тихо откатывается на старые глобальные дефолты, так что для уже
    работающих игр ничего не меняется, пока ты явно не пропишешь
    свои правила под конкретную игру.
    """
    game_key = (game_key or "").lower().strip()
    if not game_key:
        return {"digit_len": DEFAULT_DIGIT_LEN, "min_diamonds": DEFAULT_MIN_DIAMONDS}

    if game_key in _dashboard_rules_cache:
        return _dashboard_rules_cache[game_key]

    digit_len = DEFAULT_DIGIT_LEN.copy()
    min_diamonds = DEFAULT_MIN_DIAMONDS

    cfg_path = PROJECT_ROOT / "games" / game_key / "config.json"
    try:
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            ocr_cfg = cfg.get("ocr_config", {})

            digit_len_raw = ocr_cfg.get("digit_len", {})
            for k, v in digit_len_raw.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    digit_len[k] = (int(v[0]), int(v[1]))

            if "dashboard_min_diamonds" in ocr_cfg:
                min_diamonds = int(ocr_cfg["dashboard_min_diamonds"])
        else:
            print(f"[Дашборд] Конфиг '{game_key}' не найден по пути {cfg_path}, беру дефолты.")
    except Exception as e:
        print(f"[Дашборд WARN] Не удалось загрузить правила для '{game_key}': {e}. Беру дефолты.")

    result = {"digit_len": digit_len, "min_diamonds": min_diamonds}
    _dashboard_rules_cache[game_key] = result
    return result


def touch_pc(pc_name: str, ip: str = None) -> None:
    if not pc_name:
        return
    last_seen[pc_name] = time.time()
    if ip and ip not in ("127.0.0.1", "localhost"):
        node_ips[pc_name] = ip


def mark_pc_offline(pc_name: str) -> None:
    if pc_name not in cluster_states:
        return
    zeros = DEFAULT_STATS.copy()
    for win_name, win_data in list(cluster_states[pc_name].items()):
        if isinstance(win_data, dict):
            win_data["status"] = "OFFLINE"
            win_data["stats"] = zeros.copy()
        else:
            cluster_states[pc_name][win_name] = {
                "status": "OFFLINE",
                "stats": zeros.copy(),
            }


def _stale_watcher() -> None:
    while True:
        try:
            now = time.time()
            for pc_name, ts in list(last_seen.items()):
                if now - ts > STALE_AFTER_SEC:
                    mark_pc_offline(pc_name)
        except Exception:
            pass
        time.sleep(STALE_CHECK_EVERY_SEC)


def get_game_prefix(window_name: str) -> str:
    """Достаёт имя игры из display_name вида 'raven2_1' -> 'raven2'."""
    name = str(window_name).lower()
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name


def sanitize_stats(stats: dict, game_key: str = None) -> dict:
    """
    Очищает и валидирует статы перед записью в cluster_states.
    game_key — имя игры (например 'rf_next', 'raven2'), берётся из
    display_name через get_game_prefix(). Если не передан — используются
    старые глобальные дефолтные правила (обратная совместимость).
    """
    if not isinstance(stats, dict):
        return {}

    if game_key:
        rules = get_game_dashboard_rules(game_key)
    else:
        rules = {"digit_len": DEFAULT_DIGIT_LEN, "min_diamonds": DEFAULT_MIN_DIAMONDS}

    digit_len = rules["digit_len"]
    min_diamonds = rules["min_diamonds"]

    clean_stats = {}
    for key, val in stats.items():
        if val is None:
            continue
        if key in digit_len:
            min_len, max_len = digit_len[key]
            digits_only = "".join(ch for ch in str(val) if ch.isdigit())
            if not digits_only:
                continue
            if not (min_len <= len(digits_only) <= max_len):
                continue
        if key == "diamonds":
            try:
                num_val = int(val)
            except (ValueError, TypeError):
                continue
            clean_stats[key] = num_val if num_val >= min_diamonds else 0
        else:
            clean_stats[key] = val
    return clean_stats


def get_formatted_uptime() -> str:
    elapsed_seconds = int(time.time() - START_TIME)
    days = elapsed_seconds // 86400
    hours = (elapsed_seconds % 86400) // 3600
    minutes = (elapsed_seconds % 3600) // 60
    seconds = elapsed_seconds % 60
    return f"{days}д {hours:02d}ч {minutes:02d}м {seconds:02d}с"


def calc_diamonds_by_game():
    by_game = defaultdict(int)
    now = time.time()
    for pc_name, pc_windows in cluster_states.items():
        ts = last_seen.get(pc_name, 0)
        if ts and (now - ts > STALE_AFTER_SEC):
            continue
        for win_name, win_data in pc_windows.items():
            if not isinstance(win_data, dict):
                continue
            stats = win_data.get("stats", {})
            diamonds = stats.get("diamonds", 0) or 0
            try:
                diamonds = int(diamonds)
            except (ValueError, TypeError):
                diamonds = 0
            by_game[get_game_prefix(win_name)] += diamonds
    result = []
    for game, amount in sorted(by_game.items()):
        price = DIAMOND_PRICE_USD.get(game, DIAMOND_PRICE_USD["default"])
        result.append({
            "game": game.upper(),
            "amount": amount,
            "usd": round(amount * price, 2),
            "price": price,
        })
    return result


def sort_rig_keys(cluster_dict: dict) -> list:
    def rig_sort_key(pc_name: str):
        name_lower = pc_name.lower()
        if "main" in name_lower:
            return (-1, 0, pc_name)
        numbers = re.findall(r"\d+", pc_name)
        if numbers:
            return (0, int(numbers[0]), pc_name)
        return (1, 0, pc_name)

    sorted_keys = sorted(cluster_dict.keys(), key=rig_sort_key)
    return [(k, cluster_dict[k]) for k in sorted_keys]


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Multi-PC Bot Dashboard</title>
    <meta http-equiv="refresh" content="3">
<style>
    body { font-family: 'Consolas', 'Segoe UI', monospace; background: #0b0f17; color: #e2e8f0; padding: 20px; margin: 0; }
    .header-container { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
    h1 { color: #38bdf8; margin: 0 0 4px 0; font-size: 26px; font-weight: 700; }
    .sub { color: #64748b; font-size: 14px; }
    .uptime-badge { background: #131a26; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 16px; font-size: 14px; color: #94a3b8; display: flex; align-items: center; gap: 10px; }
    .uptime-value { color: #38bdf8; font-weight: bold; font-size: 15px; }
    .diamonds-panel { background: #131a26; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 20px; margin-bottom: 20px; display: flex; flex-wrap: wrap; gap: 24px; align-items: center; }
    .diamond-item { display: flex; align-items: center; gap: 8px; font-size: 16px; }
    .diamond-game { color: #94a3b8; font-weight: bold; }
    .diamond-amount { color: #38bdf8; font-weight: bold; font-size: 19px; }
    .diamond-usd { color: #4ade80; font-size: 15px; font-weight: 600; }
    .rigs-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; align-items: start; }
    @media (max-width: 1200px) { .rigs-grid { grid-template-columns: 1fr; } }
    .rig-section { background: #131a26; border: 1px solid #1e293b; border-radius: 8px; padding: 14px 16px; display: flex; flex-direction: column; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }
    .rig-header { display: flex; flex-wrap: wrap; justify-content: flex-start; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 10px; margin-bottom: 12px; gap: 16px; }
    .rig-title-badge { background: #090d16; border: 1px solid #1e293b; border-radius: 6px; padding: 6px 14px; color: #38bdf8; font-size: 18px; font-weight: bold; white-space: nowrap; display: flex; align-items: center; gap: 8px; }
    .sys-widget { background: #090d16; border: 1px solid #1e293b; border-radius: 6px; padding: 6px 12px; font-size: 13px; color: #94a3b8; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .temp-badge { padding: 2px 6px; border-radius: 4px; font-weight: bold; background: #064e3b; color: #34d399; }
    .temp-badge.warn { background: #78350f; color: #fbbf24; }
    .temp-badge.crit { background: #7f1d1d; color: #f87171; }
    .val-highlight { color: #f3f4f6; font-weight: bold; }
    .divider { color: #334155; }
    .grid { display: flex; gap: 10px; flex-wrap: wrap; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 10px 12px; width: 230px; box-sizing: border-box; }
    .card h4 { margin: 0 0 6px 0; color: #f8fafc; font-size: 15px; font-weight: 600; }
    .status { font-weight: bold; padding: 3px 8px; border-radius: 4px; display: inline-block; font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; }
    .AUTO_HUNTING { background: #059669; color: #ecfdf5; }
    .IN_GAME_IDLE { background: #d97706; color: #fffbeb; }
    .IN_QUEUE { background: #b45309; color: #fff; }
    .DISCONNECTED { background: #dc2626; color: #fef2f2; }
    .OFFLINE { background: #334155; color: #94a3b8; }
    .stats-box { margin-top: 10px; padding-top: 8px; border-top: 1px solid #334155; display: flex; flex-wrap: wrap; gap: 6px; }
    .stat-badge { background: #0f172a; border: 1px solid #334155; border-radius: 4px; padding: 4px 7px; font-size: 12px; display: flex; align-items: center; gap: 5px; }
    .stat-badge.is-zero { opacity: 0.35; }
    .stat-label { color: #64748b; font-weight: bold; font-size: 11px; }
    .stat-val { color: #ffffff; font-weight: bold; font-size: 13px; }
    .stat-val.diamond { color: #38bdf8; }
    .stat-val.cp { color: #38bdf8; }
</style>
</head>
<body>
    <div class="header-container">
        <div>
            <h1>🎮 Панель Управления</h1>
            <div class="sub">Автоматический мониторинг всех ПК в сети</div>
        </div>
        <div class="uptime-badge">
            <span>⏱️ Аптайм:</span>
            <span class="uptime-value" id="uptime-display">{{ uptime }}</span>
        </div>
    </div>
    {% if diamonds_by_game %}
    <div class="diamonds-panel">
        <span style="font-size:22px;">💎</span>
        {% for item in diamonds_by_game %}
        <div class="diamond-item">
            <span class="diamond-game">{{ item.game }}:</span>
            <span class="diamond-amount">{{ "{:,}".format(item.amount).replace(",", " ") }}</span>
            <span class="diamond-usd">≈ ${{ item.usd }}</span>
        </div>
        {% endfor %}
    </div>
    {% endif %}
    <div class="rigs-grid">
    {% for pc_name, windows in sorted_cluster %}
    <div class="rig-section">
        <div class="rig-header">
            <div class="rig-title-badge">🖥️ {{ pc_name }}</div>
            {% if sys_stats and sys_stats.get(pc_name) %}
            {% set s = sys_stats[pc_name] %}
            {% set cpu_stepped = (s.cpu // 5) * 5 %}
            <div class="sys-widget">
                <span>CPU:
                    {% if s.cpu_temp %}
                    <span class="temp-badge {% if s.cpu_temp > 80 %}crit{% elif s.cpu_temp > 70 %}warn{% endif %}">{{ s.cpu_temp }}°C</span>
                    {% endif %}
                    <span class="val-highlight">~{{ cpu_stepped }}%</span>
                </span>
                <span class="divider">|</span>
                <span>RAM: <span class="val-highlight">{{ s.ram_used_gb }}/{{ s.ram_total_gb }}G</span></span>
                {% if s.disk_used_gb and s.disk_total_gb %}
                <span class="divider">|</span>
                <span>DISK C: <span class="val-highlight">{{ s.disk_used_gb }}/{{ s.disk_free_gb }}/{{ s.disk_total_gb }}G</span></span>
                {% endif %}
            </div>
            {% endif %}
        </div>
        <div class="grid">
            {% for win_name, raw_state in windows|dictsort %}
            {% set status = raw_state.status if raw_state is mapping else raw_state %}
            {% set stats = raw_state.stats if raw_state is mapping and raw_state.stats else {} %}
            <div class="card">
                <h4>{{ win_name }}</h4>
                <div class="status {{ status }}">{{ status }}</div>
                <div class="stats-box">
                    {% set stat_config = [
                        ('level', 'LV', '', ''),
                        ('exp_percent', '%', '%', ''),
                        ('combat_power', '⚔️', '', 'cp'),
                        ('diamonds', '💎', '', 'diamond')
                    ] %}
                    {% for key, label, suffix, custom_cls in stat_config %}
                    {% set raw_val = stats.get(key, 0) %}
                    {% set is_zero = (raw_val == 0 or raw_val == 0.0 or raw_val == '0' or raw_val == '0.0') %}
                    <div class="stat-badge {% if is_zero %}is-zero{% endif %}" title="{{ key }}">
                        <span class="stat-label">{{ label }}</span>
                        <span class="stat-val {{ custom_cls }}">{{ raw_val }}{{ suffix }}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endfor %}
    </div>
    <script>
        async function updateUptime() {
            try {
                const response = await fetch('/api/uptime');
                const data = await response.json();
                if (data.uptime) document.getElementById('uptime-display').innerText = data.uptime;
            } catch (err) {}
        }
        setInterval(updateUptime, 1000);
    </script>
</body>
</html>
"""

@app.route("/api/nodes", methods=["GET"])
def api_nodes():
    return jsonify(get_registered_nodes())

@app.route("/")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        sorted_cluster=sort_rig_keys(cluster_states),
        sys_stats=system_states,
        uptime=get_formatted_uptime(),
        diamonds_by_game=calc_diamonds_by_game(),
    )


@app.route("/api/uptime")
def get_uptime_api():
    return jsonify({"uptime": get_formatted_uptime()})


@app.route("/api/update_status", methods=["POST"])
def api_update_status():
    data = request.json or {}
    pc_name = data.get("pc_name")
    window_name = data.get("window_name")
    status = data.get("status")
    raw_stats = data.get("stats")
    ip = data.get("ip") or request.remote_addr

    if not pc_name or not window_name:
        return jsonify({"status": "error", "message": "Missing fields"}), 400

    touch_pc(pc_name, ip)

    if pc_name not in cluster_states:
        cluster_states[pc_name] = {}

    if window_name not in cluster_states[pc_name]:
        cluster_states[pc_name][window_name] = {
            "status": status or "UNKNOWN",
            "stats": DEFAULT_STATS.copy(),
        }

    if status:
        cluster_states[pc_name][window_name]["status"] = status

    if raw_stats:
        game_key = get_game_prefix(window_name)
        clean = sanitize_stats(raw_stats, game_key=game_key)
        if clean:
            cluster_states[pc_name][window_name]["stats"].update(clean)

    return jsonify({"status": "ok"})


@app.route("/api/update_sys_stats", methods=["POST"])
def api_update_sys_stats():
    data = request.json or {}
    pc_name = data.get("pc_name")
    metrics = data.get("metrics")
    ip = data.get("ip") or request.remote_addr

    if pc_name and metrics:
        touch_pc(pc_name, ip)
        system_states[pc_name] = metrics
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 400


@app.route("/api/node_heartbeat", methods=["POST"])
def api_node_heartbeat():
    data = request.json or {}
    pc_name = data.get("pc_name")
    if not pc_name:
        return jsonify({"status": "error", "message": "pc_name required"}), 400

    ip = data.get("ip") or request.remote_addr
    engine_running = bool(data.get("engine_running", False))

    touch_pc(pc_name, ip)
    node_meta[pc_name] = {
        "engine_running": engine_running,
        "watchdog": True,
        "ts": time.time(),
    }
    return jsonify({"status": "ok"})

@app.route("/api/node_command", methods=["POST"])
def api_node_command():
    data = request.json or {}
    action = data.get("action")
    instance = data.get("instance")
    result = apply_node_command(action, instance)
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code

@app.route("/api/screenshot", methods=["GET"])
def api_screenshot():
    instance_id = request.args.get("instance")
    if not instance_id:
        return jsonify({"status": "error", "message": "Missing instance"}), 400

    target_hwnd = None
    for w in _local_tracked:
        if not isinstance(w, dict):
            continue
        if w.get("display_name") == instance_id or str(w.get("hwnd")) == str(instance_id):
            target_hwnd = w.get("hwnd")
            break

    if target_hwnd is None:
        try:
            target_hwnd = int(instance_id)
        except ValueError:
            return jsonify({"status": "error", "message": f"Unknown instance {instance_id}"}), 404

    try:
        from vision.vision import capture_window_frame
        import cv2
    except ImportError as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    frame = capture_window_frame(target_hwnd)
    if frame is None:
        return jsonify({"status": "error", "message": "capture failed"}), 404

    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return jsonify({"status": "error", "message": "encode failed"}), 500
    return Response(buffer.tobytes(), mimetype="image/jpeg")


class DashboardBridge:
    def __init__(
        self,
        server_ip: str = "127.0.0.1",
        pc_name: str = "Rig-Main",
        port: int = 5000,
        is_server: bool = True,
        node_ip: str = None,
    ):
        self.server_ip = server_ip
        self.pc_name = pc_name
        self.port = port
        self.is_server = is_server
        self.is_local = is_server and (server_ip in ("127.0.0.1", "localhost", "0.0.0.0"))
        self.base_url = f"http://{self.server_ip}:{self.port}"
        self.node_ip = node_ip  # LAN IP этой ноды для регистрации на Main

    def _send_async_post(self, endpoint: str, payload: dict):
        def _post():
            if not requests:
                return
            try:
                requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=1.0)
            except Exception:
                pass
        threading.Thread(target=_post, daemon=True).start()

    def _payload_base(self):
        p = {"pc_name": self.pc_name}
        if self.node_ip:
            p["ip"] = self.node_ip
        return p

    def update_status(self, window_name: str, state):
        if "launcher" in str(window_name).lower():
            return
        state_val = state.value if hasattr(state, "value") else str(state)
        touch_pc(self.pc_name, self.node_ip)

        if self.is_local:
            if self.pc_name not in cluster_states:
                cluster_states[self.pc_name] = {}
            if window_name not in cluster_states[self.pc_name]:
                cluster_states[self.pc_name][window_name] = {
                    "status": state_val,
                    "stats": DEFAULT_STATS.copy(),
                }
            elif isinstance(cluster_states[self.pc_name][window_name], dict):
                cluster_states[self.pc_name][window_name]["status"] = state_val
            else:
                cluster_states[self.pc_name][window_name] = {
                    "status": state_val,
                    "stats": DEFAULT_STATS.copy(),
                }
        else:
            payload = self._payload_base()
            payload.update({"window_name": window_name, "status": state_val})
            self._send_async_post("/api/update_status", payload)

    def update_stats(self, window_name: str, stats: dict):
        if "launcher" in str(window_name).lower():
            return
        game_key = get_game_prefix(window_name)
        clean_stats = sanitize_stats(stats, game_key=game_key)
        if not clean_stats:
            return
        touch_pc(self.pc_name, self.node_ip)

        if self.is_local:
            if self.pc_name not in cluster_states:
                cluster_states[self.pc_name] = {}
            if window_name not in cluster_states[self.pc_name]:
                merged = DEFAULT_STATS.copy()
                merged.update(clean_stats)
                cluster_states[self.pc_name][window_name] = {
                    "status": "UNKNOWN",
                    "stats": merged,
                }
            else:
                current = cluster_states[self.pc_name][window_name]
                if isinstance(current, dict):
                    current["stats"].update(clean_stats)
                else:
                    merged = DEFAULT_STATS.copy()
                    merged.update(clean_stats)
                    cluster_states[self.pc_name][window_name] = {
                        "status": str(current),
                        "stats": merged,
                    }
        else:
            payload = self._payload_base()
            payload.update({"window_name": window_name, "stats": clean_stats})
            self._send_async_post("/api/update_status", payload)

    def send_system_stats(self, metrics: dict):
        touch_pc(self.pc_name, self.node_ip)
        if self.is_local:
            system_states[self.pc_name] = metrics
        else:
            payload = self._payload_base()
            payload["metrics"] = metrics
            self._send_async_post("/api/update_sys_stats", payload)


def get_registered_nodes() -> dict:
    now = time.time()
    nodes = {}

    main_windows = list(cluster_states.get("Rig-Main", {}).keys())
    if not main_windows and _local_tracked:
        main_windows = [
            w.get("display_name")
            for w in _local_tracked
            if isinstance(w, dict) and w.get("display_name")
        ]

    nodes["Rig-Main"] = {
        "name": "Rig-Main (Сервер)",
        "is_local": True,
        "status": "online",
        "windows": main_windows,
        "ip": node_ips.get("Rig-Main") or "127.0.0.1",
        "engine_running": True,
        "watchdog_port": 5001,
    }

    known = set(last_seen.keys()) | set(node_meta.keys())
    for pc_name in known:
        if pc_name == "Rig-Main":
            continue
        ts = last_seen.get(pc_name, 0)
        meta = node_meta.get(pc_name) or {}
        meta_ts = meta.get("ts", 0)
        alive = (now - max(ts, meta_ts)) <= STALE_AFTER_SEC
        engine_running = bool(meta.get("engine_running", False)) if alive else False
        if not alive:
            status = "offline"
        elif engine_running:
            status = "online"
        else:
            status = "idle"  # watchdog жив, Engine выключен

        nodes[pc_name] = {
            "name": pc_name,
            "is_local": False,
            "status": status,
            "windows": list(cluster_states.get(pc_name, {}).keys()),
            "ip": node_ips.get(pc_name),
            "engine_running": engine_running,
            "watchdog_port": 5001,
        }
    return nodes


def start_dashboard_server(host="0.0.0.0", port=5000):
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host=host, port=port, debug=False, use_reloader=False)


def launch_dashboard_in_background(host="0.0.0.0", port=5000):
    cluster_states.clear()
    system_states.clear()
    last_seen.clear()
    node_ips.clear()
    node_meta.clear()

    threading.Thread(target=start_dashboard_server, args=(host, port), daemon=True).start()
    threading.Thread(target=_stale_watcher, daemon=True).start()
    print(f"[Дашборд] Веб-панель запущена на http://localhost:{port}")