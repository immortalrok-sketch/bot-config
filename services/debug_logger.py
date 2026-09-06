import os
import sys
import json
import logging
from datetime import datetime
from collections import deque
import pyautogui


class RingBufferHandler(logging.Handler):
    """
    Кастомный хэндлер для перехвата логов в кольцевой буфер (память).
    Хранит последние N записей без нагрузки на диск.
    """
    def __init__(self, capacity: int = 30):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        try:
            # Форматируем сообщение и кладём в буфер
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)


class DebugLogger:
    """
    Сервис сквозной отладки и аварийного дампинга (Черный ящик).
    """
    def __init__(self, buffer_size: int = 30, logs_dir: str = "logs"):
        self.logs_dir = logs_dir
        self.ring_handler = RingBufferHandler(capacity=buffer_size)
        
        # Создаем базовую директорию для логов
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Настройка главного логера
        self.logger = logging.getLogger("CyberFarmEngine")
        self.logger.setLevel(logging.DEBUG)
        
        # Форматирование вывода: Время | Уровень | Сообщение
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
        self.ring_handler.setFormatter(formatter)
        
        # 1. Вывод в консоль Visual Studio
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        
        # 2. Вывод в общий файл app.log
        file_handler = logging.FileHandler(os.path.join(self.logs_dir, "app.log"), encoding='utf-8')
        file_handler.setFormatter(formatter)
        
        # Добавляем все хэндлеры к логеру (избегаем дублирования)
        if not self.logger.handlers:
            self.logger.addHandler(self.ring_handler)
            self.logger.addHandler(console_handler)
            self.logger.addHandler(file_handler)

    def get_logger(self) -> logging.Logger:
        """Возвращает настроенный объект логера для работы в других модулях."""
        return self.logger

    def save_crash_dump(self, reason: str, fsm_state: str = "UNKNOWN", extra_info: dict = None) -> str:
        """
        Создает аварийный дамп при падении:
        - Создает папку crash_YYYYMMDD_HHMMSS
        - Делает скриншот экрана (screen.png)
        - Записывает crash_info.json (состояние FSM, причина, последние 30 логов)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        crash_folder = os.path.join(self.logs_dir, f"crash_{timestamp}")
        os.makedirs(crash_folder, exist_ok=True)

        # 1. Захват кадра (Скриншот экрана)
        screenshot_path = os.path.join(crash_folder, "screen.png")
        try:
            screenshot = pyautogui.screenshot()
            screenshot.save(screenshot_path)
            self.logger.info(f"[CrashReporter] Скриншот сохранен: {screenshot_path}")
        except Exception as e:
            self.logger.error(f"[CrashReporter] Не удалось сделать скриншот: {e}")

        # 2. Сбор истории из кольцевого буфера
        history_logs = list(self.ring_handler.buffer)

        # 3. Формирование структуры JSON
        crash_data = {
            "timestamp": timestamp,
            "fsm_state": fsm_state,
            "reason": reason,
            "extra_info": extra_info or {},
            "recent_actions_history": history_logs
        }

        json_path = os.path.join(crash_folder, "crash_info.json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(crash_data, f, ensure_ascii=False, indent=4)
            self.logger.warning(f"[CrashReporter] АВАРИЙНЫЙ ДАМП УСПЕШНО СОХРАНЕН: {crash_folder}")
        except Exception as e:
            self.logger.error(f"[CrashReporter] Ошибка сохранения json-дампа: {e}")

        return crash_folder


# Глобальный экземпляр для быстрого импорта в проекте
debug_service = DebugLogger(buffer_size=30)
logger = debug_service.get_logger()


# Пример использования / Ручная проверка модуля
if __name__ == "__main__":
    logger.info("Инициализация движка...")
    logger.debug("Загрузка профиля конфигурации...")
    logger.info("Переход FSM: INIT -> SEARCH_TARGET")
    
    # Имитируем ошибку
    try:
        logger.info("Попытка атаки моба...")
        raise TimeoutError("Моб не найден, превышено время ожидания!")
    except Exception as err:
        logger.error(f"Произошел сбой: {err}")
        # Вызываем аварийный спасатель
        debug_service.save_crash_dump(
            reason=str(err),
            fsm_state="ATTACK_TARGET",
            extra_info={"hp": 45, "target_id": None}
        )