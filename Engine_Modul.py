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
        "github_repo": "ВАШ_GITHUB_USER/ВАШ_REPO",
        "telegram": {
            "enabled": True,
            "token": "8945163867:AAF3Snlxz2J5K0acEujCzhdbH57zgD1MPHw",
            "admin_chat_id": 478279274
        }
    }

    # Жесткая привязка пути к корневой директории BASE_DIR
    node_cfg_path = os.path.join(BASE_DIR, "local_node.json")
    NODE_CFG = load_or_create_node_config(DEFAULT_NODE, filepath=node_cfg_path)

    IS_SERVER_PC = NODE_CFG.get("is_server_pc", True)
    SERVER_IP = NODE_CFG.get("server_ip", "127.0.0.1")
    PC_NAME = NODE_CFG.get("pc_name", "Rig-Main")
    GITHUB_REPO = NODE_CFG.get("github_repo")
    AUTO_UPDATE = NODE_CFG.get("auto_update", True)

    print(f"[INIT] Нода: '{PC_NAME}' | Режим Сервера: {IS_SERVER_PC} | IP Сервера: {SERVER_IP}")

    if AUTO_UPDATE and GITHUB_REPO and GITHUB_REPO != "ВАШ_GITHUB_USER/ВАШ_REPO":
        if check_and_apply_update(GITHUB_REPO, branch="main"):
            restart_process()

except Exception as e:
    print(f"[UPDATER WARN] Сбой инициализации автообновления: {e}")
    NODE_CFG = {}
    IS_SERVER_PC, SERVER_IP, PC_NAME = True, "127.0.0.1", "Rig-Main"

except Exception as e:
    print(f"[UPDATER WARN] Сбой инициализации автообновления: {e}")
    NODE_CFG = {}
    IS_SERVER_PC, SERVER_IP, PC_NAME = True, "127.0.0.1", "Rig-Main"

# --- Пакетные импорты модулей проекта ---
from core import config, launcher
from core.state_machine import GameState
from core.state_detector import WindowStateMonitor
from inputs import windows
from services.dashboard_app import launch_dashboard_in_background, DashboardBridge
from services.system_monitor import SystemMonitor
from services.telegram_bot import init_telegram_bot
import tools.generate_structure as generate_structure

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Process_Per_Monitor_DPI_Aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Обновляем карту MODULES.txt при старте
generate_structure.build_system_maps(".")


def main():
    # 0. Фиксация физического DPI 1:1 (исключает сдвиги рамок при масштабе 125%/150%)
    windows.init_dpi_awareness()

    print("=== Запуск модульного движка CyberFarm (Multi-Game) ===")

    # 1. Автозапуск веб-дашборда
    if IS_SERVER_PC:
        launch_dashboard_in_background(host="0.0.0.0", port=5000)
        time.sleep(1)

    # Передаем явно параметр is_server из local_node.json
    dashboard = DashboardBridge(server_ip=SERVER_IP, pc_name=PC_NAME, is_server=IS_SERVER_PC)

    # 2. Немедленный запуск мониторинга железа (CPU/GPU/RAM)
    print("\n=== Запуск фонового мониторинга железа (CPU/GPU) ===")
    sys_monitor = SystemMonitor(
        dashboard=dashboard,
        interval_sec=3,
        warn_gpu_temp=75,
        crit_gpu_temp=85
    )
    sys_monitor.start()

    main_cfg = config.load_main_config()

    # 3. Инициализация Telegram-бота из локального паспорта local_node.json
    tg_cfg = NODE_CFG.get("telegram", main_cfg.get("telegram", {}))

    if tg_cfg.get("enabled", True) and tg_cfg.get("token") and tg_cfg.get("admin_chat_id"):
        print("\n=== Инициализация Telegram Сервиса ===")
        try:
            init_telegram_bot(
                token=tg_cfg["token"],
                admin_chat_id=tg_cfg["admin_chat_id"],
                window_title="RF"
            )
        except Exception as e:
            print(f"[Telegram Предупреждение] Не удалось запустить бота: {e}")
            print("[Telegram Предупреждение] Движок продолжает работу без Telegram-управления.")
    else:
        print("\n[Telegram Инфо] Бот отключен в local_node.json или не указан токен. Пропуск.")

    active_games = main_cfg.get("active_games", [])

    if not active_games:
        print("[Ошибка] Список 'active_games' в main_config.json пуст.")
        return

    all_found_windows = []
    tracked_windows = []

    # 4. Последовательная проверка и цикличный дозапуск всех окон игр
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

        # Сканируем уже открытые окна в ОС
        hwnds = windows.get_game_windows(game_title) if game_title else []

        print(f"[Сканер] Найдено активных окон '{game_name}': {len(hwnds)} из {max_windows} целевых.")

        # Цикл дозапуска
        attempts = 0
        max_attempts = max_windows * 2

        while len(hwnds) < max_windows and attempts < max_attempts:
            attempts += 1
            current_slot = len(hwnds) + 1
            print(f"\n[Запуск] Запуск окна #{current_slot} из {max_windows} (Попытка {attempts})...")

            launcher_success = launcher.run_launcher_logic(game_cfg, slot_index=current_slot)
            if not launcher_success:
                print(f"[Предупреждение] Попытка автозапуска #{attempts} завершилась с ошибкой.")

            launch_delay = config.get_cfg_val(game_cfg, "timings", "game_launch_delay_min_sec", 10)
            print(f"[Загрузка] Ожидание {launch_delay} сек перед повторным сканированием окон...")
            time.sleep(launch_delay)

            hwnds = windows.get_game_windows(game_title) if game_title else []

        if len(hwnds) >= max_windows:
            print(f"[Сканер] Все {max_windows} окн(а) для '{game_name}' успешно найдены и подхвачены!")
        elif hwnds:
            print(f"[Предупреждение] Удалось запустить только {len(hwnds)} из {max_windows} окон.")
        else:
            print(f"[Ошибка] Ни одно окно для '{game_name}' так и не было обнаружено.")

        # Регистрируем каждое найденное окно
        if hwnds:
            for i, hwnd in enumerate(hwnds, start=1):
                display_name = f"{game_name}_{i}"
                dashboard.update_status(display_name, GameState.IN_GAME_IDLE)

                tracked_windows.append({
                    "hwnd": hwnd,
                    "display_name": display_name,
                    "game_name": game_name,
                    "window_title": game_title,
                    "game_cfg": game_cfg
                })

            all_found_windows.extend(hwnds)

    # 5. Глобальная расстановка окон
    print(f"\n--- Глобальная расстановка окон (Всего окон: {len(all_found_windows)}) ---")
    if all_found_windows:
        windows.arrange_windows(all_found_windows)
    else:
        print("[Окна] Игровые окна не обнаружены.")

    sys_monitor.set_tracked_windows(tracked_windows)

    # 6. Переход в режим постоянного мониторинга состояний
    print("\n=== Запуск системы мониторинга состояний через зрение ===")
    monitor = WindowStateMonitor(server_ip=SERVER_IP, pc_name=PC_NAME)
    monitor.start_monitoring_loop(tracked_windows, interval_sec=3)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[Критическая ошибка]: {e}")
    finally:
        print("\nДля продолжения нажмите любую клавишу . . .")
        os.system("pause > nul")
