import time
from dashboard_app import launch_dashboard_in_background
from state_machine import GameState, DashboardBridge

def run_integration_test():
    print("==================================================")
    print("   ТЕСТИРОВАНИЕ ИНТЕГРАЦИИ СЕРВЕРА И СОСТОЯНИЙ   ")
    print("==================================================")

    # 1. Запуск веб-дашборда в фоновом режиме
    print("[1/3] Запуск локального веб-сервера...")
    launch_dashboard_in_background(host="0.0.0.0", port=5000)
    time.sleep(1.5)  # Даем время серверу на инициализацию

    # 2. Инициализация моста передачи данных
    print("[2/3] Подключение моста DashboardBridge...")
    bridge = DashboardBridge(server_ip="127.0.0.1", pc_name="Rig-Main")

    # 3. Эмуляция динамической смены состояний окон
    print("[3/3] Симуляция смены состояний игровых окон...")

    # Шаг A: Запуск лаунчеров
    print(" -> Этап 1: Окна запускаются")
    bridge.update_status("RF_Online_1", GameState.LAUNCHER)
    bridge.update_status("RF_Online_2", GameState.LAUNCHER)
    time.sleep(3)

    # Шаг Б: Окно 1 вошло в игру, Окно 2 встало в очередь
    print(" -> Этап 2: Изменение статусов (В игре / В очереди)")
    bridge.update_status("RF_Online_1", GameState.IN_GAME_IDLE)
    bridge.update_status("RF_Online_2", GameState.IN_QUEUE)
    time.sleep(3)

    # Шаг В: Окно 1 встало на автобой, появились новое окно с ошибкой
    print(" -> Этап 3: Включение автобоя и симуляция вылета")
    bridge.update_status("RF_Online_1", GameState.AUTO_HUNTING)
    bridge.update_status("RF_Online_2", GameState.IN_GAME_IDLE)
    bridge.update_status("YMIR_Window_1", GameState.DISCONNECTED)
    time.sleep(2)

    print("\n[Успех] Тест пройден без ошибок!")
    print("Открой страницу http://localhost:5000 в браузере и убедись, что карточки отображают актуальные статусы.")
    print("Нажми Ctrl+C в консоли для завершения теста.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Стоп] Тестирование завершено.")

if __name__ == "__main__":
    run_integration_test()
