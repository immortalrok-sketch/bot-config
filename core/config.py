import os
import sys
import json

MAIN_CONFIG_FILE = "main_config.json"
LAST_LOADED_GAME = "ymir"

DEFAULT_MAIN_CONFIG = {
    "active_games": [
        "rf_next",
        "ymir"
    ],
    "auto_update_enabled": False,
    "version": "1.0.12"
}

DEFAULT_GAME_CONFIG = {
    "game_name": "Game Title",
    "launcher": {
        "path": "C:\\Path\\To\\Launcher.exe",
        "window_title": "Launcher Window",
        "startup_wait_sec": 15,
        "activate_delay_sec": 1.5
    },
    "game": {
        "window_title": "Game Window",
        "max_windows": 1
    },
    "images": {
        "acc_inactive": "acc_inactive.png",
        "acc_active": "acc_active.png",
        "play_button": "play.png",
        "enter_button": "enter.png"
    },
    "recognition": {
        "confidence_acc_inactive": 0.75,
        "confidence_acc_active": 0.75,
        "confidence_play_button": 0.8,
        "confidence_enter_button": 0.72,
        "click_offset_min_px": 3,
        "click_offset_max_px": 12
    },
    "timings": {
        "delay_after_acc_click_sec": 1.5,
        "play_button_timeout_sec": 10,
        "wait_between_accounts_sec": 20,
        "game_launch_delay_min_sec": 20,
        "game_launch_delay_max_sec": 25,
        "enter_button_timeout_sec": 15,
        "console_close_delay_sec": 7
    }
}


def get_base_dir():
    """Возвращает корень проекта (выходит из подпапки core/)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    
    # Файл лежит в core/config.py — поднимаемся на 1 уровень вверх в корень
    core_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(core_dir)


def get_cfg_val(cfg, group, key, default=None):
    """Безопасное извлечение значения из словаря настроек."""
    try:
        return cfg.get(group, {}).get(key, default)
    except Exception:
        return default


def load_main_config():
    """Загружает главный файл конфигурации из корня проекта."""
    main_path = os.path.join(get_base_dir(), MAIN_CONFIG_FILE)

    if not os.path.exists(main_path):
        try:
            with open(main_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_MAIN_CONFIG, f, indent=4, ensure_ascii=False)
            print(f"[Конфиг] Создан главный файл конфигурации: {main_path}")
            return DEFAULT_MAIN_CONFIG
        except Exception as e:
            print(f"[Ошибка] Не удалось создать главный конфиг: {e}")
            return DEFAULT_MAIN_CONFIG

    try:
        with open(main_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Ошибка] Ошибка чтения main_config.json: {e}")
        return DEFAULT_MAIN_CONFIG


def load_game_config(game_name):
    """Загружает конфиг конкретной игры из корневой папки games/<game_name>/config.json."""
    global LAST_LOADED_GAME
    LAST_LOADED_GAME = game_name

    base_dir = get_base_dir()
    game_dir = os.path.join(base_dir, "games", game_name)
    images_dir = os.path.join(game_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    game_config_path = os.path.join(game_dir, "config.json")

    if not os.path.exists(game_config_path):
        custom_default = DEFAULT_GAME_CONFIG.copy()
        custom_default["game_name"] = game_name
        try:
            with open(game_config_path, "w", encoding="utf-8") as f:
                json.dump(custom_default, f, indent=4, ensure_ascii=False)
            print(f"[Конфиг] Создан новый конфиг для игры '{game_name}': {game_config_path}")
            return custom_default
        except Exception as e:
            print(f"[Ошибка] Не удалось создать конфиг игры '{game_name}': {e}")
            return custom_default

    try:
        with open(game_config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            print(f"[Конфиг] Загружена конфигурация игры: '{game_name.upper()}'")
            return cfg
    except Exception as e:
        print(f"[Ошибка] Ошибка чтения конфига для '{game_name}': {e}")
        return DEFAULT_GAME_CONFIG


def get_image_path(game_name, filename):
    """Возвращает полный путь к файлу изображения в папке игры."""
    base_dir = get_base_dir()
    game_image_path = os.path.join(base_dir, "games", game_name, "images", filename)
    if os.path.exists(game_image_path):
        return game_image_path

    fallback_path = os.path.join(base_dir, "images", filename)
    if os.path.exists(fallback_path):
        return fallback_path

    return game_image_path


def get_path(arg1, arg2=None):
    """Универсальная функция поиска путей."""
    if arg2 is None:
        return get_image_path(LAST_LOADED_GAME, arg1)
    return get_image_path(arg1, arg2)