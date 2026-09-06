import os
import cv2
import numpy as np

class DeathDetector:
    def __init__(self, profile):
        self.profile = profile
        self.templates_cache = {}
        # Путь к папке с изображениями
        self.images_dir = getattr(profile, 'images_dir', os.path.join('games', 'rf_next', 'images'))

    def _load_template(self, template_name):
        """Загружает картинку из кэша или с диска."""
        if template_name in self.templates_cache:
            return self.templates_cache[template_name]

        # Если в профиле есть метод get_template, вызываем его
        if hasattr(self.profile, 'get_template'):
            img = self.profile.get_template(template_name)
            if img is not None:
                self.templates_cache[template_name] = img
                return img

        # Иначе загружаем из папки
        file_path = os.path.join(self.images_dir, f"{template_name}.png")
        if os.path.exists(file_path):
            img = cv2.imread(file_path)
            self.templates_cache[template_name] = img
            return img

        print(f"[Vision Error] Файл шаблона не найден: {file_path}")
        return None

    def find_template(self, frame, template_name, threshold=0.7):
        if frame is None:
            return None

        template = self._load_template(template_name)
        if template is None:
            return None

        res = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            h, w = template.shape[:2]
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            return {
                'confidence': float(max_val),
                'center': (center_x, center_y),
                'box': (max_loc[0], max_loc[1], w, h)
            }

        return None

    def check_is_dead(self, frame):
        match = self.find_template(frame, "death_screen", threshold=0.75)
        return match is not None