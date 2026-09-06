import time
import random
import win32api
import win32con
import win32gui
from vision import find_template_in_window


def human_delay(min_sec: float = 0.3, max_sec: float = 0.8):
    """
    Выполняет рандомизированную паузу, имитируя естественную задержку реакции человека.
    """
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)


def wait_for_template(hwnd: int, image_path: str, timeout_sec: float = 10.0, 
                      confidence: float = 0.75, check_interval: float = 0.5) -> bool:
    """
    Умное ожидание появления шаблона на экране.
    Крутит цикл до появления картинки или истечения таймаута.
    """
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        if not win32gui.IsWindow(hwnd):
            return False

        if find_template_in_window(hwnd, image_path, confidence=confidence):
            return True

        # Небольшая случайная пауза между проверками
        human_delay(check_interval * 0.8, check_interval * 1.2)

    return False


def wait_until_disappears(hwnd: int, image_path: str, timeout_sec: float = 10.0, 
                           confidence: float = 0.75, check_interval: float = 0.5) -> bool:
    """
    Умное ожидание исчезновения шаблона (например, экрана загрузки или окна очереди).
    """
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        if not win32gui.IsWindow(hwnd):
            return True

        # Если картинка больше не находится — экран изменился
        if not find_template_in_window(hwnd, image_path, confidence=confidence):
            return True

        human_delay(check_interval * 0.8, check_interval * 1.2)

    return False


def click_window_bg(hwnd: int, x: int, y: int, random_offset: int = 4) -> bool:
    """
    Отправляет клик левой кнопкой мыши в фоновое окно по координатам (x, y).
    Добавляет случайный разброс пикселей, чтобы клик не был точечно идеальным.
    """
    if not win32gui.IsWindow(hwnd):
        return False

    # Смещение координат для 'человечности'
    final_x = x + random.randint(-random_offset, random_offset)
    final_y = y + random.randint(-random_offset, random_offset)

    # Упаковываем X и Y координаты в один 32-битный параметр для Win32 API
    l_param = win32api.MAKELONG(final_x, final_y)

    # 1. Зажатие кнопки мыши
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_param)
    
    # Случайная микро-задержка зажатия клавиши (от 40 до 110 мс)
    human_delay(0.04, 0.11)
    
    # 2. Отпускание кнопки мыши
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, l_param)

    print(f"[Действие] Фоновый клик по HWND {hwnd} в точку ({final_x}, {final_y})")
    return True