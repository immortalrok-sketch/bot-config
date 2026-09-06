"""
Инструмент подготовки, клонирования и валидации профилей игр.
Автоматизирует развертывание новых тайтлов и предотвращает ошибки рантайма.
"""

import json
import os
import shutil
import sys
from pathlib import Path

# Корень проекта (поднимаемся на один уровень вверх из папки tools)
BASE_DIR = Path(__file__).resolve().parent.parent
GAMES_DIR = BASE_DIR / "games"

# Шаблон базового конфига с полным набором мета-метрических ROI
TEMPLATE_CONFIG = {
    "game_name": "New Game Title",
    "launcher": {
        "path": "C:\\Path\\To\\Launcher.exe",
        "window_title": "Launcher Title",
        "startup_wait_sec": 10
    },
    "game": {
        "window_title": "Game Window Title",
        "max_windows": 1,
        "window_prefix": "new_game_1"
    },
    "ocr_rois": {
        "level": {
            "x_pct": 0.0,
            "y_pct": 0.0,
            "w_pct": 0.0,
            "h_pct": 0.0,
            "color": "white"
        },
        "exp_percent": {
            "x_pct": 0.0,
            "y_pct": 0.0,
            "w_pct": 0.0,
            "h_pct": 0.0,
            "color": "orange"
        },
        "combat_power": {
            "x_pct": 0.0,
            "y_pct": 0.0,
            "w_pct": 0.0,
            "h_pct": 0.0,
            "color": "orange"
        },
        "diamonds": {
            "x_pct": 0.0,
            "y_pct": 0.0,
            "w_pct": 0.0,
            "h_pct": 0.0,
            "color": "white"
        }
    },
    "images": {
        "play_button": "play.png"
    },
    "templates": {
        "play_button": "play.png"
    },
    "recognition": {
        "confidence_play_button": 0.75
    },
    "timings": {
        "wait_between_accounts_sec": 10
    }
}


def get_existing_games() -> list[str]:
    """Сканирует папку games/ и возвращает список существующих профилей."""
    if not GAMES_DIR.exists():
        return []
    return [d.name for d in GAMES_DIR.iterdir() if d.is_dir()]


def create_game_scaffold(game_id: str) -> None:
    """Генерирует готовую структуру папок и шаблонный config.json под новую игру."""
    clean_game_id = game_id.strip().lower().replace(" ", "_")
    if not clean_game_id:
        print("[ERROR] Название игры не может быть пустым.")
        return

    game_path = GAMES_DIR / clean_game_id
    images_path = game_path / "images"
    screenshots_path = game_path / "ui_screenshots"

    if game_path.exists():
        print(f"\n[!] Папка игры '{clean_game_id}' уже существует: {game_path}\n")
        return

    images_path.mkdir(parents=True, exist_ok=True)
    screenshots_path.mkdir(parents=True, exist_ok=True)

    config_file = game_path / "config.json"
    template = TEMPLATE_CONFIG.copy()
    template["game_name"] = game_id.strip()

    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Чистый профиль '{clean_game_id}' успешно создан!")
    print(f"    1. Замените скриншоты в: {screenshots_path}")
    print(f"    2. Замените картинки кнопок в: {images_path}")
    print(f"    3. Разметьте ROI в конфиге: {config_file}\n")


def clone_game_profile(source_id: str, target_id: str) -> None:
    """Полностью копирует папку существующей игры-донора в новый профиль."""
    clean_source = source_id.strip().lower().replace(" ", "_")
    clean_target = target_id.strip().lower().replace(" ", "_")

    source_path = GAMES_DIR / clean_source
    target_path = GAMES_DIR / clean_target

    if not source_path.exists():
        print(f"\n[ERROR] Исходная игра '{clean_source}' не найдена!\n")
        return

    if target_path.exists():
        print(f"\n[!] Игра '{clean_target}' уже существует: {target_path}\n")
        return

    # Копируем всё содержимое (images, ui_screenshots, config.json)
    shutil.copytree(source_path, target_path)

    # Обновляем поле game_name в скопированном config.json
    config_file = target_path / "config.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            
            config_data["game_name"] = target_id.strip()

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[WARN] Не удалось автоматически обновить game_name в конфиге: {e}")

    print(f"\n[+] Профиль '{clean_target}' успешно скопирован из '{clean_source}'!")
    print(f"    Путь к новому профилю: {target_path}\n")


def validate_game_profile(game_id: str) -> bool:
    """Проверяет конфиг, картинки и выводит предупреждения для неразмеченных ROI."""
    clean_game_id = game_id.strip().lower().replace(" ", "_")
    game_path = GAMES_DIR / clean_game_id
    config_file = game_path / "config.json"
    images_dir = game_path / "images"

    if not config_file.exists():
        print(f"\n[ERROR] Конфиг не найден: {config_file}\n")
        return False

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"\n[ERROR] Ошибка чтения JSON: {e}\n")
        return False

    errors = []
    warnings = []

    # 1. Проверка пути к лаунчеру
    launcher_path = config.get("launcher", {}).get("path")
    if launcher_path and not os.path.exists(launcher_path):
        warnings.append(f"Лаунчер не найден по пути: {launcher_path}")

    # 2. Проверка ROI: валидность границ + подсветка нулевых значений
    rois = config.get("ocr_rois", {})
    for roi_name, roi_data in rois.items():
        x = roi_data.get("x_pct", 0.0)
        y = roi_data.get("y_pct", 0.0)
        w = roi_data.get("w_pct", 0.0)
        h = roi_data.get("h_pct", 0.0)

        if x == 0.0 and y == 0.0 and w == 0.0 and h == 0.0:
            warnings.append(f"ROI '{roi_name}' содержит нулевые координаты (требует разметки)")

        for key, val in [("x_pct", x), ("y_pct", y), ("w_pct", w), ("h_pct", h)]:
            if not (0.0 <= val <= 1.0):
                errors.append(f"ROI '{roi_name}' параметр {key} = {val} выходит за рамки [0.0 - 1.0]")

    # 3. Проверка существования файлов картинок
    images = config.get("images", {})
    for img_key, img_filename in images.items():
        img_path = images_dir / img_filename
        if not img_path.exists():
            errors.append(f"Картинка '{img_key}' не найдена в папке images/: {img_filename}")

    # Вывод отчета
    print(f"\n==========================================")
    print(f" Отчет проверки профиля: '{clean_game_id}'")
    print(f"==========================================")
    if warnings:
        for w in warnings:
            print(f"[WARN] {w}")

    if errors:
        for err in errors:
            print(f"[FAIL] {err}")
        print(f"Результат: Проверка НЕ пройдена ({len(errors)} ошибок)\n")
        return False

    print(f"[OK] Профиль полностью валиден и готов к работе!\n")
    return True


def interactive_menu():
    """Интерактивное меню управления профилями движка."""
    while True:
        print("==========================================")
        print("     МЕНЕДЖЕР ПРОФИЛЕЙ ИГР ДВИЖКА         ")
        print("==========================================")
        print("1. Создать чистую структуру для новой игры")
        print("2. Клонировать существующую игру")
        print("3. Проверить готовность игры")
        print("4. Показать список доступных игр")
        print("0. Выход")
        print("------------------------------------------")

        choice = input("Выберите действие (0-4): ").strip()

        if choice == "1":
            game_name = input("\nВведите ID новой игры (например: mir5): ").strip()
            if game_name:
                create_game_scaffold(game_name)

        elif choice == "2":
            games = get_existing_games()
            if not games:
                print("\n[!] Нет доступных игр для клонирования.\n")
                continue

            print("\nВыберите игру-донор для клонирования:")
            for idx, g in enumerate(games, 1):
                print(f"  {idx}. {g}")

            source_idx = input(f"\nНомер игры-донора (1-{len(games)}): ").strip()
            if source_idx.isdigit() and 1 <= int(source_idx) <= len(games):
                source_game = games[int(source_idx) - 1]
                target_game = input(f"Введите название/ID новой игры: ").strip()
                if target_game:
                    clone_game_profile(source_game, target_game)
            else:
                print("[!] Неверный выбор игры-донора.\n")

        elif choice == "3":
            games = get_existing_games()
            if not games:
                print("\n[!] Список игр пуст.\n")
                continue

            print("\nДоступные игры:")
            for idx, g in enumerate(games, 1):
                print(f"  {idx}. {g}")

            selected = input(f"\nВыберите номер игры (1-{len(games)}) или введите имя: ").strip()
            if selected.isdigit() and 1 <= int(selected) <= len(games):
                target_game = games[int(selected) - 1]
            else:
                target_game = selected

            if target_game:
                validate_game_profile(target_game)

        elif choice == "4":
            games = get_existing_games()
            print("\n--- Список зарегистрированных игр ---")
            if games:
                for g in games:
                    print(f"  - {g}")
            else:
                print("  Папка games/ пуста.")
            print()

        elif choice == "0":
            print("\nЗавершение работы менеджера профилей.")
            break
        else:
            print("\n[!] Неверный ввод. Выберите от 0 до 4.\n")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        cmd = sys.argv[1].lower()
        game = sys.argv[2]
        if cmd == "create":
            create_game_scaffold(game)
        elif cmd == "check":
            validate_game_profile(game)
        elif cmd == "clone" and len(sys.argv) >= 4:
            clone_game_profile(game, sys.argv[3])
    else:
        interactive_menu()