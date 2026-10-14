import os
import sys
import time
from datetime import datetime
import ctypes
import win32gui
import win32ui
import win32con
import numpy as np
import cv2

# Определение BASE_DIR относительно корня проекта
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def get_available_games() -> list[str]:
    """Сканирует директорию games/ и возвращает список папок игр."""
    games_dir = os.path.join(BASE_DIR, "games")
    if not os.path.exists(games_dir):
        return []
    return [
        d for d in os.listdir(games_dir)
        if os.path.isdir(os.path.join(games_dir, d))
    ]


def get_open_windows() -> list[tuple[int, str]]:
    """Возвращает список открытых видимых окон (HWND, Title)."""
    windows = []

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title and title not in ["Program Manager", "Settings"]:
                windows.append((hwnd, title))

    win32gui.EnumWindows(enum_handler, None)
    return windows


def capture_window_printwindow(hwnd: int) -> np.ndarray | None:
    """Захват через PrintWindow (PW_RENDERFULLCONTENT = 2)."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        return None

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)

    try:
        # PW_RENDERFULLCONTENT = 2
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        if result != 1:
            return None

        bmp_str = save_bitmap.GetBitmapBits(True)
        img = np.frombuffer(bmp_str, dtype=np.uint8)
        img.shape = (height, width, 4)
        frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        # Проверка на полностью чёрный кадр
        if np.max(frame) == 0:
            return None

        return frame
    except Exception:
        return None
    finally:
        win32gui.DeleteObject(save_bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def capture_window_bitblt(hwnd: int) -> np.ndarray | None:
    """Запасной метод захвата через BitBlt."""
    rect = win32gui.GetWindowRect(hwnd)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]

    if width <= 0 or height <= 0:
        return None

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)

    try:
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)
        bmp_str = save_bitmap.GetBitmapBits(True)
        img = np.frombuffer(bmp_str, dtype=np.uint8)
        img.shape = (height, width, 4)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    except Exception:
        return None
    finally:
        win32gui.DeleteObject(save_bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def main():
    print("=" * 50)
    print("      УНИВЕРСАЛЬНЫЙ СКРИНШОТЕР UI / L2 / GAME      ")
    print("=" * 50)

    # 1. Выбор целевой игры
    games = get_available_games()
    if not games:
        print("[!] Папки игр не найдены в games/")
        game_folder = input("Введите имя папки игры вручную (например, rf_next): ").strip()
    else:
        print("\nВыберите целевую игру:")
        for idx, g in enumerate(games, 1):
            print(f"  [{idx}] {g}")
        
        choice = input(f"Введите номер (1-{len(games)}) [По умолчанию 1]: ").strip()
        selected_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(games) else 0
        game_folder = games[selected_idx]

    target_dir = os.path.join(BASE_DIR, "games", game_folder, "ui_screenshots")
    os.makedirs(target_dir, exist_ok=True)
    print(f"[+] Сохраняем в: {target_dir}")

    # 2. Выбор окна
    windows = get_open_windows()
    if not windows:
        print("[!] Не найдено открытых окон!")
        return

    print("\nДоступные окна:")
    for idx, (hwnd, title) in enumerate(windows, 1):
        print(f"  [{idx}] {title} (HWND: {hwnd})")

    win_choice = input(f"Выберите окно для снимка (1-{len(windows)}): ").strip()
    if not win_choice.isdigit() or not (1 <= int(win_choice) <= len(windows)):
        print("[!] Неверный выбор.")
        return

    selected_hwnd, selected_title = windows[int(win_choice) - 1]
    print(f"\n[+] Выбрано окно: '{selected_title}'")

    # 3. Интерактивный цикл создания скриншотов
    while True:
        print("\n" + "-" * 40)
        filename_input = input("Имя файла (нажми Enter для таймштампа, 'q' для выхода): ").strip()
        
        if filename_input.lower() == 'q':
            print("Выход из инструмента.")
            break

        if not filename_input:
            filename = f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        else:
            filename = filename_input if filename_input.endswith(".png") else f"{filename_input}.png"

        # Захват (PrintWindow -> Fallback BitBlt)
        frame = capture_window_printwindow(selected_hwnd)
        method = "PrintWindow"

        if frame is None:
            print("[!] PrintWindow вернул пустой/черный кадр. Пробуем BitBlt...")
            frame = capture_window_bitblt(selected_hwnd)
            method = "BitBlt"

        if frame is None:
            print("[ERROR] Не удалось захватить окно. Проверьте, не свернуто ли оно.")
            continue

        out_path = os.path.join(target_dir, filename)
        cv2.imwrite(out_path, frame)
        h, w, _ = frame.shape
        print(f"[OK] Успешно ({method}) -> {out_path} ({w}x{h})")


if __name__ == "__main__":
    main()