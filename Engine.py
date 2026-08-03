def wait_and_arrange_windows(target_count=None):
    game_title = get_cfg_val(CFG, "game", "window_title", "RF ONLINE NEXT")
    max_win_cfg = get_cfg_val(CFG, "game", "max_windows", 2)
    
    # Жестко получаем реальное разрешение через Windows API
    user32_local = ctypes.windll.user32
    screen_w = user32_local.GetSystemMetrics(0)
    screen_h = user32_local.GetSystemMetrics(1)
    
    print(f"\n[Диагностика экрана] Windows API определил разрешение: {screen_w}x{screen_h}")

    half_w = screen_w // 2
    half_h = screen_h // 2

    # Кастомная расстановка по углам:
    grid_positions = [
        {"x": half_w, "y": 0, "w": screen_w - half_w, "h": half_h},                 # 1. Правый верх
        {"x": 0, "y": half_h, "w": half_w, "h": screen_h - half_h},                 # 2. Левый низ
        {"x": half_w, "y": half_h, "w": screen_w - half_w, "h": screen_h - half_h}, # 3. Правый низ
        {"x": 0, "y": 0, "w": half_w, "h": half_h}                                  # 4. Левый верх
    ]

    if target_count is None or target_count == 0:
        target_count = max_win_cfg

    required_windows = min(target_count, len(grid_positions))

    min_launch_delay = get_cfg_val(CFG, "timings", "game_launch_delay_min_sec", 23)
    max_launch_delay = get_cfg_val(CFG, "timings", "game_launch_delay_max_sec", 26)
    launch_delay = random.uniform(min_launch_delay, max_launch_delay)

    print(f"Пауза {launch_delay:.1f} сек для подгрузки клиента игры...")
    time.sleep(launch_delay)

    print(f"Ожидание появления игровых окон (цель: {required_windows})...")
    for attempt in range(60):
        possible_windows = [w for w in gw.getWindowsWithTitle(game_title) if not w.isMinimized]
        
        unique_game_windows = []
        seen_pids = set()

        for win in possible_windows:
            if win.title == "":
                continue
            
            window_class = win32gui.GetClassName(win._hWnd)
            if window_class == "Chrome_WidgetWin_1":
                continue

            _, win_pid = win32process.GetWindowThreadProcessId(win._hWnd)
            if win_pid not in seen_pids:
                exe_name = get_process_name_by_hwnd(win._hWnd)
                if any(b in exe_name for b in ["chrome", "browser", "edge", "opera"]):
                    continue
                if "launcher" in exe_name:
                    continue

                seen_pids.add(win_pid)
                unique_game_windows.append(win)

        if len(unique_game_windows) >= required_windows:
            print(f"[{len(unique_game_windows)}/{required_windows}] Игровые окна обнаружены. Раскладываем по углам...")
            random_sleep(2.0, 4.0)

            # Жестко сортируем окна по hWnd, чтобы порядок был стабильным
            unique_game_windows.sort(key=lambda w: w._hWnd)

            for idx, win in enumerate(unique_game_windows[:required_windows]):
                pos = grid_positions[idx]
                target_x, target_y, target_w, target_h = pos["x"], pos["y"], pos["w"], pos["h"]
                hwnd = win._hWnd
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.5)
                    resize_with_dwm(hwnd, target_x, target_y, target_w, target_h)
                    print(f" -> Окно {idx+1} успешно выставлено: ({target_x}, {target_y}, {target_w}, {target_h})")
                except Exception as e:
                    print(f"[Предупреждение] Не удалось переместить окно {idx+1}: {e}")

            print(f"\n--- Фокус, активация и нажатие кнопки 'Выбрать' ---")
            img_enter = get_cfg_val(CFG, "images", "enter_button", "enter.png")
            conf_enter = get_cfg_val(CFG, "recognition", "confidence_enter_button", 0.72)
            enter_timeout = get_cfg_val(CFG, "timings", "enter_button_timeout_sec", 15)

            for idx, win in enumerate(unique_game_windows[:required_windows]):
                hwnd = win._hWnd
                print(f"\n[Окно {idx+1}] Активация...")
                force_focus_window(hwnd)
                random_sleep(0.8, 1.5)

                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    center_x = (rect[0] + rect[2]) // 2
                    center_y = (rect[1] + rect[3]) // 2

                    center_offset_x = random.randint(15, 40) * random.choice([-1, 1])
                    center_offset_y = random.randint(15, 40) * random.choice([-1, 1])

                    print(f" -> Клик для фокуса в точке ({center_x + center_offset_x}, {center_y + center_offset_y})...")
                    click_with_offset(center_x + center_offset_x, center_y + center_offset_y, min_offset=2, max_offset=8)
                except Exception as e:
                    print(f"[Ошибка] Не удалось кликнуть в окне {idx+1}: {e}")

                print(f" -> Ожидание и клик по кнопке 'Выбрать'...")
                if wait_and_click_image(img_enter, timeout=enter_timeout, confidence=conf_enter):
                    print(f" -> УСПЕХ: Кнопка 'Выбрать' нажата в окне {idx+1}")
                else:
                    print(f" -> [Предупреждение] Кнопка 'Выбрать' не распознана в окне {idx+1}.")

                time.sleep(1.0)

            print(f"\n=== ВСЕ ГОТОВО. СКРИПТ ОТКЛЮЧИЛСЯ ===")
            return

        time.sleep(3)

    print("[Внимание] Окна игры не появились вовремя.")
