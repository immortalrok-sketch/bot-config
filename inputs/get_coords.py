import time
import win32api

print("Наведите мышь на нужную кнопку в игре. Через 3 секунды скрипт покажет координаты...")
time.sleep(3)
x, y = win32api.GetCursorPos()
print(f"Точные координаты курсора: \"x\": {x}, \"y\": {y}")
