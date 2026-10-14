import sys
import json
import cv2
import win32api
import win32con
from pathlib import Path

# Автоматическая настройка системных путей
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GAMES_DIR = PROJECT_ROOT / "games"
TOOLS_DIR = PROJECT_ROOT / "tools"
CROPS_DIR = TOOLS_DIR / "debug_crops"

PROFILES = {
    "1": {
        "name": "custom",
        "zones": ["custom_zone"],
    },
    "2": {
        "name": "Main_page",
        "zones": [
            "level",
            "exp_percent",
            "combat_power",
            "diamonds",
        ],
    },
    "3": {
        "name": "auth",
        "zones": [
            "tap_to_start",
            "select_char_btn",
            "queue_dialog",
            "reconnect_btn",
        ],
    },
    "4": {
        "name": "windows",
        "zones": [
            "inventory_grid",
            "mail_icon",
            "close_win_btn",
            "confirm_btn",
        ],
    },
}


def get_screen_working_area() -> tuple[int, int]:
    """Возвращает разрешение экрана Windows для авто-масштабирования окна."""
    try:
        screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        return screen_w, screen_h
    except Exception:
        # Резервный дефолт, если win32api недоступен
        return 1920, 1080


def get_abs_roi(roi_pct: dict, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Переводит относительные % координаты в абсолютные пиксели."""
    x = int(roi_pct["x_pct"] * frame_w)
    y = int(roi_pct["y_pct"] * frame_h)
    w = int(roi_pct["w_pct"] * frame_w)
    h = int(roi_pct["h_pct"] * frame_h)
    return x, y, w, h


def rebuild_annotated_frame(base_img, config_dict: dict, color=(0, 255, 0), thickness=2):
    """Отрисовывает зеленые рамки для всех зафиксированных в этой сессии зон."""
    h_img, w_img = base_img.shape[:2]
    canvas = base_img.copy()
    for z_name, pct_data in config_dict.items():
        x, y, w, h = get_abs_roi(pct_data, w_img, h_img)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, thickness)
    return canvas


def select_game_directory() -> Path:
    """Сканирует директорию games/ и запрашивает целевую игру."""
    games = [d for d in GAMES_DIR.iterdir() if d.is_dir()] if GAMES_DIR.exists() else []

    if not games:
        print("[!] Папка games/ пуста. Создаем структуру по умолчанию: games/rf_next/")
        target = GAMES_DIR / "rf_next"
        target.mkdir(parents=True, exist_ok=True)
        return target

    print("\n=== 1. ВЫБОР ИГРЫ ===")
    for idx, game_dir in enumerate(games, 1):
        print(f" {idx} — {game_dir.name}")

    choice = input(f"Выбери игру (1-{len(games)}) [дефолт: 1]: ").strip() or "1"
    try:
        selected_idx = int(choice) - 1
        if 0 <= selected_idx < len(games):
            return games[selected_idx]
    except ValueError:
        pass

    return games[0]


def get_source_frame(game_dir: Path):
    """Загружает скриншот интерфейса из папки games/<game_name>/ui_screenshots/."""
    screenshots_dir = game_dir / "ui_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    valid_extensions = {".png", ".jpg", ".jpeg"}
    images = [f for f in screenshots_dir.iterdir() if f.is_file() and f.suffix.lower() in valid_extensions]

    if not images:
        print("\n[!] В папке не найдено ни одного скриншота:")
        print(f"    👉 {screenshots_dir}")
        print("    Положи туда файлы скриншотов (.png или .jpg) и запусти инструмент заново.\n")
        return None

    print(f"\n=== 2. ВЫБОР СКРИНШОТА ИНТЕРФЕЙСА ({game_dir.name}/ui_screenshots/) ===")
    for idx, img_path in enumerate(images, 1):
        print(f" {idx} — {img_path.name}")

    choice = input(f"Выбери скриншот (1-{len(images)}) [дефолт: 1]: ").strip() or "1"
    try:
        selected_idx = int(choice) - 1
        if 0 <= selected_idx < len(images):
            target_file = images[selected_idx]
        else:
            target_file = images[0]
    except ValueError:
        target_file = images[0]

    print(f"[+] Загружаем скриншот: {target_file.name}")
    frame = cv2.imread(str(target_file))
    return frame


def run_roi_selector():
    game_dir = select_game_directory()
    game_name = game_dir.name

    frame = get_source_frame(game_dir)
    if frame is None:
        return

    h_img, w_img = frame.shape[:2]

    # --- РАСЧЕТ МАСШТАБА ПОД РАЗРЕШЕНИЕ ЭКРАНА ---
    screen_w, screen_h = get_screen_working_area()
    # Ограничиваем максимальную высоту рабочей зоны (80% высоты экрана для учета рамок и панели)
    max_disp_w = int(screen_w * 0.85)
    max_disp_h = int(screen_h * 0.80)

    scale = min(max_disp_w / w_img, max_disp_h / h_img, 1.0)
    print(f"[Info] Исходный скриншот: {w_img}x{h_img}px | Масштаб показа: {scale:.2f}")

    print("\n=== 3. ВЫБОР ПРОФИЛЯ РАЗМЕТКИ ===")
    print(" 1 — Custom (Быстрый замер 1 произвольной зоны)")
    print(" 2 — HUD (Боевые иконки: Автобой, Сбережение, HP)")
    print(" 3 — Auth (Окна входа, Реконнект)")
    print(" 4 — Windows (Инвентарь, Почта, Диалоги)")
    choice = input("Выбери профиль (1-4) [дефолт: 1]: ").strip() or "1"

    profile = PROFILES.get(choice, PROFILES["1"])
    profile_name = profile["name"]
    target_zones = profile["zones"]

    roi_config = {}
    CROPS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n⚡️ РАЗМЕТКА ЗОН [Черновик -> roi_{profile_name}_draft.json]")
    print("    • Выдели прямоугольник мышкой -> [Space] или [Enter]")
    print("    • Нажми [ESC] -> Меню отмены/пропуска\n")

    idx = 0
    while idx < len(target_zones):
        zone_name = target_zones[idx]
        
        # Строим кадр с уже нарисованными рамками
        annotated_canvas = rebuild_annotated_frame(frame, roi_config)

        # Подгоняем размер кадра для удобного показа на экране
        if scale < 1.0:
            disp_w = int(w_img * scale)
            disp_h = int(h_img * scale)
            display_frame = cv2.resize(annotated_canvas, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
        else:
            display_frame = annotated_canvas

        print("=" * 65)
        print(f"👉 [ШАГ {idx + 1}/{len(target_zones)}] ВЫДЕЛЯЕМ ЗОНУ: ===> {zone_name.upper()} <===")
        print("   Перейди в графическое окно и выдели элемент мышкой.")
        print("=" * 65)

        window_title = f"[{idx + 1}/{len(target_zones)}] {zone_name.upper()} | Space/Enter = OK, ESC = Меню"

        # Получаем выделение с уменьшенной копии
        rx, ry, rw, rh = cv2.selectROI(window_title, display_frame, showCrosshair=True)
        cv2.destroyWindow(window_title)

        if rw > 0 and rh > 0:
            # Пересчитываем координаты с уменьшенной копии обратно в оригинальное 2K разрешение
            orig_x = int(rx / scale)
            orig_y = int(ry / scale)
            orig_w = int(rw / scale)
            orig_h = int(rh / scale)

            # Сохраняем относительные % координаты от исходного 2K разрешения
            roi_config[zone_name] = {
                "x_pct": round(orig_x / w_img, 4),
                "y_pct": round(orig_y / h_img, 4),
                "w_pct": round(orig_w / w_img, 4),
                "h_pct": round(orig_h / h_img, 4),
            }

            # Кроп сохраняем в 100% исходном качестве без сжатия
            crop_img = frame[orig_y : orig_y + orig_h, orig_x : orig_x + orig_w]
            crop_path = CROPS_DIR / f"{game_name}_{zone_name}_crop.png"
            cv2.imwrite(str(crop_path), crop_img)

            print(f"[✓] Зона '{zone_name}' зафиксирована.")
            print(f"    ├─ Кроп сохранен: tools/debug_crops/{crop_path.name}\n")
            idx += 1
        else:
            print(f"\n[!] Выделение зоны '{zone_name}' отменено.")
            action = input("    [r] Повторить | [b] Назад (Undo) | [s] Пропустить: ").strip().lower()
            if action == "b" and idx > 0:
                idx -= 1
                popped = target_zones[idx]
                roi_config.pop(popped, None)
                print(f"    [←] Откат назад. Удалена зона '{popped}'.")
            elif action == "s":
                idx += 1
                print(f"    [->] Зона '{zone_name}' пропущена.")

    # 1. Сохранение черновика координат в папку игры
    json_draft_path = game_dir / f"roi_{profile_name}_draft.json"
    with open(json_draft_path, "w", encoding="utf-8") as f:
        json.dump(roi_config, f, indent=4, ensure_ascii=False)

    # 2. Сохранение общего размеченного скриншота в папку tools/
    full_annotated_frame = rebuild_annotated_frame(frame, roi_config)
    annotated_img_path = TOOLS_DIR / f"{game_name}_{profile_name}_annotated.png"
    cv2.imwrite(str(annotated_img_path), full_annotated_frame)

    print("\n[Успех] Разметка завершена!")
    print(f" ├─ ЧЕРНОВИК JSON: {json_draft_path}")
    print(f" ├─ ОБЩИЙ СКРИНШОТ: {annotated_img_path}")
    print(f" └─ ОТЛАДОЧНЫЕ КРОПЫ: {CROPS_DIR}")


if __name__ == "__main__":
    run_roi_selector()