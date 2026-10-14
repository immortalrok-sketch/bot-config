import sys, os, time
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from vision.vision import capture_ui_frame, find_template_in_frame
import inputs.windows as windows
import cv2

hwnd = windows.get_game_windows("Netmarble Launcher")[0]

# 1. Берём ОДИН кадр и вырезаем шаблон ПРЯМО ИЗ НЕГО В ПАМЯТИ (без диска)
frame_a = capture_ui_frame(hwnd)
# сюда вставь координаты кнопки в пикселях (посмотри на 960x540 картинке, где кнопка)
x1, y1, x2, y2 = 400, 460, 620, 500  # ПОДСТАВЬ реальные под свою кнопку
template_from_same_frame = frame_a[y1:y2, x1:x2]
cv2.imwrite("temp_template.png", template_from_same_frame)

# 2. Ищем этот шаблон в ТОМ ЖЕ САМОМ кадре (self-match)
res_self = find_template_in_frame(frame_a, "temp_template.png", confidence=0.0)
print(f"Self-match (тот же кадр): {res_self['confidence'] if res_self else 'None'}")

time.sleep(2)

# 3. Ищем тот же шаблон в НОВОМ кадре, снятом чуть позже
frame_b = capture_ui_frame(hwnd)
res_later = find_template_in_frame(frame_b, "temp_template.png", confidence=0.0)
print(f"Match в новом кадре (2 сек спустя): {res_later['confidence'] if res_later else 'None'}")

input("Enter...")