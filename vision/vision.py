"""
Модуль компьютерного зрения (Vision).
Слой VISION.

Захватывает точные пиксели клиентской области окна через Desktop DC,
преобразуя координаты (0,0) клиентской зоны в абсолютные экранные.
Это решает проблему черных/белых экранов в DirectX и убирает сдвиг рамок.
"""
import os
import cv2
import numpy as np
import win32gui
import win32ui
import win32con
from typing import Optional, Dict, Any

from inputs.windows import get_client_area_rect


# vision.py

def capture_game_frame(hwnd: int) -> Optional[np.ndarray]:
    """
    Захватывает СТРОГО чистую клиентскую область окна (без шапки и рамок).
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        print(f"[Vision Error] Невалидный HWND: {hwnd}")
        return None

    # 1. Получаем точные размеры рабочей зоны
    left, top, width, height = get_client_area_rect(hwnd)
    
    # Исключаем свёрнутые и фантомные окна (порог 10px)
    if width <= 10 or height <= 10:
        return None

    # ... остальной код функции остается без изменений ...

    # 2. Берем контекст всего экрана (Desktop DC), чтобы прочитать реальные пиксели DirectX
    hwnd_dc = win32gui.GetWindowDC(0)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)

    # 3. Вырезаем кусок экрана строго по координатам клиентской зоны
    save_dc.BitBlt((0, 0), (width, height), mfc_dc, (left, top), win32con.SRCCOPY)

    # 4. Преобразуем пиксели в OpenCV BGR
    bmp_str = save_bitmap.GetBitmapBits(True)
    img = np.frombuffer(bmp_str, dtype=np.uint8)
    img.shape = (height, width, 4)

    # 5. Очистка ресурсов GDI (предотвращает утечки RAM)
    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(0, hwnd_dc)

    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    # 6. Canvas Guard: принудительная нормализация до эталонных 960x540
    h, w = frame.shape[:2]
    if w != 960 or h != 540:
        frame = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)

    return frame


# Алиас для сохранения полной совместимости со всеми вызовами в движке
capture_window_frame = capture_game_frame


def find_template_in_frame(
    frame: np.ndarray, 
    template_path: str, 
    confidence: float = 0.8
) -> Optional[Dict[str, Any]]:
    """
    Ищет шаблон изображения внутри готового кадра (np.ndarray).
    """
    if frame is None or not os.path.exists(template_path):
        return None

    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        print(f"[Vision Error] Ошибка чтения шаблона: {template_path}")
        return None

    th, tw = template.shape[:2]
    if frame.shape[0] < th or frame.shape[1] < tw:
        return None

    res = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= confidence:
        center_x = max_loc[0] + tw // 2
        center_y = max_loc[1] + th // 2
        return {
            "center": (center_x, center_y),
            "confidence": float(max_val),
            "rect": (max_loc[0], max_loc[1], tw, th)
        }

    return None


def find_template_in_window(
    hwnd: int, 
    template_path: str, 
    confidence: float = 0.8
) -> Optional[Dict[str, Any]]:
    """
    Захватывает свежий кадр окна по HWND и выполняет поиск шаблона.
    """
    frame = capture_game_frame(hwnd)
    if frame is None:
        return None
    return find_template_in_frame(frame, template_path, confidence=confidence)
# vision/vision.py

def find_template(
    frame: np.ndarray,
    template_path: str,
    mask_path: Optional[str] = None,
    search_zone: Optional[Dict[str, float]] = None,
    confidence: float = 0.8
) -> Optional[Dict[str, Any]]:
    """
    Универсальный поиск шаблона в кадре с поддержкой маски и ограничением зоны поиска.
    """
    if frame is None or not os.path.exists(template_path):
        return None

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = 0, 0, w, h

    # 1. Ограничение области поиска, если передана search_zone
    if search_zone and all(k in search_zone for k in ("x_pct", "y_pct", "w_pct", "h_pct")):
        x1 = int(search_zone["x_pct"] * w)
        y1 = int(search_zone["y_pct"] * h)
        x2 = int((search_zone["x_pct"] + search_zone["w_pct"]) * w)
        y2 = int((search_zone["y_pct"] + search_zone["h_pct"]) * h)

    search_area = frame[y1:y2, x1:x2]
    if search_area.size == 0:
        return None

    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        return None

    th, tw = template.shape[:2]
    if search_area.shape[0] < th or search_area.shape[1] < tw:
        return None

    # 2. Поиск с маской или без
    mask = None
    if mask_path and os.path.exists(mask_path):
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

    if mask is not None and mask.shape[:2] == (th, tw):
        res = cv2.matchTemplate(search_area, template, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        res = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)

    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= confidence:
        # Корректируем координаты относительного кропа к полному кадру
        abs_x = x1 + max_loc[0]
        abs_y = y1 + max_loc[1]
        return {
            "center": (abs_x + tw // 2, abs_y + th // 2),
            "confidence": float(max_val),
            "rect": (abs_x, abs_y, tw, th)
        }

    return None

def crop_right_of_template(
    frame: np.ndarray,
    template_path: str,
    mask_path: Optional[str] = None,
    search_zone: Optional[Dict[str, float]] = None,
    crop_width: int = 43,
    crop_height: int = 16,
    gap: int = 1,
    offset_y: int = 0,
    confidence: float = 0.85,
    save_debug: bool = False,
    debug_dir: Union[str, Path] = "debug_crops",
    window_name: str = "rf_next",
) -> Optional[np.ndarray]:
    if frame is None or not os.path.exists(template_path):
        return None

    h, w = frame.shape[:2]

    # 1. Расчет границ зоны поиска (search_zone)
    if search_zone and all(k in search_zone for k in ("x_pct", "y_pct", "w_pct", "h_pct")):
        zx1 = int(search_zone["x_pct"] * w)
        zy1 = int(search_zone["y_pct"] * h)
        zx2 = int((search_zone["x_pct"] + search_zone["w_pct"]) * w)
        zy2 = int((search_zone["y_pct"] + search_zone["h_pct"]) * h)
        search_img = frame[zy1:zy2, zx1:zx2]
    else:
        zx1, zy1, zx2, zy2 = 0, 0, w, h
        search_img = frame

    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        return None

    mask = cv2.imread(mask_path, cv2.IMREAD_COLOR) if mask_path and os.path.exists(mask_path) else None

    # 2. Поиск шаблона
    if mask is not None:
        res = cv2.matchTemplate(search_img, template, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        res = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)

    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    status = "FOUND" if max_val >= confidence else "FAILED"
    
    # 3. Логирование точности совпадения
    if save_debug:
        print(
            f"[DEBUG] [ICON_MATCH] win={window_name:<10} | "
            f"max_val={max_val:.3f} | threshold={confidence} | status={status}"
        )

    # 4. Дебаг-визуализация с цветной разметкой
    if save_debug:
        debug_img = frame.copy()
        
        # Желтый бокс (0, 255, 255) — Границы search_zone
        cv2.rectangle(debug_img, (zx1, zy1), (zx2, zy2), (0, 255, 255), 1)

        th, tw = template.shape[:2]
        icon_x1 = zx1 + max_loc[0]
        icon_y1 = zy1 + max_loc[1]
        icon_x2 = icon_x1 + tw
        icon_y2 = icon_y1 + th

        # Зеленый бокс (0, 255, 0) при успехе, Красный (0, 0, 255) при FAILED
        color = (0, 255, 0) if max_val >= confidence else (0, 0, 255)
        cv2.rectangle(debug_img, (icon_x1, icon_y1), (icon_x2, icon_y2), color, 1)

        # Синий бокс (255, 0, 0) — Итоговый кроп для цифр
        crop_x1 = icon_x2 + gap
        crop_y1 = icon_y1 + offset_y
        crop_x2 = crop_x1 + crop_width
        crop_y2 = crop_y1 + crop_height
        cv2.rectangle(debug_img, (crop_x1, crop_y1), (crop_x2, crop_y2), (255, 0, 0), 1)

        os.makedirs(debug_dir, exist_ok=True)
        filename = f"{window_name}_diamond_search_{status}.png"
        cv2.imwrite(os.path.join(debug_dir, filename), debug_img)

    if max_val < confidence:
        return None

    # 5. Кроп области цифр
    th, tw = template.shape[:2]
    icon_x2 = zx1 + max_loc[0] + tw
    icon_y1 = zy1 + max_loc[1]

    cx1 = icon_x2 + gap
    cy1 = icon_y1 + offset_y
    cx2 = cx1 + crop_width
    cy2 = cy1 + crop_height

    crop = frame[cy1:cy2, cx1:cx2]
    return crop if crop.size > 0 else None