"""
Модуль работы с окнами Windows (Win32 API).
Слой INPUTS.

Универсальный системный слой: отвечает за поиск HWND, трансляцию координат,
географическую сортировку и математическую расстановку окон по сетке с компенсацией DWM-рамок.
"""
import subprocess
from typing import List, Tuple
import win32api
import win32con
import win32gui
import win32process

import ctypes

def init_dpi_awareness() -> None:
    """
    Принудительно переводит процесс в режим Per-Monitor DPI Aware (1:1 физические пиксели).
    Убирает размытие и сдвиги рамок Windows при системном масштабе 125%/150%.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_client_area_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """
    Возвращает точные экранные координаты рабочей (клиентской) области окна.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return (0, 0, 0, 0)

    try:
        _, _, width, height = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        return (left, top, width, height)
    except Exception as e:
        print(f"[Windows API Error] Ошибка клиентской зоны HWND {hwnd}: {e}")
        return (0, 0, 0, 0)


def client_to_screen(hwnd: int, x: int, y: int) -> Tuple[int, int]:
    """
    Преобразует локальные координаты клиентской зоны в глобальные координаты экрана.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return x, y
    try:
        return win32gui.ClientToScreen(hwnd, (x, y))
    except Exception as e:
        print(f"[Windows API Error] Ошибка трансляции координат HWND {hwnd}: {e}")
        return x, y


def click_hwnd(hwnd: int, x: int, y: int) -> None:
    """
    Виртуальный фоновый клик через сообщения Windows (WM_LBUTTON).
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return
    l_param = (y << 16) | (x & 0xFFFF)
    win32gui.SendMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_param)
    win32gui.SendMessage(hwnd, win32con.WM_LBUTTONUP, 0, l_param)


def get_window_position(hwnd: int) -> Tuple[int, int, int, int]:
    """
    Возвращает внешние координаты окна на рабочем столе (left, top, width, height).
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return (0, 0, 0, 0)
    try:
        rect = win32gui.GetWindowRect(hwnd)
        return (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
    except Exception:
        return (0, 0, 0, 0)


def sort_windows_spatially(hwnds: List[int]) -> List[int]:
    """
    Географическая сортировка HWND: сверху-вниз, слева-направо.
    """
    valid_hwnds = [h for h in hwnds if h and win32gui.IsWindow(h)]
    if not valid_hwnds:
        return []

    def get_sort_key(hwnd: int) -> Tuple[int, int]:
        left, top, _, _ = get_window_position(hwnd)
        return (top, left)

    return sorted(valid_hwnds, key=get_sort_key)


def get_game_windows(
    window_title: str,
    min_width: int = 100,
    min_height: int = 100,
    auto_sort: bool = True,
    only_visible: bool = True,
) -> List[int]:
    found_hwnds = []
    if not window_title:
        return found_hwnds

    target_title = window_title.lower().strip()

    def enum_windows_callback(hwnd, extra):
        if only_visible and not win32gui.IsWindowVisible(hwnd):
            return True
        # только верхний уровень
        if win32gui.GetParent(hwnd):
            return True
        try:
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if ex & win32con.WS_EX_TOOLWINDOW:
                return True
        except Exception:
            pass

        text = (win32gui.GetWindowText(hwnd) or "").lower().strip()
        if target_title and target_title in text:
            _, _, width, height = get_client_area_rect(hwnd)
            if width >= min_width and height >= min_height:
                found_hwnds.append(hwnd)
        return True

    win32gui.EnumWindows(enum_windows_callback, None)

    # уникальные hwnd, порядок как нашли
    unique = list(dict.fromkeys(found_hwnds))

    if auto_sort:
        return sort_windows_spatially(unique)
    return unique


def focus_window(hwnd: int) -> bool:
    """
    Выводит окно на передний план. Возвращает True, только если окно
    реально стало активным (иначе клики мышью попадут в чужое окно).
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        fore_hwnd = win32gui.GetForegroundWindow()
        print(f"[Focus DEBUG] target={hwnd} | current_foreground={fore_hwnd}")

        if fore_hwnd == hwnd:
            print(f"[Focus DEBUG] Окно {hwnd} уже в фокусе.")
            return True

        # Попытка 1: через AttachThreadInput (если не сработает — не страшно)
        attached = False
        fore_thread = curr_thread = 0
        if fore_hwnd:
            fore_thread, _ = win32process.GetWindowThreadProcessId(fore_hwnd)
            curr_thread = win32api.GetCurrentThreadId()
            if fore_thread and fore_thread != curr_thread:
                try:
                    win32process.AttachThreadInput(curr_thread, fore_thread, True)
                    attached = True
                except Exception as e:
                    print(f"[Focus DEBUG] AttachThreadInput не сработал: {e}")
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            print(f"[Focus DEBUG] SetForegroundWindow (попытка 1) не сработал: {e}")
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(curr_thread, fore_thread, False)
                except Exception:
                    pass

        # Попытка 2: обход Foreground Lock через короткое нажатие Alt
        if win32gui.GetForegroundWindow() != hwnd:
            print("[Focus DEBUG] Фокус не встал — пробую обход через Alt.")
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception as e:
                print(f"[Focus DEBUG] SetForegroundWindow (попытка 2) не сработал: {e}")
            finally:
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

        ok = win32gui.GetForegroundWindow() == hwnd
        print(f"[Focus DEBUG] Итог: foreground={win32gui.GetForegroundWindow()} | ожидали={hwnd} | success={ok}")
        return ok

    except Exception as e:
        print(f"[Windows API Error] Не удалось сфокусировать HWND {hwnd}: {e}")
        return False


def place_window_in_slot(
    hwnd: int,
    slot_index: int,
    cols: int = 2,
    rows: int = 2,
    bottom_padding: int = 40,
    dwm_offset_x: int = 8,
    dwm_offset_y: int = 8
) -> bool:
    """
    Точечная расстановка конкретного окна в нужный слот сетки.
    С интеллектуальной проверкой: если окно уже стоит на своем месте, 
    мы его не дергаем, чтобы не сбрасывать позиции при перезапуске бота.

    Возвращает True, только если окно реально было сдвинуто (False — уже стояло на месте или ошибка).
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False

    try:
        work_left, work_top, work_right, work_bottom = win32gui.SystemParametersInfo(win32con.SPI_GETWORKAREA)
        work_w = work_right - work_left
        work_h = work_bottom - work_top
    except Exception:
        work_left, work_top = 0, 0
        work_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        work_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

    effective_h = max(100, work_h - bottom_padding)
    slot_w = work_w // cols
    slot_h = effective_h // rows

    col = slot_index % cols
    row = slot_index // cols

    base_x = work_left + (col * slot_w)
    base_y = work_top + (row * slot_h)

    target_x = base_x - dwm_offset_x
    target_y = base_y
    target_w = slot_w + (dwm_offset_x * 2)
    target_h = slot_h + dwm_offset_y

    # 1. Узнаем текущие координаты окна на рабочем столе
    current_rect = get_window_position(hwnd)
    if current_rect != (0, 0, 0, 0):
        curr_x, curr_y, curr_w, curr_h = current_rect
        
        # 2. Проверяем, стоит ли окно уже на своем месте (с погрешностью в 3 пикселя на случай двоения рамок)
        if (
            abs(curr_x - target_x) <= 3 and 
            abs(curr_y - target_y) <= 3 and 
            abs(curr_w - target_w) <= 3 and 
            abs(curr_h - target_h) <= 3
        ):
            # Окно уже идеально стоит на своем месте, пропускаем перемещение
            return False

    # 3. Если координаты отличаются — ставим в слот
    try:
        win32gui.MoveWindow(hwnd, target_x, target_y, target_w, target_h, True)
        return True
    except Exception as e:
        print(f"[Windows Arrange Error] Ошибка точечной посадки HWND {hwnd} в слот #{slot_index}: {e}")
        return False


def arrange_windows(
    hwnds: List[int],
    cols: int = 2,
    rows: int = 2,
    bottom_padding: int = 40,
    dwm_offset_x: int = 8,
    dwm_offset_y: int = 8
) -> List[int]:
    """
    Умная расстановка:
    - окна, которые уже стоят в правильном слоте, не трогаем
    - остальные ставим в свободные слоты
    """
    if not hwnds:
        return []

    valid = [h for h in hwnds if h and win32gui.IsWindow(h)]
    if not valid:
        return []

    max_slots = cols * rows

    # Считаем целевые координаты всех слотов
    try:
        work_left, work_top, work_right, work_bottom = win32gui.SystemParametersInfo(win32con.SPI_GETWORKAREA)
        work_w = work_right - work_left
        work_h = work_bottom - work_top
    except Exception:
        work_left, work_top = 0, 0
        work_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        work_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

    effective_h = max(100, work_h - bottom_padding)
    slot_w = work_w // cols
    slot_h = effective_h // rows

    def slot_rect(idx: int):
        col = idx % cols
        row = idx // cols
        base_x = work_left + (col * slot_w)
        base_y = work_top + (row * slot_h)
        return (
            base_x - dwm_offset_x,
            base_y,
            slot_w + (dwm_offset_x * 2),
            slot_h + dwm_offset_y
        )

    # Какие слоты уже заняты правильно стоящими окнами
    occupied = {}  # slot_index -> hwnd
    remaining = []

    for hwnd in valid:
        curr = get_window_position(hwnd)
        if curr == (0, 0, 0, 0):
            remaining.append(hwnd)
            continue

        cx, cy, cw, ch = curr
        matched = False
        for idx in range(max_slots):
            if idx in occupied:
                continue
            tx, ty, tw, th = slot_rect(idx)
            if (
                abs(cx - tx) <= 5 and
                abs(cy - ty) <= 5 and
                abs(cw - tw) <= 5 and
                abs(ch - th) <= 5
            ):
                occupied[idx] = hwnd
                matched = True
                break
        if not matched:
            remaining.append(hwnd)

    # Свободные слоты
    free_slots = [i for i in range(max_slots) if i not in occupied]

    # Ставим только те окна, которые не на месте
    for hwnd, slot_idx in zip(remaining, free_slots):
        place_window_in_slot(
            hwnd=hwnd,
            slot_index=slot_idx,
            cols=cols,
            rows=rows,
            bottom_padding=bottom_padding,
            dwm_offset_x=dwm_offset_x,
            dwm_offset_y=dwm_offset_y
        )

    # Итоговый порядок: по слотам
    result = []
    for i in range(max_slots):
        if i in occupied:
            result.append(occupied[i])
    result.extend(remaining[:len(free_slots)])
    return result


def launch_process(exe_path: str) -> bool:
    """Запускает внешнее приложение по указанному пути."""
    try:
        subprocess.Popen(exe_path)
        return True
    except Exception as e:
        print(f"[Windows Input Error] Ошибка запуска {exe_path}: {e}")
        return False