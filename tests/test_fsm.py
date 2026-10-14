import time
from state_machine import GameState, DashboardBridge

# Создаем экземпляр моста дашборда
dashboard = DashboardBridge()

# Симуляция работы основного цикла бота
print("--- Тестирование системы статусов ---")

# Окно 1 запустилось
dashboard.update_status("RF_Window_1", GameState.LAUNCHER)
time.sleep(1)

# Окно 1 зашло в игру и встало в очередь
dashboard.update_status("RF_Window_1", GameState.IN_QUEUE)
time.sleep(1)

# Повторный вызов с тем же статусом (сообщение НЕ будет продублировано)
dashboard.update_status("RF_Window_1", GameState.IN_QUEUE)

# Персонаж прошел очередь и включил автобой
dashboard.update_status("RF_Window_1", GameState.AUTO_HUNTING)
