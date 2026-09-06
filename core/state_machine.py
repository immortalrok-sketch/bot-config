"""
Менеджер состояний (FSM).
Защищен от дублирования сообщений в логах.
Слой CORE.
"""
import os
import sys
from enum import Enum

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from services.debug_logger import logger


class GameState(Enum):
    """Список всех возможных состояний бота."""
    LAUNCHER = "LAUNCHER"
    IN_QUEUE = "IN_QUEUE"
    IN_GAME_IDLE = "IN_GAME_IDLE"
    AUTO_HUNTING = "AUTO_HUNTING"
    RECOVERY = "RECOVERY"
    DISCONNECTED = "DISCONNECTED"
    OFFLINE = "OFFLINE"


class FSMManager:
    """Менеджер состояний (FSM) с фильтрацией повторных записей."""
    def __init__(self, game_name: str = "rf_next", max_age_seconds: int = 7200):
        self.game_name = game_name
        self.max_age_seconds = max_age_seconds
        
        self.game_dir = os.path.join(ROOT_DIR, "games", self.game_name)
        os.makedirs(self.game_dir, exist_ok=True)
        
        self.state_file = os.path.join(self.game_dir, "fsm_state.json")
        self.current_state = GameState.LAUNCHER

    def get_state(self) -> GameState:
        return self.current_state

    def transition_to(self, new_state: GameState, reason: str = ""):
        # Фильтр: Писать в лог только если статус реально изменился
        if self.current_state != new_state:
            old_state_name = self.current_state.value if self.current_state else "NONE"
            self.current_state = new_state
            
            log_msg = f"[FSM] Переход: {old_state_name} -> {new_state.value}"
            if reason:
                log_msg += f" (Причина: {reason})"
            logger.info(log_msg)

    def save_state(self):
        """Временно отключено."""
        pass

    def load_state(self) -> GameState:
        """Временно возвращаем LAUNCHER."""
        self.current_state = GameState.LAUNCHER
        return GameState.LAUNCHER


if __name__ == "__main__":
    fsm = FSMManager(game_name="rf_next")
    print(f"Текущее состояние: {fsm.get_state().value}")