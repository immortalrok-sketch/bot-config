import cv2
import numpy as np

class TemplateFinder:
    """Универсальный поиск кнопок и иконок на экране через OpenCV."""

    @staticmethod
    def find_template(frame, template, threshold: float = 0.8):
        """
        Ищет шаблон на кадре.
        Возвращает ((center_x, center_y), confidence) или (None, confidence).
        """
        if frame is None or template is None:
            return None, 0.0

        # Поиск совпадения
        res = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            h, w = template.shape[:2]
            # Считаем точный центр найденной иконки/кнопки
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            return (center_x, center_y), max_val

        return None, max_val