"""
Модуль человекоподобного ввода мыши (Mouse Input).
Слой INPUTS.

Реализует алгоритмы кривых Безье, биомеханику движения рук человека,
рандомизацию таймингов и микро-тремор курсора.
Заменяется на работу с Arduino/KMBox при переходе на аппаратный уровень.
"""
import time
import random
import math
import win32api
import win32con
from typing import Tuple


def bezier_point(p0: Tuple[float, float], p1: Tuple[float, float], 
                 p2: Tuple[float, float], p3: Tuple[float, float], t: float) -> Tuple[float, float]:
    """Вычисляет координату точки на кубической кривой Безье для шага t в диапазоне [0, 1]."""
    x = (1 - t)**3 * p0[0] + 3 * (1 - t)**2 * t * p1[0] + 3 * (1 - t) * t**2 * p2[0] + t**3 * p3[0]
    y = (1 - t)**3 * p0[1] + 3 * (1 - t)**2 * t * p1[1] + 3 * (1 - t) * t**2 * p2[2 if len(p2)>2 else 1] if False else (1 - t)**3 * p0[1] + 3 * (1 - t)**2 * t * p1[1] + 3 * (1 - t) * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def human_move(target_x: int, target_y: int) -> None:
    """
    Перемещает курсор мыши в точку (target_x, target_y) по биомеханической дуге
    с замедлением на финише и микро-тремором.
    """
    start_x, start_y = win32api.GetCursorPos()
    if (start_x, start_y) == (target_x, target_y):
        return

    # Генерация случайных контрольных точек Безье для симуляции дуги руки
    distance = math.hypot(target_x - start_x, target_y - start_y)
    offset = min(distance * 0.3, 150)

    ctrl1_x = start_x + (target_x - start_x) * random.uniform(0.2, 0.4) + random.uniform(-offset, offset)
    ctrl1_y = start_y + (target_y - start_y) * random.uniform(0.2, 0.4) + random.uniform(-offset, offset)
    
    ctrl2_x = start_x + (target_x - start_x) * random.uniform(0.6, 0.8) + random.uniform(-offset * 0.5, offset * 0.5)
    ctrl2_y = start_y + (target_y - start_y) * random.uniform(0.6, 0.8) + random.uniform(-offset * 0.5, offset * 0.5)

    # Количество шагов зависит от расстояния (чем дальше — тем больше шагов)
    steps = max(20, int(distance / random.uniform(12.0, 18.0)))
    
    for i in range(steps + 1):
        t = i / steps
        # S-образное сглаживание скорости (медленный старт, разгон, плавное приземление)
        t_eased = math.sin(t * math.pi / 2)
        
        curr_x, curr_y = bezier_point((start_x, start_y), (ctrl1_x, ctrl1_y), (ctrl2_x, ctrl2_y), (target_x, target_y), t_eased)
        
        # На микро-шагах в середине пути добавляем естественный дрожь (тремор)
        if 0.15 < t < 0.85:
            curr_x += random.uniform(-1.2, 1.2)
            curr_y += random.uniform(-1.2, 1.2)

        win32api.SetCursorPos((int(curr_x), int(curr_y)))
        time.sleep(random.uniform(0.002, 0.006))


def human_click(x: int, y: int) -> None:
    """
    Полный цикл человекоподобного клика:
    Плавная доводка курсора -> Пауза прицеливания -> Зажатие -> Удержание -> Отпускание.
    """
    # 1. Плавная доводка мышью
    human_move(x, y)
    
    # 2. Небольшая задержка "прицеливания" перед кликом (80 - 160 мс)
    time.sleep(random.uniform(0.08, 0.16))
    
    # 3. Физическое нажатие
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    
    # 4. Время физического зажатия кнопки пальцем человеком (40 - 85 мс)
    time.sleep(random.uniform(0.04, 0.085))
    
    # 5. Отпускание кнопки
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    
    # 6. Микро-задержка после выполнения клика
    time.sleep(random.uniform(0.1, 0.25))