"""
Модуль загрузки и управления профилями игр.
Слой CORE.
"""
import os
import json
import cv2

# Вычисляем абсолютный корень проекта (на 1 уровень выше текущей папки core/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GameProfile:
    def __init__(self, profile_name: str):
        self.profile_name = profile_name.lower()
        
        # Полный абсолютный путь к папке игры от корня проекта
        self.profile_dir = os.path.join(BASE_DIR, "games", self.profile_name)
        self.config_path = os.path.join(self.profile_dir, "config.json")
        self.templates_dir = os.path.join(self.profile_dir, "images")

        self.config = self._load_config()
        self.templates = self._load_templates()

    def _load_config(self) -> dict:
        """Считывает JSON-конфиг игры по абсолютному пути."""
        if not os.path.exists(self.config_path):
            print(f"[ProfileLoader Error] Файл конфига не найден по пути: {self.config_path}")
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ProfileLoader Error] Ошибка чтения файла {self.config_path}: {e}")
            return {}

    def _load_templates(self) -> dict:
        """Загружает картинки из секции images или templates."""
        templates = {}
        # Читаем картинки из "images" или "templates" без падений
        template_map = self.config.get("images") or self.config.get("templates") or {}

        if isinstance(template_map, dict):
            for key, filename in template_map.items():
                if not isinstance(filename, str):
                    continue
                img_path = os.path.join(self.templates_dir, filename)
                if os.path.exists(img_path):
                    templates[key] = cv2.imread(img_path, cv2.IMREAD_COLOR)
                else:
                    print(f"[Warning] Шаблон {filename} не найден в {self.templates_dir}")
        return templates

    def get_launcher_path(self) -> str:
        """Безопасно извлекает путь к EXE из любого места в JSON."""
        launcher_cfg = self.config.get("launcher")
        raw_path = None

        if isinstance(launcher_cfg, dict):
            raw_path = launcher_cfg.get("path") or launcher_cfg.get("exe_path")
        elif isinstance(launcher_cfg, str):
            raw_path = launcher_cfg

        if not raw_path:
            raw_path = self.config.get("launcher_path")

        if not raw_path:
            return ""

        normalized_path = os.path.normpath(raw_path)
        return normalized_path if os.path.exists(normalized_path) else ""

    def crop_roi(self, frame, roi_name: str):
        """Вырезает из кадра окна указанную область (ROI)."""
        roi = self.config.get("rois", {}).get(roi_name)
        if not roi or frame is None:
            return None

        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        return frame[y : y + h, x : x + w]

    def get_setting(self, key: str, default=10):
        """Безопасное получение параметров из recovery_settings."""
        return self.config.get("recovery_settings", {}).get(key, default)