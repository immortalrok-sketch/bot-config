import os
import sys
import time
import cv2
import numpy as np
import win32gui
from PIL import ImageGrab

# Добавляем корень проекта в путь поиска Python
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Импорты сервисов и логирования
from core.profile_loader import GameProfile
from services.recovery_pipeline import RecoveryPipeline
from vision.death_detector import DeathDetector
from inputs.mouse_input import MouseInputEngine
from services.dashboard_app import cluster_states

# Подключаем наш новый аварийный сервисный логер ("Черный ящик")
from services.debug_logger import logger, debug_service


def get_game_frame(hwnd):
    """Захватывает актуальный кадр окна игры или всего экрана."""
    if hwnd and win32gui.IsWindow(hwnd):
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] > 0 and rect[3] - rect[1] > 0:
            img = ImageGrab.grab(bbox=rect)
            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    img = ImageGrab.grab()
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def update_dashboard_state(pc_name: str, win_name: str, status: str, stats: dict = None):
    """Прямая запись состояния в оперативную память дашборда."""
    if pc_name not in cluster_states:
        cluster_states[pc_name] = {}

    current_data = cluster_states[pc_name].get(win_name, {"status": "UNKNOWN", "stats": {}})
    updated_stats = stats if stats is not None else current_data.get("stats", {})

    cluster_states[pc_name][win_name] = {
        "status": status,
        "stats": updated_stats
    }


def run_main_orchestrator():
    logger.info("=== Инициализация главного цикла бота RF ===")

    current_fsm_state = "INIT"

    try:
        profile = GameProfile("rf_next")
        detector = DeathDetector(profile)
        mouse_input = MouseInputEngine(dry_run=False) 
        pipeline = RecoveryPipeline(profile, input_engine=mouse_input)

        target_hwnd = win32gui.FindWindow(None, "RF")
        check_interval_sec = 2.0

        pc_name = os.environ.get("COMPUTERNAME", "Rig_1")
        window_label = "RF_Win_1"

        current_fsm_state = "AUTO_HUNTING"
        update_dashboard_state(pc_name, window_label, current_fsm_state)
        logger.info(f"[Main Engine] Бот запущен (HWND: {target_hwnd}) и ведет мониторинг...")

        while True:
            frame = get_game_frame(target_hwnd)

            # Детекция смерти персонажа
            if detector.check_is_dead(frame):
                current_fsm_state = "RECOVERY"
                logger.warning("Обнаружена смерть персонажа! Запуск Recovery Pipeline...")
                update_dashboard_state(pc_name, window_label, "DISCONNECTED")

                success = pipeline.run_recovery(
                    hwnd=target_hwnd,
                    get_frame_func=get_game_frame,
                    death_detector=detector
                )

                if success:
                    current_fsm_state = "AUTO_HUNTING"
                    logger.info("[Main Engine] Восстановление завершено. Возврат к авто-охоте.")
                    update_dashboard_state(pc_name, window_label, current_fsm_state)
                else:
                    logger.error("[Main Engine] Сбой восстановления! Повтор через 10 секунд...")
                    time.sleep(10.0)

            time.sleep(check_interval_sec)

    except KeyboardInterrupt:
        logger.info("[Main Engine] Мониторинг остановлен пользователем (Ctrl+C).")
        update_dashboard_state(pc_name, window_label, "OFFLINE")

    except Exception as fatal_error:
        # Глобальный перехват падений: автоматическое сохранение дампа при неожидаемом сбое
        logger.critical(f"[Main Engine] Критическая ошибка цикла: {fatal_error}", exc_info=True)
        
        crash_folder = debug_service.save_crash_dump(
            reason=str(fatal_error),
            fsm_state=current_fsm_state,
            extra_info={"target_hwnd": target_hwnd if 'target_hwnd' in locals() else None}
        )
        logger.info(f"[Main Engine] Дамп падения сформирован в директории: {crash_folder}")


if __name__ == "__main__":
    run_main_orchestrator()