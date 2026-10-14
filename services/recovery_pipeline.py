"""
Сервис авто-восстановления игровых окон (Recovery Pipeline).
Слой SERVICES.

Отвечает за контроль состояния сессий (OFFLINE -> ONLINE),
проведение Pre-Flight сканирования и перезапуск с посадкой в конкретный слот.
"""
import time
from typing import Dict, Any, Optional, Iterable

import inputs.windows as windows
from core.launcher import run_launcher_logic
from services.debug_logger import logger


def _account_slot_from_name(display_name: str) -> int:
    """
    Номер аккаунта внутри игры из имени окна: 'rf_next_2' -> 2, 'raven2_1' -> 1.
    Лаунчеру нужен именно он (какой аккаунт выбрать), а не позиция окна в сетке.
    """
    try:
        return int(str(display_name).rsplit("_", 1)[1])
    except (ValueError, IndexError):
        return 1


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

    def handle_offline_window(
        self,
        win_info: Dict[str, Any],
        global_game_cfg: Dict[str, Any],
        exclude_hwnds: Optional[Iterable[int]] = None,
    ) -> Optional[int]:
        """
        Обрабатывает окно в статусе OFFLINE.
        Сканирует процессы, при необходимости перезапускает лаунчер и сажает HWND ровно в его слот.

        exclude_hwnds — HWND окон, которые уже закреплены за другими записями
        (живые соседи). Без этого поиск по заголовку находит окно соседа и
        присваивает его упавшему окну вместо перезапуска.
        """
        display_name = win_info.get("display_name", "Unknown")
        game_cfg = win_info.get("game_cfg") or global_game_cfg or {}
        game_name = win_info.get("game_name") or game_cfg.get("game_name")
        slot_index = win_info.get("slot_index", 0)          # позиция в сетке (для расстановки)
        account_slot = _account_slot_from_name(display_name)  # номер аккаунта (для лаунчера)
        busy = {h for h in (exclude_hwnds or []) if h}

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

        # 3. Pre-Flight сканирование (только окна, не занятые другими записями)
        logger.info(f"[Recovery Pre-Flight] Сканирование ОС для '{game_title}' ({display_name})...")
        free_windows = [
            h for h in windows.get_game_windows(game_title, only_visible=True)
            if h not in busy
        ]

        if free_windows:
            new_hwnd = free_windows[0]
            logger.info(f"[Recovery Pre-Flight] Свободное окно найдено (HWND: {new_hwnd}). Запуск лаунчера отменен.")

            self._place_window_to_its_slot(new_hwnd, slot_index, game_cfg)
            self._offline_since.pop(display_name, None)
            return new_hwnd

        # 4. Перезапуск через лаунчер
        self._attempt_counts[display_name] = current_attempts + 1
        logger.info(
            f"[Recovery] Запуск лаунчера для '{display_name}' "
            f"(Игра: {game_name}, аккаунт #{account_slot}, слот сетки #{slot_index})..."
        )

        try:
            launched = run_launcher_logic(game_cfg, slot_index=account_slot)
            if not launched:
                logger.error(f"[Recovery] Лаунчер не отработал для '{display_name}'. Повтор после кулдауна.")
                self._offline_since[display_name] = time.time()
                return None

            launch_delay = game_cfg.get("timings", {}).get("game_launch_delay_min_sec", 15)
            logger.info(f"[Recovery] Ожидание появления окна {launch_delay} сек (из конфига)...")
            time.sleep(launch_delay)

            post_windows = [
                h for h in windows.get_game_windows(game_title, only_visible=True)
                if h not in busy
            ]
            if not post_windows:
                logger.error(f"[Recovery] Новое окно '{display_name}' не появилось после перезапуска.")
                self._offline_since[display_name] = time.time()
                return None

            new_hwnd = post_windows[0]

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