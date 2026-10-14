"""Главная точка входа движка CyberFarm."""
import os
import sys
import time
import ctypes

# --- Явная настройка корневого пути проекта ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# --- 0. АВТООБНОВЛЕНИЕ И ЛОКАЛЬНЫЙ ПАСПОРТ НОДЫ ---
try:
    from updater import load_or_create_node_config, check_and_apply_update, restart_process

    DEFAULT_NODE = {
        "pc_name": "Rig-Main",
        "is_server_pc": True,
        "server_ip": "127.0.0.1",
        "node_ip": None,  # LAN IP этой ноды, напр. "192.168.0.45" (нужен воркерам)
        "github_repo": "ВАШ_GITHUB_USER/ВАШ_REPO",
        "auto_update": True,
        "telegram": {
            "enabled": True,  # на воркерах: false
            "token": "",
            "admin_chat_id": 0,
        },
    }

    node_cfg_path = os.path.join(BASE_DIR, "local_node.json")
    NODE_CFG = load_or_create_node_config(DEFAULT_NODE, filepath=node_cfg_path)

    IS_SERVER_PC = NODE_CFG.get("is_server_pc", True)
    SERVER_IP = NODE_CFG.get("server_ip", "127.0.0.1")
    PC_NAME = NODE_CFG.get("pc_name", "Rig-Main")
    NODE_IP = NODE_CFG.get("node_ip")
    GITHUB_REPO = NODE_CFG.get("github_repo")
    AUTO_UPDATE = NODE_CFG.get("auto_update", True)

    print(
        f"[INIT] Нода: '{PC_NAME}' | Сервер: {IS_SERVER_PC} | "
        f"IP сервера: {SERVER_IP} | node_ip: {NODE_IP}"
    )

    if AUTO_UPDATE and GITHUB_REPO and GITHUB_REPO != "ВАШ_GITHUB_USER/ВАШ_REPO":
        if check_and_apply_update(GITHUB_REPO, branch="main"):
            restart_process()

except Exception as e:
    print(f"[UPDATER WARN] Сбой инициализации автообновления: {e}")
    NODE_CFG = {}
    IS_SERVER_PC, SERVER_IP, PC_NAME, NODE_IP = True, "127.0.0.1", "Rig-Main", None

# --- Пакетные импорты модулей проекта ---
from core import config, launcher
from core.state_machine import GameState
from core.state_detector import WindowStateMonitor
from inputs import windows
from services.dashboard_app import (
    launch_dashboard_in_background,
    DashboardBridge,
    get_registered_nodes,
)
from services.system_monitor import SystemMonitor
from services.telegram_bot import init_telegram_bot
import tools.generate_structure as generate_structure

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

generate_structure.build_system_maps(".")


def main():
    windows.init_dpi_awareness()

    print("=== Запуск модульного движка CyberFarm (Multi-Game) ===")

    # 1. Flask API на КАЖДОЙ ноде (команды TG + screenshot)
    #    Main: ещё и веб-панель. Воркер: только API на :5000
    launch_dashboard_in_background(host="0.0.0.0", port=5000)
    time.sleep(1)

    dashboard = DashboardBridge(
        server_ip=SERVER_IP,
        pc_name=PC_NAME,
        is_server=IS_SERVER_PC,
        node_ip=NODE_IP,
    )

    # 2. Мониторинг железа
    print("\n=== Запуск фонового мониторинга железа (CPU/GPU) ===")
    sys_monitor = SystemMonitor(
        dashboard=dashboard,
        interval_sec=3,
        warn_gpu_temp=75,
        crit_gpu_temp=85,
    )
    sys_monitor.start()

    main_cfg = config.load_main_config()
    active_games = main_cfg.get("active_games", [])

    # 3. Telegram — только если enabled (на воркерах выключить в local_node.json)
    # 3. Telegram — только в watchdog на Main (не здесь, иначе Conflict)
    print("\n[Telegram] Пропуск в Engine — управляет watchdog", flush=True)
    if not active_games:
        print("[Ошибка] Список 'active_games' в main_config.json пуст.")
        return

    all_found_windows = []
    tracked_windows = []

    # 4. Запуск / подхват окон по играм
    for game_name in active_games:
        print("\n==================================================")
        print(f"   НАЧАЛО ОБРАБОТКИ ИГРЫ: {game_name.upper()}")
        print("==================================================")

        game_cfg = config.load_game_config(game_name)
        if not game_cfg:
            print(f"[Ошибка] Не удалось загрузить конфигурацию для '{game_name}'")
            continue

        game_cfg["_game_name"] = game_name
        game_title = config.get_cfg_val(game_cfg, "game", "window_title", "")
        max_windows = config.get_cfg_val(game_cfg, "game", "max_windows", 1)

        hwnds = windows.get_game_windows(game_title) if game_title else []
        print(
            f"[Сканер] Найдено активных окон '{game_name}': "
            f"{len(hwnds)} из {max_windows} целевых."
        )

        attempts = 0
        max_attempts = max_windows * 2

        while len(hwnds) < max_windows and attempts < max_attempts:
            attempts += 1
            current_slot = len(hwnds) + 1
            print(
                f"\n[Запуск] Запуск окна #{current_slot} из {max_windows} "
                f"(Попытка {attempts})..."
            )

            launcher_success = launcher.run_launcher_logic(
                game_cfg, slot_index=current_slot
            )
            if not launcher_success:
                print(
                    f"[Предупреждение] Попытка автозапуска #{attempts} "
                    f"завершилась с ошибкой."
                )

            launch_delay = config.get_cfg_val(
                game_cfg, "timings", "game_launch_delay_min_sec", 10
            )
            print(
                f"[Загрузка] Ожидание {launch_delay} сек "
                f"перед повторным сканированием окон..."
            )
            time.sleep(launch_delay)

            hwnds = windows.get_game_windows(game_title) if game_title else []

        if len(hwnds) >= max_windows:
            print(
                f"[Сканер] Все {max_windows} окн(а) для '{game_name}' "
                f"успешно найдены и подхвачены!"
            )
        elif hwnds:
            print(
                f"[Предупреждение] Удалось запустить только "
                f"{len(hwnds)} из {max_windows} окон."
            )
        else:
            print(
                f"[Ошибка] Ни одно окно для '{game_name}' "
                f"так и не было обнаружено."
            )

        if hwnds:
            for i, hwnd in enumerate(hwnds, start=1):
                display_name = f"{game_name}_{i}"
                dashboard.update_status(display_name, GameState.IN_GAME_IDLE)

                tracked_windows.append(
                    {
                        "hwnd": hwnd,
                        "display_name": display_name,
                        "game_name": game_name,
                        "window_title": game_title,
                        "game_cfg": game_cfg,
                    }
                )

            all_found_windows.extend(hwnds)

    # 5. Расстановка окон
    print(
        f"\n--- Глобальная расстановка окон "
        f"(Всего окон: {len(all_found_windows)}) ---"
    )
    if all_found_windows:
        windows.arrange_windows(all_found_windows)
    else:
        print("[Окна] Игровые окна не обнаружены.")

    sys_monitor.set_tracked_windows(tracked_windows)

    # 6. Мониторинг состояний (NODE_CONTROL / TG пауза внутри)
    print("\n=== Запуск системы мониторинга состояний через зрение ===")
    monitor = WindowStateMonitor(server_ip=SERVER_IP, pc_name=PC_NAME)
    monitor.start_monitoring_loop(tracked_windows, interval_sec=3)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[Критическая ошибка]: {e}")
    finally:
        # пауза только если запустили руками, не через watchdog/TG
        if os.environ.get("CYBERFARM_NOPAUSE") != "1":
            print("\nДля продолжения нажмите любую клавишу . . .")
            os.system("pause > nul")