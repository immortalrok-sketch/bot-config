# Динамическая архитектурная карта CyberFarm Engine

```mermaid
graph TB
    subgraph core ["Слой CORE"]
        core/__init__
        core/actions
        core/config
        core/launcher
        core/main_loop
        core/profile_loader
        core/state_detector
        core/state_machine
    end

    subgraph inputs ["Слой INPUTS"]
        inputs/__init__
        inputs/get_coords
        inputs/mouse_input
        inputs/windows
    end

    subgraph root ["Точка Входа"]
        Engine_Modul
        build
        updater
    end

    subgraph services ["Слой SERVICES"]
        services/__init__
        services/dashboard_app
        services/debug_logger
        services/recovery_pipeline
        services/system_monitor
        services/telegram_bot
        services/watchdog
    end

    subgraph tools ["Слой TOOLS"]
        tools/check_canvas
        tools/make_mask
        tools/prepare_new_game
        tools/roi_selector
    end

    subgraph vision ["Слой VISION"]
        vision/__init__
        vision/death_detector
        vision/debug
        vision/ocr_reader
        vision/template_finder
        vision/vision
    end

    Engine_Modul --> core/state_detector
    Engine_Modul --> core/state_machine
    Engine_Modul --> services/dashboard_app
    Engine_Modul --> services/system_monitor
    Engine_Modul --> services/telegram_bot
    Engine_Modul --> updater
    core/actions --> vision/vision
    core/launcher --> core/profile_loader
    core/launcher --> inputs/mouse_input
    core/launcher --> inputs/windows
    core/launcher --> services/debug_logger
    core/launcher --> vision/vision
    core/main_loop --> core/profile_loader
    core/main_loop --> inputs/mouse_input
    core/main_loop --> services/dashboard_app
    core/main_loop --> services/debug_logger
    core/main_loop --> services/recovery_pipeline
    core/main_loop --> vision/death_detector
    core/state_detector --> core/state_machine
    core/state_detector --> inputs/windows
    core/state_detector --> services/dashboard_app
    core/state_detector --> services/debug_logger
    core/state_detector --> services/recovery_pipeline
    core/state_detector --> vision/ocr_reader
    core/state_detector --> vision/vision
    core/state_machine --> services/debug_logger
    services/recovery_pipeline --> core/launcher
    services/recovery_pipeline --> inputs/windows
    services/recovery_pipeline --> services/debug_logger
    services/telegram_bot --> inputs/windows
    services/telegram_bot --> vision/vision
    services/watchdog --> inputs/windows
    services/watchdog --> vision/vision
    vision/__init__ --> vision/vision
    vision/debug --> inputs/windows
    vision/debug --> vision/vision
    vision/ocr_reader --> vision/vision
    vision/vision --> inputs/windows
```

## Назначение слоев
* **core/** — Управление конфигурациями, профилями и глобальным циклом.
* **vision/** — Компьютерное зрение, детекция состояний и OCR.
* **inputs/** — Эмуляция ввода и управление HWND окнами.
* **services/** — Фоновые сервисы, веб-панель и восстановления.
* **games/** — Конфиги и пресеты под конкретные игры.