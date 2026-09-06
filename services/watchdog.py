import os
import sys
import time

# 1. Корректный расчёт корня проекта: поднимаемся на 2 уровня вверх из папки tests/
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)

# 2. Добавляем корень проекта в начало sys.path до всех внутренних импортов
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from inputs.windows import get_game_windows
from vision.vision import capture_window_frame
from services.watchdog import WindowWatchdog


def run_test():
    # Настраиваем ускоренный таймаут 5.0 секунд для удобного тестирования
    watchdog = WindowWatchdog(freeze_timeout_sec=5.0, diff_threshold=0.8)

    print("=== Запуск изоляционного теста Watchdog ===", flush=True)

    hwnds = get_game_windows("RF")
    if not hwnds:
        print("[ОШИБКА] Окна игры не найдены! Запусти игру и повтори тест.", flush=True)
        return

    print(f"[ИНФО] Найдено окон для мониторинга: {len(hwnds)} | HWND: {hwnds}", flush=True)
    print("Мониторинг запущен на 30 секунд. Запускаем проверку...\n", flush=True)

    try:
        for iteration in range(1, 31):
            print(f"--- Проход {iteration}/30 ---", flush=True)
            for hwnd in list(hwnds):
                # Захваченный кадр передаём в метод проверки
                frame = capture_window_frame(hwnd)
                status = watchdog.check_status(hwnd, frame)
                
                print(f"[WATCHDOG LOG] HWND: {hwnd} | Status: -> {status}", flush=True)

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[ТЕСТ СНЯТ] Остановлено пользователем.", flush=True)

    print("\n=== Тестирование завершено ===", flush=True)


if __name__ == "__main__":
    run_test()