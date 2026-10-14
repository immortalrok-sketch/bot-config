import win32gui
import win32api
import time

print("1. Кликните по окну Netmarble Launcher, чтобы выбрать его.")
time.sleep(2)
hwnd = win32gui.GetForegroundWindow()

rect = win32gui.GetClientRect(hwnd)
w = rect[2] - rect[0]
h = rect[3] - rect[1]

print(f"Размер клиентской области окна: {w}x{h}")
print("2. Кликните по нужному элементу (иконке аккаунта) для замера координат...")

# Ждем нажатия ЛКМ
while True:
    state = win32api.GetAsyncKeyState(0x01)  # VK_LBUTTON
    if state & 0x8000:
        # Получаем глобальные координаты мыши
        sx, sy = win32api.GetCursorPos()
        # Переводим в координаты внутри клиентской области
        rx, ry = win32gui.ScreenToClient(hwnd, (sx, sy))
        
        x_pct = round(rx / w, 3)
        y_pct = round(ry / h, 3)
        
        print("\n=== ГОТОВЫЙ РЕЗУЛЬТАТ ДЛЯ CONFIG.JSON ===")
        print(f'"x_pct": {x_pct}, "y_pct": {y_pct}')
        print(f"Точка клика в пикселях: rx={rx}, ry={ry}")
        print("=======================================\n")
        time.sleep(0.5)
        break