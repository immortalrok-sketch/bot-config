import ctypes
from typing import List, Tuple
import win32gui

# 1. Принудительно отключаем виртуализацию масштабирования Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE_V2
except Exception as e:
    print(f"[WARN] Не удалось установить DPI Awareness: {e}")

TARGET_WIDTH = 960
TARGET_HEIGHT = 540


def get_rf_windows(title_pattern: str = "rf") -> List[Tuple[int, str]]:
    """Находит все HWND и заголовки окон, содержащие title_pattern (без учета регистра)."""
    found_windows = []

    def enum_handler(hwnd: int, extra: None) -> None:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title_pattern.lower() in title.lower():
                found_windows.append((hwnd, title))

    win32gui.EnumWindows(enum_handler, None)
    return found_windows


def inspect_canvas() -> None:
    """Анализирует фактический размер рабочей зоны (ClientRect) каждого окна."""
    windows = get_rf_windows("rf")

    if not windows:
        print("[CHECK] Окна с паттерном 'rf' не найдены.")
        return

    print("\n" + "=" * 70)
    print(f"{'HWND':<12} | {'TITLE':<20} | {'CLIENT SIZE':<12} | {'STATUS'}")
    print("=" * 70)

    for hwnd, title in windows:
        # Извлекаем чистую рабочую область без рамок и заголовка Windows
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width = right - left
        height = bottom - top

        if width == TARGET_WIDTH and height == TARGET_HEIGHT:
            status = "OK (960x540)"
        else:
            status = f"MISMATCH! (Нужен cv2.resize до 960x540)"

        print(
            f"{hex(hwnd):<12} | {title[:20]:<20} | {width}x{height:<7} | {status}"
        )

    print("=" * 70 + "\n")


if __name__ == "__main__":
    inspect_canvas()