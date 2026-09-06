"""
Сервис авто-восстановления игровых окон (Recovery Pipeline).
Слой SERVICES.

Отвечает за контроль состояния сессий (OFFLINE -> ONLINE),
проведение Pre-Flight сканирования и перезапуск с посадкой в конкретный слот.
"""
import time
from typing import Dict, Any, Optional

import inputs.windows as windows
from core.launcher import run_launcher_logic
from services.debug_logger import logger


class GameRelauncher:
    """Сервис авто-восстановления с адресной посадкой окон по слотам."""

    def __init__(self, max_attempts: int = 3, cooldown_sec: float = 20.0):
        self.max_attempts = max_attempts
        self.cooldown_sec = cooldown_sec
        self._offline_since: Dict[str, float] = {}
        self._attempt_counts: Dict[str, int] = {}

    def reset_attempts(self, display_name: str) -> None:
        """Сброс счетчика ошибок при возврате сессии в ONLINE."""
        if display_name in self._attempt_counts or display_name in self._offline_since:
            logger.info(f"[Recovery] Окно '{display_name}' вернулось в онлайн. Сброс счетчика.")
            self._attempt_counts.pop(display_name, None)
            self._offline_since.pop(display_name, None)

    def handle_offline_window(self, win_info: Dict[str, Any], global_game_cfg: Dict[str, Any]) -> Optional[int]:
        """
        Обрабатывает окно в статусе OFFLINE.
        Сканирует процессы, при необходимости перезапускает лаунчер и сажает HWND ровно в его слот.
        """
        display_name = win_info.get("display_name", "Unknown")
        game_cfg = win_info.get("game_cfg") or global_game_cfg or {}
        game_name = win_info.get("game_name") or game_cfg.get("game_name")
        slot_index = win_info.get("slot_index", 0)

        if not game_name:
            logger.error(f"[Recovery] Окно '{display_name}' не имеет привязанного game_name!")
            return None

        game_title = (
            win_info.get("window_title")
            or game_cfg.get("game", {}).get("window_title")
            or ""
        )

        if not game_title:
            logger.error(f"[Recovery] У окна '{display_name}' не найден window_title!")
            return None

        now = time.time()

        # 1. Проверка лимита попыток
        current_attempts = self._attempt_counts.get(display_name, 0)
        if current_attempts >= self.max_attempts:
            logger.error(f"[Recovery] Превышен лимит попыток ({self.max_attempts}) для '{display_name}'.")
            return None

        # 2. Проверка кулдауна
        if display_name not in self._offline_since:
            self._offline_since[display_name] = now
            logger.warning(
                f"[Recovery] Окно '{display_name}' (Игра: {game_name}) ушло в OFFLINE. "
                f"Ожидание {self.cooldown_sec} сек..."
            )
            return None

        if now - self._offline_since[display_name] < self.cooldown_sec:
            return None

        # 3. Pre-Flight сканирование
        logger.info(f"[Recovery Pre-Flight] Сканирование ОС для '{game_title}' ({display_name})...")
        active_windows = windows.get_game_windows(game_title, only_visible=True)

        if active_windows:
            new_hwnd = active_windows[0]
            logger.info(f"[Recovery Pre-Flight] Окно найдено (HWND: {new_hwnd}). Запуск лаунчера отменен.")
            
            self._place_window_to_its_slot(new_hwnd, slot_index, game_cfg)
            self._offline_since.pop(display_name, None)
            return new_hwnd

        # 4. Перезапуск через лаунчер
        self._attempt_counts[display_name] = current_attempts + 1
        logger.info(f"[Recovery] Запуск лаунчера для '{display_name}' (Игра: {game_name}, Слот #{slot_index})...")

        try:
            run_launcher_logic(game_cfg, slot_index=slot_index)

            launch_delay = game_cfg.get("timings", {}).get("game_launch_delay_min_sec", 15)
            logger.info(f"[Recovery] Ожидание появления окна {launch_delay} сек (из конфига)...")
            time.sleep(launch_delay)

            post_windows = windows.get_game_windows(game_title, only_visible=True)
            if not post_windows:
                logger.error(f"[Recovery] Окно '{display_name}' не появилось после перезапуска.")
                self._offline_since[display_name] = time.time()
                return None

            new_hwnd = post_windows[-1]

            # Посадка окна строго в выделенную ячейку
            self._place_window_to_its_slot(new_hwnd, slot_index, game_cfg)
            self._offline_since.pop(display_name, None)
            return new_hwnd

        except Exception as err:
            logger.error(f"[Recovery] Ошибка перезапуска '{display_name}': {err}")
            self._offline_since[display_name] = time.time()
            return None

    def _place_window_to_its_slot(self, hwnd: int, slot_index: int, game_cfg: Dict[str, Any]) -> None:
        """Вспомогательный метод точечной расстановки окна в его ячейку."""
        cols = game_cfg.get("grid_cols", 2)
        rows = game_cfg.get("grid_rows", 2)
        bottom_pad = game_cfg.get("bottom_padding", 40)

        logger.info(f"[Recovery] Посадка HWND {hwnd} строго в слот #{slot_index}...")
        windows.place_window_in_slot(
            hwnd=hwnd,
            slot_index=slot_index,
            cols=cols,
            rows=rows,
            bottom_padding=bottom_pad
        )