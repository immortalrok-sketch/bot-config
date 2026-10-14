import numpy as np
from profile_loader import GameProfile
from death_detector import DeathDetector
from recovery_pipeline import RecoveryPipeline
from mouse_input import MouseInputEngine

class FakeFrameProvider:
    """Имитация захвата кадров окна."""
    def get_frame(self, hwnd):
        # В рабочем движке здесь будет вызов вашего модуля захвата окна (win32/d3d)
        return np.zeros((1080, 1920, 3), dtype=np.uint8)

def run_sandbox_test():
    print("=== Проверка Recovery Pipeline с контролем кадров ===\n")

    try:
        profile = GameProfile("rf_next")
        print(f"[OK] Профиль '{profile.config.get('game_name')}' загружен.")
    except Exception as e:
        print(f"[FAIL] Не удалось загрузить профиль: {e}")
        return

    detector = DeathDetector(profile)
    mouse_input = MouseInputEngine(dry_run=True)
    pipeline = RecoveryPipeline(profile, input_engine=mouse_input)
    frame_provider = FakeFrameProvider()

    print("\n--- Запуск теста с контролем состояния ---")
    fake_hwnd = 0  

    # Запускаем конвейер, передавая ему функцию захвата кадра и детектор
    success = pipeline.run_recovery(
        hwnd=fake_hwnd,
        get_frame_func=frame_provider.get_frame,
        death_detector=detector
    )

    print(f"\n[Test] Результат работы умного конвейера: {success}")

if __name__ == "__main__":
    run_sandbox_test()