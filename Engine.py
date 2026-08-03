import ctypes
import sys

# === 1. ПРОВЕРКА И ЗАПРОС ПРАВ АДМИНИСТРАТОРА ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    sys.exit()


# === 2. ИМПОРТ БИБЛИОТЕК ===
import os
import time
import random
import json
import urllib.request
import urllib.error
import traceback

import pyautogui
import pygetwindow as gw
import win32process
import win32api
import win32gui
import win32con


# === 3. МОДУЛЬ КОНФИГУРАЦИИ И АПДЕЙТЕРА ===
CONFIG_FILE = "config.json"
BASE_RAW_URL = "https://raw.githubusercontent.com/immortalrok-sketch/bot-config/main/"

DEFAULT_CONFIG = {
    "version": "1.0.10",
    "update_server_url": "https://raw.githubusercontent.com/immortalrok-sketch/bot-config/main/config.json",
    "auto_update_enabled": True,
    "files_to_update": ["Engine.py", "acc_inactive.png", "acc_active.png", "play.png", "enter.png"],
    "launcher": {
        "path": "C:\\Program Files\\Netmarble\\Netmarble Launcher\\Netmarble Launcher.exe",
        "window_title": "Netmarble Launcher",
        "startup_wait_sec": 20,
        "activate_delay_sec": 1.5
    },
    "game": {
        "window_title": "RF ONLINE NEXT",
        "max_windows": 2
    },
    "images": {
        "acc_inactive": "acc_inactive.png",
        "acc_active": "acc_active.png",
        "play_button": "play.png",
        "enter_button": "enter.png"
    },
    "recognition": {
        "confidence_acc_inactive": 0.75,
        "confidence_acc_active": 0.75,
        "confidence_play_button": 0.8,
        "confidence_enter_button": 0.72,
        "click_offset_min_px": 3,
        "click_offset_max_px": 12
    },
    "timings": {
        "delay_after_acc_click_sec": 1.5,
        "play_button_timeout_sec": 10,
        "wait_between_accounts_sec": 20,
        "game_launch_delay_min_sec": 23,
        "game_launch_delay_max_sec": 26,
        "enter_button_timeout_sec": 15,
        "console_close_delay_sec": 7
    }
}

def get_cfg_val(cfg, group, key, default):
    try:
        return cfg.get(group, {}).get(key, default)
    except Exception:
        return default

def load_config():
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            print(f"[Конфиг] Создан файл по умолчанию: {CONFIG_FILE}")
            return DEFAULT_CONFIG
        except Exception as e:
            print(f"[Ошибка создания конфига]: {e}")
            return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    except Exception as e:
        print(f"[Ошибка чтения конфига]: {e}. Используем дефолтные настройки.")
        return DEFAULT_CONFIG

def download_file_from_github(filename):
    url = BASE_RAW_URL + filename
    try:
        print(f"[Апдейтер] Скачиваем {filename}...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(filename, 'wb') as out_file:
            out_file.write(response.read())
        return True
    except Exception as e:
        print(f"[Ошибка скачивания {filename}]: {e}")
        return False

def check_and_update_all(local_cfg):
    if not local_cfg.get("auto_update_enabled", True):
        return local_cfg

    server_url = local_cfg.get("update_server_url")
    if not server_url:
        return local_cfg

    print("[Апдейтер] Проверка обновлений с сервера...")
    try:
        req = urllib.request.Request(server_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            remote_cfg = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"[Апдейтер] Не удалось проверить обновления: {e}")
        return local_cfg

    local_ver = local_cfg.get("version", "1.0.0")
    remote_ver = remote_cfg.get("version", "1.0.0")

    if remote_ver > local_ver:
        print(f"[Апдейтер] Найдена новая версия {remote_ver} (текущая: {local_ver})! Начинаем обновление...")
        files_list = remote_cfg.get("files_to_update", [])
        engine_was_updated = False

        for filename in files_list:
            if download_file_from_github(filename):
                if filename == "Engine.py":
                    engine_was_updated = True

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(remote_cfg, f, indent=4, ensure_ascii=False)
            print("[Апдейтер] Конфигурация успешно обновлена!")
        except Exception as e:
            print(f"[Ошибка сохранения config.json]: {e}")

        if engine_was_updated:
            print("[Апдейтер] Код скрипта обновлен. Перезапуск бота...")
            os.execv(sys.executable, [sys.executable] + sys.argv)

        return remote_cfg
    else:
        print(f"[Апдейтер] У вас актуальная версия конфига ({local_ver}).")
        return local_cfg


# === 4. ВСПОМОГАТЕЛЬНЫЕ ПУТИ И ЛОГИРОВАНИЕ ===
def get_path(filename):
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
    
    root_path = os.path.join(application_path, filename)
    if os.path.exists(root_path):
        return root_path
    return os.path.join(application_path, 'images', filename)

try:
    log_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
    sys.stderr = open(os.path.join(log_dir, 'error_log.txt'), 'a', encoding='utf-8')
except Exception:
    pass


# === 5. ИНИЦИАЛИЗАЦИЯ НАСТРОЕК И API WINDOWS ===
print("Бот запущен...")

CFG = load_config()
CFG = check_and_update_all(CFG)

user32 = ctypes.WinDLL("user32")
dwmapi = ctypes.WinDLL("dwmapi")


# === 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВЗАИМОДЕЙСТВИЯ ===
def force_focus_window(hwnd):
    """Фокусирует окно."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    except Exception as e:
        print(f"[Предупреждение] Не удалось сфокусировать окно: {e}")

def random_sleep(min_sec, max_sec):
    """Пауза со случайным плавающим временем."""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)

def click_with_offset(base_x, base_y, min_offset=None, max_offset=None):
    """Клик с динамическим рандомным разбросом координат."""
    if min_offset is None:
        min_offset = get_cfg_val(CFG, "recognition", "click_offset_min_px", 3)
    if max_offset is None:
        max_offset = get_cfg_val(CFG, "recognition", "click_offset_max_px", 12)

    offset_x = random.randint(min_offset, max_offset) * random.choice([-1, 1])
    offset_y = random.randint(min_offset, max_offset) * random.choice([-1, 1])
    
    rx = base_x + offset_x
    ry = base_y + offset_y

    time.sleep(random.uniform(0.1, 0.3))
    pyautogui.click(rx, ry)

def find_launcher_window():
    title_key = get_cfg_val(CFG, "launcher", "window_title", "Launcher")
    for w in gw.getAllWindows():
        if title_key.lower() in w.title.lower() or "launcher" in w.title.lower():
            return w
    return None

def get_process_name_by_hwnd(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        exe_name = win32process.GetModuleFileNameEx(handle, 0)
        win32api.CloseHandle(handle)
        return exe_name.lower()
    except Exception:
        return ""

def resize_with_dwm(hwnd, target_x, target_y, target_w, target_h):
    win32gui.SetWindowPos(
        hwnd, 0,
        target_x, target_y, target_w, target_h,
        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    )

def get_or_start_launcher():
    win = find_launcher_window()
    if not win:
        launcher_path = get_cfg_val(CFG, "launcher", "path", DEFAULT_CONFIG["launcher"]["path"])
        startup_wait = get_cfg_val(CFG, "launcher", "startup_wait_sec", 20)
        print("Лаунчер не найден. Запускаем...")
        os.startfile(launcher_path)
        random_sleep(startup_wait - 2, startup_wait + 3)
        win = find_launcher_window()
    return win

def get_inactive_accounts(path_inactive, path_active, conf_acc):
    """Надежный поиск неактивных плашек с фильтрацией дубликатов."""
    active_pos = None
    try:
        active_pos = pyautogui.locateOnScreen(path_active, confidence=conf_acc)
        print(f"[Лаунчер] Активная плашка найдена в координатах: {active_pos}")
    except Exception:
        print("[Лаунчер] Активная плашка (acc_active.png) не найдена на экране.")

    raw_inactive = []
    try:
        raw_inactive = list(pyautogui.locateAllOnScreen(path_inactive, confidence=conf_acc))
    except Exception:
        pass

    print(f"[Лаунчер] Сырых неактивных плашек найдено: {len(raw_inactive)}")

    inactive_boxes = []
    for box in raw_inactive:
        if active_pos:
            cx_in = box.left + box.width // 2
            cy_in = box.top + box.height // 2
            cx_act = active_pos.left + active_pos.width // 2
            cy_act = active_pos.top + active_pos.height // 2
            if abs(cx_in - cx_act) < 30 and abs(cy_in - cy_act) < 30:
                continue

        is_duplicate = False
        for existing in inactive_boxes:
            if abs(existing.left - box.left) < 15 and abs(existing.top - box.top) < 15:
                is_duplicate = True
                break
        if not is_duplicate:
            inactive_boxes.append(box)

    inactive_boxes.sort(key=lambda b: (b.top, b.left))
    print(f"[Лаунчер] Чистых уникальных неактивных плашек для выбора: {len(inactive_boxes)}")
    return active_pos, inactive_boxes

def wait_and_click_image(image_name, timeout=10, confidence=None, region=None):
    if confidence is None:
        confidence = get_cfg_val(CFG, "recognition", "confidence_play_button", 0.8)
        
    img_path = get_path(image_name)
    if not os.path.exists(img_path):
        print(f"[Ошибка] Файл не найден: {img_path}")
        return False

    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            if region:
                pos = pyautogui.locateOnScreen(img_path, confidence=confidence, region=region)
            else:
                pos = pyautogui.locateOnScreen(img_path, confidence=confidence)
                
            if pos:
                center_x = pos.left + pos.width // 2
                center_y = pos.top + pos.height // 2
                click_with_offset(center_x, center_y)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# === 7. ОСНОВНАЯ ЛОГИКА ЗАПУСКА И РАССТАНОВКИ ===
def run_accounts(launcher_win):
    print("--- Запуск аккаунтов ---")

    act_delay = get_cfg_val(CFG, "launcher", "activate_delay_sec", 1.5)
    img_inactive = get_cfg_val(CFG, "images", "acc_inactive", "acc_inactive.png")
    path_inactive = get_path(img_inactive)
    img_active = get_cfg_val(CFG, "images", "acc_active", "acc_active.png")
    path_active = get_path(img_active)
    img_play = get_cfg_val(CFG, "images", "play_button", "play.png")
    
    conf_acc = get_cfg_val(CFG, "recognition", "confidence_acc_inactive", 0.75)
    conf_play = get_cfg_val(CFG, "recognition", "confidence_play_button", 0.8)
    
    delay_acc_click = get_cfg_val(CFG, "timings", "delay_after_acc_click_sec", 1.5)
    play_timeout = get_cfg_val(CFG, "timings", "play_button_timeout_sec", 10)
    wait_sec = get_cfg_val(CFG, "timings", "wait_between_accounts_sec", 20)
    max_windows = get_cfg_val(CFG, "game", "max_windows", 2)

    launched_count = 0
    launched_centers = [] # Список координат уже запущенных аккаунтов

    for i in range(max_windows):
        print(f"\n================ [ Запуск аккаунта {i + 1} из {max_windows} ] ================")
        
        if launcher_win:
            force_focus_window(launcher_win._hWnd)
            random_sleep(act_delay - 0.3, act_delay + 0.5)

        active_pos, inactive_boxes = get_inactive_accounts(path_inactive, path_active, conf_acc)

        # Фильтруем плашки: исключаем те, на которые уже кликали в предыдущих итерациях
        fresh_boxes = []
        for box in inactive_boxes:
            cx = box.left + box.width // 2
            cy = box.top + box.height // 2
            
            is_already_launched = False
            for lx, ly in launched_centers:
                if abs(cx - lx) < 40 and abs(cy - ly) < 40:
                    is_already_launched = True
                    break
            
            if not is_already_launched:
                fresh_boxes.append((box, cx, cy))

        print(f" -> Доступных новых плашек после проверки: {len(fresh_boxes)}")

        if fresh_boxes:
            target_box, cx, cy = fresh_boxes[0]
            print(f" -> Кликаем по новой плашке аккаунта ({cx}, {cy})...")
            click_with_offset(cx, cy)
            random_sleep(delay_acc_click, delay_acc_click + 1.0)
            launched_centers.append((cx, cy))
        else:
            if i == 0 and active_pos:
                print(" -> [Аккаунт 1] Уже выбран по умолчанию (активна плашка). Кликать не нужно.")
            else:
                print(f" -> [Предупреждение] Не удалось найти новую неактивную плашку для аккаунта {i + 1}.")
                break

        print(f" -> Ожидание и клик по кнопке 'Играть'...")
        if wait_and_click_image(img_play, timeout=play_timeout, confidence=conf_play):
            launched_count += 1
            print(f" -> УСПЕХ: Аккаунт {launched_count} запущен!")
            if launched_count < max_windows:
                random_wait = random.uniform(wait_sec - 2, wait_sec + 4)
                print(f" -> Ожидание {random_wait:.1f} сек перед выбором следующего аккаунта...")
                time.sleep(random_wait)
        else:
            print(f" -> [Ошибка] Кнопка 'Играть' не распознана.")
            break

    if launcher_win:
        try:
            win32gui.ShowWindow(launcher_win._hWnd, win32con.SW_MINIMIZE)
            print("\nЛаунчер свернут.")
        except Exception:
            pass

    print("--- Работа с лаунчером завершена ---")
    return launched_count

def wait_and_arrange_windows(target_count=None):
    game_title = get_cfg_val(CFG, "game", "window_title", "RF ONLINE NEXT")
    max_win_cfg = get_cfg_val(CFG, "game", "max_windows", 2)
    
    # Автоматически генерируем 4 угла под разрешение твоего монитора
    screen_w, screen_h = pyautogui.size()
    half_w = screen_w // 2
    half_h = screen_h // 2

# Кастомная расстановка по углам:
    # 0 -> Правый верх
    # 1 -> Левый низ
    # 2 -> Правый низ
    # 3 -> Левый верх (сверху слева)
    grid_positions = [
        {"x": half_w, "y": 0, "w": screen_w - half_w, "h": half_h},                 # 1. Правый верх
        {"x": 0, "y": half_h, "w": half_w, "h": screen_h - half_h},                 # 2. Левый низ
        {"x": half_w, "y": half_h, "w": screen_w - half_w, "h": screen_h - half_h}, # 3. Правый низ
        {"x": 0, "y": 0, "w": half_w, "h": half_h}                                  # 4. Левый верх
    ]

    if target_count is None or target_count == 0:
        target_count = max_win_cfg

    required_windows = min(target_count, len(grid_positions))

    min_launch_delay = get_cfg_val(CFG, "timings", "game_launch_delay_min_sec", 23)
    max_launch_delay = get_cfg_val(CFG, "timings", "game_launch_delay_max_sec", 26)
    launch_delay = random.uniform(min_launch_delay, max_launch_delay)

    print(f"\nПауза {launch_delay:.1f} сек для подгрузки клиента игры...")
    time.sleep(launch_delay)

    print(f"Ожидание появления игровых окон (цель: {required_windows})...")
    for attempt in range(60):
        possible_windows = [w for w in gw.getWindowsWithTitle(game_title) if not w.isMinimized]
        
        unique_game_windows = []
        seen_pids = set()

        for win in possible_windows:
            if win.title == "":
                continue
            
            window_class = win32gui.GetClassName(win._hWnd)
            if window_class == "Chrome_WidgetWin_1":
                continue

            _, win_pid = win32process.GetWindowThreadProcessId(win._hWnd)
            if win_pid not in seen_pids:
                exe_name = get_process_name_by_hwnd(win._hWnd)
                if any(b in exe_name for b in ["chrome", "browser", "edge", "opera"]):
                    continue
                if "launcher" in exe_name:
                    continue

                seen_pids.add(win_pid)
                unique_game_windows.append(win)

        if len(unique_game_windows) >= required_windows:
            print(f"[{len(unique_game_windows)}/{required_windows}] Игровые окна обнаружены. Раскладываем по углам...")
            random_sleep(2.0, 4.0)

            for idx, win in enumerate(unique_game_windows[:required_windows]):
                pos = grid_positions[idx]
                target_x, target_y, target_w, target_h = pos["x"], pos["y"], pos["w"], pos["h"]
                hwnd = win._hWnd
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.5)
                    resize_with_dwm(hwnd, target_x, target_y, target_w, target_h)
                    print(f" -> Окно {idx+1} успешно выставлено: ({target_x}, {target_y}, {target_w}, {target_h})")
                except Exception as e:
                    print(f"[Предупреждение] Не удалось переместить окно {idx+1}: {e}")

            print(f"\n--- Фокус, активация и нажатие кнопки 'Выбрать' ---")
            img_enter = get_cfg_val(CFG, "images", "enter_button", "enter.png")
            conf_enter = get_cfg_val(CFG, "recognition", "confidence_enter_button", 0.72)
            enter_timeout = get_cfg_val(CFG, "timings", "enter_button_timeout_sec", 15)

            for idx, win in enumerate(unique_game_windows[:required_windows]):
                hwnd = win._hWnd
                print(f"\n[Окно {idx+1}] Активация...")
                force_focus_window(hwnd)
                random_sleep(0.8, 1.5)

                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    center_x = (rect[0] + rect[2]) // 2
                    center_y = (rect[1] + rect[3]) // 2

                    center_offset_x = random.randint(15, 40) * random.choice([-1, 1])
                    center_offset_y = random.randint(15, 40) * random.choice([-1, 1])

                    print(f" -> Клик для фокуса в точке ({center_x + center_offset_x}, {center_y + center_offset_y})...")
                    click_with_offset(center_x + center_offset_x, center_y + center_offset_y, min_offset=2, max_offset=8)
                except Exception as e:
                    print(f"[Ошибка] Не удалось кликнуть в окне {idx+1}: {e}")

                print(f" -> Ожидание и клик по кнопке 'Выбрать'...")
                if wait_and_click_image(img_enter, timeout=enter_timeout, confidence=conf_enter):
                    print(f" -> УСПЕХ: Кнопка 'Выбрать' нажата в окне {idx+1}")
                else:
                    print(f" -> [Предупреждение] Кнопка 'Выбрать' не распознана в окне {idx+1}.")

                time.sleep(1.0)

            print(f"\n=== ВСЕ ГОТОВО. СКРИПТ ОТКЛЮЧИЛСЯ ===")
            return

        time.sleep(3)

    print("[Внимание] Окна игры не появились вовремя.")


# === 8. ТОЧКА ВХОДА ===
if __name__ == "__main__":
    win = get_or_start_launcher()
    count_launched = run_accounts(win)
    wait_and_arrange_windows(target_count=count_launched)

    close_delay = get_cfg_val(CFG, "timings", "console_close_delay_sec", 7)
    print("\n-------------------------------------------")
    for i in range(close_delay, 0, -1):
        print(f"Окна выставлены. Закрытие консоли через {i} сек...", end="\r")
        time.sleep(1)

    print("\nВсе готово! Бот завершает работу.")
    sys.exit()
