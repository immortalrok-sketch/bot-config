"""
Модуль локального и сетевого веб-дэшборда (Flask).
Слой SERVICES.
Поддерживает работу в режиме Master (сервер) и Worker (клиент сети).
"""
import sys
import time
import re
import threading
import logging
from pathlib import Path
from flask import Flask, render_template_string, jsonify, request
from collections import defaultdict

# Пытаемся импортировать requests для отправки данных по сети на ПК-Воркерах
try:
    import requests
except ImportError:
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

app = Flask(__name__)
START_TIME = time.time()

# === Настройки цены бриллиантов ===
DIAMOND_PRICE_USD = {
    "rf_next": 0.006,   # $ за 1 бриллиант RF
    "ymir": 0.50,       # $ за 1 бриллиант Ymir
    "vampir": 0.30,
    "default": 0.50
}

DEFAULT_STATS = {
    "level": 0,
    "exp_percent": 0.0,
    "combat_power": 0,
    "diamonds": 0
}

# Внутрипамятные хранилища для Master-сервера
cluster_states = {}
system_states = {}

# === Валидация входных данных для Дашборда из config.json ===
def load_dashboard_ocr_rules() -> dict:
    rules = {
        "level": (1, 3),
        "combat_power": (5, 6),
        "diamonds": (3, 5)
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

STAT_DIGIT_LEN = load_dashboard_ocr_rules()

# dashboard_app.py (Строки ~64-88)

def sanitize_stats(stats: dict) -> dict:
    """
    Отфильтровывает некорректные значения статов на основе длин из config.json.
    Невалидные ключи отбрасываются, сохраняя предыдущие значения на дашборде.
    """
    if not isinstance(stats, dict):
        return {}

    clean_stats = {}
    for key, val in stats.items():
        if val is None:
            continue

        if key in STAT_DIGIT_LEN:
            min_len, max_len = STAT_DIGIT_LEN[key]
            digits_only = "".join(ch for ch in str(val) if ch.isdigit())
            
            # Если количество цифр вне диапазона [min, max] — игнорируем обновляемое поле
            if not (min_len <= len(digits_only) <= max_len):
                continue

        # ВСТАВКА: Фильтр отображения для diamonds (все что меньше 500 превращаем в 0)
        if key == "diamonds":
            try:
                num_val = int(val)
                val = num_val if num_val >= 500 else 0
            except (ValueError, TypeError):
                val = 0

        clean_stats[key] = val

    return clean_stats

def get_formatted_uptime() -> str:
    elapsed_seconds = int(time.time() - START_TIME)
    days = elapsed_seconds // 86400
    hours = (elapsed_seconds % 86400) // 3600
    minutes = (elapsed_seconds % 3600) // 60
    seconds = elapsed_seconds % 60
    return f"{days}д {hours:02d}ч {minutes:02d}м {seconds:02d}с"


def get_game_prefix(window_name: str) -> str:
    """rf_next_1 → rf_next, ymir_2 → ymir"""
    name = str(window_name).lower()
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name


def calc_diamonds_by_game():
    """Считает бриллианты и $ по каждой игре."""
    by_game = defaultdict(int)

    for pc_windows in cluster_states.values():
        for win_name, win_data in pc_windows.items():
            if not isinstance(win_data, dict):
                continue
            stats = win_data.get("stats", {})
            diamonds = stats.get("diamonds", 0) or stats.get("💎", 0) or 0
            try:
                diamonds = int(diamonds)
            except (ValueError, TypeError):
                diamonds = 0

            game = get_game_prefix(win_name)
            by_game[game] += diamonds

    result = []
    for game, amount in sorted(by_game.items()):
        price = DIAMOND_PRICE_USD.get(game, DIAMOND_PRICE_USD["default"])
        usd = round(amount * price, 2)
        result.append({
            "game": game.upper(),
            "amount": amount,
            "usd": usd,
            "price": price
        })
    return result


def sort_rig_keys(cluster_dict: dict) -> list:
    """
    Сортировка ПК:
    1. Компьютер с 'main' в имени всегда идет первым.
    2. Все остальные сортируются по встроенным числам (Rig-PC2, Rig-PC3 ... Rig-PC10).
    """
    def rig_sort_key(pc_name: str):
        name_lower = pc_name.lower()
        if "main" in name_lower:
            return (-1, 0, pc_name)
        
        numbers = re.findall(r'\d+', pc_name)
        if numbers:
            return (0, int(numbers[0]), pc_name)
        
        return (1, 0, pc_name)

    sorted_keys = sorted(cluster_dict.keys(), key=rig_sort_key)
    return [(k, cluster_dict[k]) for k in sorted_keys]


# Замените существующий HTML_TEMPLATE в dashboard_app.py на этот:

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Multi-PC Bot Dashboard</title>
    <meta http-equiv="refresh" content="3"> 
    <!-- 1. Обновленный блок стилей <style> -->
<style>
    body { 
        font-family: 'Consolas', 'Segoe UI', monospace; 
        background: #0b0f17; 
        color: #e2e8f0; 
        padding: 20px; 
        margin: 0;
    }
    .header-container {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 20px;
    }
    h1 { color: #38bdf8; margin: 0 0 4px 0; font-size: 26px; font-weight: 700; }
    .sub { color: #64748b; font-size: 14px; }
    
    .uptime-badge {
        background: #131a26;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 8px 16px;
        font-size: 14px;
        color: #94a3b8;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .uptime-value { color: #38bdf8; font-weight: bold; font-size: 15px; }

    .diamonds-panel {
        background: #131a26;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 12px 20px;
        margin-bottom: 20px;
        display: flex;
        flex-wrap: wrap;
        gap: 24px;
        align-items: center;
    }
    .diamond-item { display: flex; align-items: center; gap: 8px; font-size: 16px; }
    .diamond-game { color: #94a3b8; font-weight: bold; }
    .diamond-amount { color: #38bdf8; font-weight: bold; font-size: 19px; }
    .diamond-usd { color: #4ade80; font-size: 15px; font-weight: 600; }

    .rigs-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 16px;
        align-items: start;
    }

    @media (max-width: 1200px) {
        .rigs-grid { grid-template-columns: 1fr; }
    }

    .rig-section { 
        background: #131a26; 
        border: 1px solid #1e293b; 
        border-radius: 8px; 
        padding: 14px 16px; 
        display: flex;
        flex-direction: column;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
    }
    
    .rig-header { 
        display: flex; 
        flex-wrap: wrap;
        justify-content: space-between; 
        align-items: center; 
        border-bottom: 1px solid #1e293b; 
        padding-bottom: 10px; 
        margin-bottom: 12px; 
        gap: 12px;
    }
    
    /* Чёрная плашка под имя ПК */
.rig-header { 
        display: flex; 
        flex-wrap: wrap;
        justify-content: flex-start; /* Сдвигает метрики железа вплотную к имени ПК */
        align-items: center; 
        border-bottom: 1px solid #1e293b; 
        padding-bottom: 10px; 
        margin-bottom: 12px; 
        gap: 16px; /* Фиксированный аккуратный отступ между блоками */
    }

.rig-title-badge { 
        background: #090d16;
        border: 1px solid #1e293b;
        border-radius: 6px;
        padding: 6px 14px;
        color: #38bdf8; 
        font-size: 18px; 
        font-weight: bold; 
        white-space: nowrap;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    .win-count {
        color: #64748b;
        font-size: 13px;
        font-weight: normal;
    }

    .sys-widget { 
        background: #090d16; 
        border: 1px solid #1e293b; 
        border-radius: 6px; 
        padding: 6px 12px; 
        font-size: 13px; 
        color: #94a3b8; 
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        align-items: center;
    }
    
    .temp-badge { 
        padding: 2px 6px; 
        border-radius: 4px; 
        font-weight: bold; 
        background: #064e3b; 
        color: #34d399; 
    }
    .temp-badge.warn { background: #78350f; color: #fbbf24; }
    .temp-badge.crit { background: #7f1d1d; color: #f87171; }

    .val-highlight { color: #f3f4f6; font-weight: bold; }
    .divider { color: #334155; }

    .grid { display: flex; gap: 10px; flex-wrap: wrap; }
    
    .card { 
        background: #1e293b; 
        border: 1px solid #334155; 
        border-radius: 6px; 
        padding: 10px 12px; 
        width: 230px;
        box-sizing: border-box;
    }
    .card h4 { margin: 0 0 6px 0; color: #f8fafc; font-size: 15px; font-weight: 600; }
    
    .status { 
        font-weight: bold; 
        padding: 3px 8px; 
        border-radius: 4px; 
        display: inline-block; 
        font-size: 11px; 
        letter-spacing: 0.5px;
        text-transform: uppercase; 
    }

    .AUTO_HUNTING { background: #059669; color: #ecfdf5; }
    .IN_GAME_IDLE { background: #d97706; color: #fffbeb; }
    .IN_QUEUE { background: #b45309; color: #fff; }
    .DISCONNECTED { background: #dc2626; color: #fef2f2; }
    .OFFLINE { background: #334155; color: #94a3b8; }
    
    .stats-box {
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid #334155;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
    }
    
    .stat-badge {
        background: #0f172a;
        border: 1px solid #334155;
        border-radius: 4px;
        padding: 4px 7px;
        font-size: 12px;
        display: flex;
        align-items: center;
        gap: 5px;
    }
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

    <!-- === БРИЛЛИАНТЫ ПО ИГРАМ === -->
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
    {% set cpu_stepped = (s.cpu // 5) * 5 %}  {# Округление шагами по 5% для устранения мигания #}
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
                if (data.uptime) {
                    document.getElementById('uptime-display').innerText = data.uptime;
                }
            } catch (err) {}
        }
        setInterval(updateUptime, 1000);
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    diamonds_by_game = calc_diamonds_by_game()
    sorted_cluster = sort_rig_keys(cluster_states)
    return render_template_string(
        HTML_TEMPLATE, 
        sorted_cluster=sorted_cluster, 
        sys_stats=system_states,
        uptime=get_formatted_uptime(),
        diamonds_by_game=diamonds_by_game
    )

@app.route("/api/uptime")
def get_uptime_api():
    return jsonify({"uptime": get_formatted_uptime()})


# === СЕТЕВЫЕ ЭНДПОИНТЫ ДЛЯ ПРИЕМА ДАННЫХ ОТ ВОРКЕРОВ ===

@app.route("/api/update_status", methods=["POST"])
def api_update_status():
    """Принимает статусы окон от других ПК по сети."""
    data = request.json or {}
    pc_name = data.get("pc_name")
    window_name = data.get("window_name")
    status = data.get("status")
    raw_stats = data.get("stats")

    if not pc_name or not window_name:
        return jsonify({"status": "error", "message": "Missing fields"}), 400

    if pc_name not in cluster_states:
        cluster_states[pc_name] = {}

    if window_name not in cluster_states[pc_name]:
        cluster_states[pc_name][window_name] = {
            "status": status or "UNKNOWN",
            "stats": DEFAULT_STATS.copy()
        }

    if status:
        cluster_states[pc_name][window_name]["status"] = status
        
    if raw_stats:
        clean = sanitize_stats(raw_stats)
        if clean:
            cluster_states[pc_name][window_name]["stats"].update(clean)

    return jsonify({"status": "ok"})


@app.route("/api/update_sys_stats", methods=["POST"])
def api_update_sys_stats():
    """Принимает метрики железа от других ПК по сети."""
    data = request.json or {}
    pc_name = data.get("pc_name")
    metrics = data.get("metrics")

    if pc_name and metrics:
        system_states[pc_name] = metrics
        return jsonify({"status": "ok"})

    return jsonify({"status": "error"}), 400


class DashboardBridge:
    """Универсальный мост: если локальный ПК — пишет в память, если Воркер — шлет по HTTP."""

    def __init__(self, server_ip: str = "127.0.0.1", pc_name: str = "Rig-Main", port: int = 5000, is_server: bool = True):
        self.server_ip = server_ip
        self.pc_name = pc_name
        self.port = port
        self.is_server = is_server
        # Поле is_local строго True только если ПК одновременно Сервер И указывает на localhost
        self.is_local = is_server and (server_ip in ("127.0.0.1", "localhost", "0.0.0.0"))
        self.base_url = f"http://{self.server_ip}:{self.port}"

    def _send_async_post(self, endpoint: str, payload: dict):
        """Асинхронная отправка сетевого запроса без блокировки игровых потоков."""
        def _post():
            if not requests:
                return
            try:
                requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=1.0)
            except Exception:
                pass  # Тихо игнорируем сетевые сбои, чтобы не фризить бота

        threading.Thread(target=_post, daemon=True).start()

    def update_status(self, window_name: str, state):
        if "launcher" in str(window_name).lower():
            return

        state_val = state.value if hasattr(state, "value") else str(state)

        if self.is_local:
            if self.pc_name not in cluster_states:
                cluster_states[self.pc_name] = {}

            if window_name not in cluster_states[self.pc_name]:
                cluster_states[self.pc_name][window_name] = {
                    "status": state_val, 
                    "stats": DEFAULT_STATS.copy()
                }
            elif isinstance(cluster_states[self.pc_name][window_name], dict):
                cluster_states[self.pc_name][window_name]["status"] = state_val
            else:
                cluster_states[self.pc_name][window_name] = {
                    "status": state_val, 
                    "stats": DEFAULT_STATS.copy()
                }
        else:
            payload = {
                "pc_name": self.pc_name,
                "window_name": window_name,
                "status": state_val
            }
            self._send_async_post("/api/update_status", payload)

    def update_stats(self, window_name: str, stats: dict):
        if "launcher" in str(window_name).lower():
            return

        # Фильтруем данные перед сохранением/отправкой
        clean_stats = sanitize_stats(stats)
        if not clean_stats:
            return

        if self.is_local:
            if self.pc_name not in cluster_states:
                cluster_states[self.pc_name] = {}

            if window_name not in cluster_states[self.pc_name]:
                merged_stats = DEFAULT_STATS.copy()
                merged_stats.update(clean_stats)
                cluster_states[self.pc_name][window_name] = {
                    "status": "UNKNOWN",
                    "stats": merged_stats
                }
            else:
                current = cluster_states[self.pc_name][window_name]
                if isinstance(current, dict):
                    current["stats"].update(clean_stats)
                else:
                    merged_stats = DEFAULT_STATS.copy()
                    merged_stats.update(clean_stats)
                    cluster_states[self.pc_name][window_name] = {
                        "status": str(current),
                        "stats": merged_stats
                    }
        else:
            payload = {
                "pc_name": self.pc_name,
                "window_name": window_name,
                "stats": clean_stats
            }
            self._send_async_post("/api/update_status", payload)

    def send_system_stats(self, metrics: dict):
        if self.is_local:
            system_states[self.pc_name] = metrics
        else:
            payload = {
                "pc_name": self.pc_name,
                "metrics": metrics
            }
            self._send_async_post("/api/update_sys_stats", payload)


def start_dashboard_server(host="0.0.0.0", port=5000):
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host=host, port=port, debug=False, use_reloader=False)

def launch_dashboard_in_background(host="0.0.0.0", port=5000):
    cluster_states.clear()
    system_states.clear()

    server_thread = threading.Thread(
        target=start_dashboard_server, 
        args=(host, port), 
        daemon=True
    )
    server_thread.start()
    print(f"[Дашборд] Веб-панель запущена на http://localhost:{port}")
