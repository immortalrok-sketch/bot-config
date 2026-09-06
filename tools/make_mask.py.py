import os
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMES_DIR = os.path.join(BASE_DIR, "games")


def get_game_list() -> list[str]:
    """Сканирует директорию games/ и возвращает список папок игр."""
    if not os.path.exists(GAMES_DIR):
        return []
    return [
        d for d in os.listdir(GAMES_DIR)
        if os.path.isdir(os.path.join(GAMES_DIR, d))
    ]


def select_game(games: list[str]) -> str | None:
    """Выводит интерактивное CLI-меню для выбора игры."""
    print("\n=== ВЫБОР ИГРЫ ДЛЯ ГЕНЕРАЦИИ МАСКИ ===")
    for idx, game in enumerate(games, 1):
        print(f"{idx}. {game}")
    print("0. Выход")

    while True:
        choice = input("\nВыберите номер игры: ").strip()
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(games):
            return games[int(choice) - 1]
        print("[!] Неверный ввод, введите корректный номер.")


def generate_solid_mask(game_name: str) -> None:
    """Генерирует монолитную бинарную маску без внутренних дыр."""
    target_dir = os.path.join(GAMES_DIR, game_name, "images")
    icon_path = os.path.join(target_dir, "diamond_icon.png")
    mask_path = os.path.join(target_dir, "diamond_mask.png")

    if not os.path.exists(icon_path):
        print(f"\n[ERROR] Файл иконки не найден: {icon_path}")
        return

    img = cv2.imread(icon_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        print("\n[ERROR] Не удалось прочитать изображение.")
        return

    h, w = img.shape[:2]

    # 1. Первичная бинаризация по Alpha-каналу или яркости
    if len(img.shape) == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        _, binary = cv2.threshold(alpha, 1, 255, cv2.THRESH_BINARY)
    else:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 2. Поиск внешних контуров и заливка внутренних дыр (сплошной силуэт)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    solid_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(solid_mask, contours, -1, 255, thickness=cv2.FILLED)

    # 3. Сохранение строго 1-канальной маски (CV_8UC1)
    cv2.imwrite(mask_path, solid_mask)
    print(f"\n[SUCCESS] Монолитная маска создана для '{game_name}': {mask_path}")
    print(f"[INFO] Разрешение: {w}x{h} px | Тип: 1 канал (CV_8UC1)")


if __name__ == "__main__":
    game_list = get_game_list()
    if not game_list:
        print("[ERROR] В папке games/ не найдено подпапок.")
    else:
        selected_game = select_game(game_list)
        if selected_game:
            generate_solid_mask(selected_game)