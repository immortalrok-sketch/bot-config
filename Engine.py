import ctypes
import sys

# === 1. ПРОВЕРКА И ЗАПРОС ПРАВ АДМИНИСТРАТОРА ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

if not is_admin():
    # Перезапускаем скрипт с правами администратора
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

DEFAULT_CONFIG = {
    "version": "1.0.0",
    "update_server_url": "",
    "auto_update_enabled": False,
    "launcher_path": r"C:\Program Files\Netmarble\Netmarble Launcher\Netmarble Launcher.exe",
    "game_window_title": "RF ONLINE NEXT",
    "confidence_acc_inactive": 0.97,
    "confidence_play_button": 0.7,
    "wait_between_accounts_sec": 20
}

def load_local_config():
    """Загружает локальный config.json. Если его нет — создает базовый."""
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[Конфиг] Не удалось создать {CONFIG_FILE}: {e}")
        return DEFAULT_CONFIG

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[Конфиг] Ошибка чтения {CONFIG_FILE}, используем стандартные настройки: {e}")
        return DEFAULT_CONFIG

def check_for_updates(cfg):
    """Связывается с сервером/GitHub и обновляет локальный конфиг при наличии новой версии."""
    if not cfg.get("auto_update_enabled") or not cfg.get("update_server_url"):
        return cfg

    url = cfg["update_server_url"]
    print("[Апдейтер] Проверка обновлений с сервера...")

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Bot-Launcher/1.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            server_cfg = json.loads(response.read().decode('utf-8'))

        server_version = server_cfg.get("version", "1.0.0")
        local_version = cfg.get("version", "1.0.0")

        if server_version > local_version:
            print(f"[Апдейтер] Найдено обновление! ({local_version} -> {server_version})")
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(server_cfg, f, indent=4, ensure_ascii=False)
            print("[Апдейтер] Конфигурация успешно обновлена.")
            return server_cfg
        else:
            print(f"[Апдейтер] У вас актуальная версия конфига ({local_version}).")

    except urllib.error.URLError as e:
        print(f"[Апдейтер] Сервер недоступен ({e.reason}). Работаем на локальных настройках.")
    except Exception as e:
        print(f"[Апдейтер] Ошибка при проверке обновлений: {e}")

    return cfg


# === 4. ВСПОМОГАТЕЛЬНЫЕ ПУТИ И ЛОГИРОВАНИЕ ===
def get_path(filename):
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(application_path, 'images', filename)

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Настройка логирования ошибок
try:
    log_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
    sys.stderr = open(os.path.join(log_dir, 'error_log.txt'), 'a', encoding='utf-8')
except Exception:
        pass


# === 5. ИНИЦИАЛИЗАЦИЯ НАСТРОЕК И API WINDOWS ===
print("Бот запущен...")

# Загружаем настройки и проверяем обновления с сервера
CFG = load_local_config()
CFG = check_for_updates(CFG)

LAUNCHER_PATH = CFG.get("launcher_path", DEFAULT_CONFIG["launcher_path"])
GAME_WINDOW_TITLE = CFG.get("game_window_title", DEFAULT_CONFIG["game_window_title"])

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("top", ctypes.c_int),
                ("right", ctypes.c_int), ("bottom", ctypes.c_int)]

user32 = ctypes.WinDLL("user32")
dwmapi = ctypes.WinDLL("dwmapi")

# Автоматически определяем чистую рабочую зону Full HD экрана (БЕЗ панели задач)
monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))
work_area = monitor_info['Work']

SCREEN_W = work_area[2]    # 1920
WORK_H = work_area[3]      # Высота рабочей области (например, 1040)

HALF_W = SCREEN_W // 2     # 960
HALF_H = WORK_H // 2       # Высота окон (например, 520)

# Адаптивная сетка под Full HD с микро-коррекцией правого окна на 3 пикселя
ZONES = [
    (HALF_W - 3, 0, HALF_W + 3, HALF_H),      # 1-е окно: Справа-Сверху
    (0, HALF_H, HALF_W, WORK_H - HALF_H)      # 2-е окно: Слева-Снизу
]


# === 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВЗАИМОДЕЙСТВИЯ ===
def random_sleep(min_val, max_val):
    time.sleep(random.uniform(min_val, max_val))

def click_with_offset(base_x, base_y, offset=3):
    rx = base_x + random.randint(-offset, offset)
    ry = base_y + random.randint(-offset, offset)
    pyautogui.click(rx, ry)

def find_launcher_window():
    for w in gw.getAllWindows():
        if "Netmarble Launcher" in w.title or "Launcher" in w.title:
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
        print("Лаунчер не найден. Запускаем...")
        os.startfile(LAUNCHER_PATH)
        time.sleep(10)
        win = find_launcher_window()
    return win

def click_image(image_name, offset=5, confidence=0.8):
    img_path = get_path(image_name)
    try:
        pos = pyautogui.locateOnScreen(img_path, confidence=confidence)
        if pos:
            center_x = pos.left + pos.width // 2
            center_y = pos.top + pos.height // 2
            click_with_offset(center_x, center_y, offset)
            return True
    except Exception as e:
        print(f"[Ошибка click_image] {image_name}: {e}")
    return False


# === 7. ОСНОВНАЯ ЛОГИКА ЗАПУСКА И РАССТАНОВКИ ===
def run_accounts(launcher_win):
    print("--- Запуск аккаунтов (гибкий режим) ---")

    # --- ДОБАВЛЕНО: Активируем окно и даем ему время прорисоваться ДО поиска ---
    if launcher_win:
        try:
            launcher_win.restore()
            launcher_win.activate()
            time.sleep(1.5)  # Задержка, чтобы лаунчер успел открыться на экране
        except Exception as e:
            print(f"[Предупреждение] Не удалось активировать лаунчер: {e}")

    path_inactive = get_path('acc_inactive.png')

    # 1. Ищем ВСЕ доступные (неактивные) аккаунты
    conf_acc = CFG.get("confidence_acc_inactive", 0.8)
    accounts = []
    
    try:
        accounts = list(pyautogui.locateAllOnScreen(path_inactive, confidence=conf_acc))
    except Exception:
        pass # Если ничего не найдено, просто останется пустой список

    accounts = sorted(accounts, key=lambda box: (box.top, box.left))

    if not accounts:
        print("Нет доступных аккаунтов для запуска (возможно, все уже запущены).")
        return

    print(f"Найдено плашек для запуска: {len(accounts)}")

    # 2. Проходим по каждой найденной плашке
    for i, box in enumerate(accounts):
        if launcher_win:
            try:
                launcher_win.restore()
                launcher_win.activate()
                time.sleep(1)
            except Exception:
                pass

        # Клик по аккаунту
        x = box.left + box.width // 2
        y = box.top + box.height // 2
        pyautogui.click(x, y)
        time.sleep(1.5)

        # Клик по кнопке "Играть"
        conf_play = CFG.get("confidence_play_button", 0.8)
        wait_sec = CFG.get("wait_between_accounts_sec", 20)

        if click_image('play.png', confidence=conf_play):
            print(f"Аккаунт {i+1} успешно отправлен на запуск.")
            time.sleep(wait_sec)
        else:
            print(f"Аккаунт {i+1}: Кнопка 'Играть' не найдена, пропускаем.")

    print("--- Работа завершена ---")
    pyautogui.moveTo(0, 0)

def wait_and_arrange_windows():
    for attempt in range(60):
        possible_windows = [w for w in gw.getWindowsWithTitle(GAME_WINDOW_TITLE) if not w.isMinimized]

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

        if len(unique_game_windows) >= 2:
            print("[4/4] Игровые окна обнаружены. Раскладываем по сетке...")
            time.sleep(8)

            for idx, win in enumerate(unique_game_windows[:2]):
                target = ZONES[idx]
                hwnd = win._hWnd
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.5)

                    resize_with_dwm(hwnd, target[0], target[1], target[2], target[3])
                    print(f" -> Окно {idx+1} успешно выставлено в Full HD зону: {target}")
                except Exception as e:
                    print(f"[Предупреждение] Не удалось переместить окно {idx+1}: {e}")

            print("\n=== ВСЕ ГОТОВО. СКРИПТ ОТКЛЮЧИЛСЯ ===")
            return

        time.sleep(3)

    print("[Внимание] Окна игры не появились вовремя.")


# === 8. ТОЧКА ВХОДА ===
if __name__ == "__main__":
    win = get_or_start_launcher()
    run_accounts(win)
    wait_and_arrange_windows()

    # --- БЛОК АВТОЗАКРЫТИЯ КОНСОЛИ ---
    print("\n-------------------------------------------")
    for i in range(10, 0, -1):
        print(f"Окна выставлены. Закрытие консоли через {i} сек...", end="\r")
        time.sleep(1)

    print("\nВсе готово! Бот завершает работу. Удачи на фарме!")
    sys.exit()