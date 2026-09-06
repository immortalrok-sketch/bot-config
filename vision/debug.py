"""
Скрипт глубокой визуальной диагностики геометрии окон и ROI разметки.
Слой VISION / DEBUG.
"""
import os
import sys
import json
import cv2

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from inputs.windows import get_game_windows, get_client_area_rect, get_window_position
from vision.vision import capture_game_frame


def run_geometry_debug(game_name: str = "rf_next", window_title: str = "RF"):
    print("=" * 60)
    print("=== ДИАГНОСТИКА ГЕОМЕТРИИ И ЗОН OCR ===")
    print("=" * 60)

    hwnds = get_game_windows(window_title)
    if not hwnds:
        print(f"[!] Окна с заголовком '{window_title}' не найдены!")
        return

    # Загружаем конфиг с координатами ROI
    cfg_path = os.path.join(ROOT_DIR, "games", game_name, "config.json")
    rois = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            rois = json.load(f).get("ocr_rois", {})

    for idx, hwnd in enumerate(hwnds, start=1):
        win_name = f"{game_name}_{idx}"

        # 1. Внешние границы окна на рабочем столе (включая рамки и заголовок)
        wx, wy, ww, wh = get_window_position(hwnd)

        # 2. Чистая клиентская область (только игровое поле)
        cx, cy, cw, ch = get_client_area_rect(hwnd)

        print(f"\n--- [{win_name}] (HWND: {hwnd}) ---")
        print(f"Внешний Rect (GetWindowRect) : X={wx:<4} Y={wy:<4} W={ww:<4} H={wh:<4}")
        print(f"Клиентская зона (ClientArea) : X={cx:<4} Y={cy:<4} W={cw:<4} H={ch:<4}")
        print(f"Смещение рамок ОС (Шапка/Бока): Top={cy - wy}px, Left={cx - wx}px")

        # 3. Захват кадра
        frame = capture_game_frame(hwnd)
        if frame is None:
            print(f"[!] Ошибка захвата кадра для {win_name}")
            continue

        fh, fw = frame.shape[:2]
        print(f"Размер итогового кадра         : {fw}x{fh}")

        # 4. Отрисовка ярких рамок ROI прямо поверх полного кадра
        debug_frame = frame.copy()
        for key, cfg in rois.items():
            if not all(k in cfg for k in ("x_pct", "y_pct", "w_pct", "h_pct")):
                continue

            x1 = int(cfg["x_pct"] * fw)
            y1 = int(cfg["y_pct"] * fh)
            x2 = int((cfg["x_pct"] + cfg["w_pct"]) * fw)
            y2 = int((cfg["y_pct"] + cfg["h_pct"]) * fh)

            # Зеленый контур прямоугольника ROI
            cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                debug_frame,
                key,
                (x1, max(y1 - 5, 15)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

        # 5. Сохраняем панорамный снимок с сеткой
        out_path = os.path.join(ROOT_DIR, f"debug_full_{win_name}.png")
        cv2.imwrite(out_path, debug_frame)
        print(f"[+] Снимок с наложенными рамками сохранен: debug_full_{win_name}.png")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    run_geometry_debug()
