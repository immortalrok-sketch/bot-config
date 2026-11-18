"""
Замер: на что уходит время и процессор при чтении статов (OCR). Слой TOOLS.

Запуск (из корня проекта):
    python tools/profile_ocr.py rf_next
    python tools/profile_ocr.py rf_next --image путь/к/кадру.png --reads 4
Можно запустить и двойным кликом: тогда он покажет список игр и спросит номер.

Берёт готовый кадр (по умолчанию games/<игра>/ui_screenshots/game_main.png — его делает
grab_screens.py), приводит к 960x540 и прогоняет чтение статов несколько раз в трёх режимах
onnxruntime. Печатает:
  * сколько времени и процессорного времени уходит на одно чтение и сколько ядер это в среднем;
  * для режима «как было» — разбивку по полям и по движкам (RapidOCR быстрый / с детекцией / Tesseract).

Игру и бота запускать не нужно. Лучше закрыть всё лишнее, чтобы цифры не «плавали».
Присылай весь вывод целиком.
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np


def load_frame(path: Path) -> np.ndarray:
    """Читает картинку (в том числе из путей с кириллицей) и приводит к 960x540."""
    data = np.fromfile(str(path), dtype=np.uint8)
    frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if frame is None:
        raise SystemExit(f"[!] Не удалось прочитать изображение: {path}")
    if frame.shape[1] != 960 or frame.shape[0] != 540:
        print(f"[i] Кадр {frame.shape[1]}x{frame.shape[0]} приведён к 960x540.")
        frame = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
    return frame


CURRENT = {"probe": None}     # какой счётчик сейчас активен (обёртка Tesseract ставится на модуль один раз)


class Probe:
    """Счётчики вызовов и времени по полям и движкам."""

    def __init__(self):
        self.key = "?"
        self.stats = {}          # (поле, движок) -> [вызовов, секунд]

    def reset(self):
        self.key = "?"
        self.stats = {}

    def add(self, engine: str, seconds: float):
        rec = self.stats.setdefault((self.key, engine), [0, 0.0])
        rec[0] += 1
        rec[1] += seconds


class TimedRapid:
    """Обёртка над RapidOCR: считает быстрые вызовы и вызовы с детекцией текста."""

    def __init__(self, inner, probe: Probe):
        self._inner = inner
        self._probe = probe

    def __call__(self, img, *args, **kwargs):
        engine = "RapidOCR с детекцией" if kwargs.get("use_det") else "RapidOCR быстрый"
        t0 = time.perf_counter()
        try:
            return self._inner(img, *args, **kwargs)
        finally:
            self._probe.add(engine, time.perf_counter() - t0)


def instrument(reader, ocr_mod, probe: Probe):
    """Подключает замеры к уже созданному ридеру."""
    CURRENT["probe"] = probe
    reader.ocr = TimedRapid(reader.ocr, probe)

    original_run_ocr = reader._run_ocr

    def run_ocr(processed, key, *args, **kwargs):
        probe.key = key
        return original_run_ocr(processed, key, *args, **kwargs)

    reader._run_ocr = run_ocr

    tess = getattr(ocr_mod, "pytesseract", None)
    if tess is not None and not getattr(tess, "_profile_wrapped", False):
        original = tess.image_to_string

        def timed(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                if CURRENT["probe"] is not None:
                    CURRENT["probe"].add("Tesseract", time.perf_counter() - t0)

        tess.image_to_string = timed
        tess._profile_wrapped = True


def measure(ocr_mod, tuning: dict, frame, game: str, reads: int):
    """Одно чтение = read_stats по всем полям. Возвращает (сек/чтение, ЦП-сек/чтение, статистика)."""
    import psutil

    ocr_mod.ORT_TUNING.clear()
    ocr_mod.ORT_TUNING.update(tuning)
    reader = ocr_mod.HUDReader(debug_mode=False)       # новые сессии onnxruntime с этими настройками
    probe = Probe()
    instrument(reader, ocr_mod, probe)

    # прогрев: первое чтение не считаем (загрузка весов, первые вызовы)
    reader.read_stats(frame, game_name=game, window_name="warmup", game_cfg=None, debug=False)
    probe.reset()

    proc = psutil.Process()
    cpu0 = proc.cpu_times()
    t0 = time.perf_counter()
    for i in range(reads):
        reader.read_stats(frame, game_name=game, window_name=f"probe{i}", game_cfg=None, debug=False)
    wall = time.perf_counter() - t0
    cpu1 = proc.cpu_times()
    cpu = (cpu1.user + cpu1.system) - (cpu0.user + cpu0.system)
    return wall / reads, cpu / reads, {k: list(v) for k, v in probe.stats.items()}


def print_breakdown(stats: dict, reads: int, wall_per_read: float):
    print("\n  Разбивка на ОДНО чтение (сек):")
    print(f"  {'поле':<14}{'движок':<24}{'вызовов':>9}{'время, с':>11}")
    total_engine = 0.0
    for (key, engine), (calls, seconds) in sorted(stats.items(), key=lambda kv: -kv[1][1]):
        total_engine += seconds / reads
        print(f"  {key:<14}{engine:<24}{calls / reads:>9.1f}{seconds / reads:>11.2f}")
    other = max(0.0, wall_per_read - total_engine)
    print(f"  {'(прочее)':<14}{'предобработка, кропы':<24}{'':>9}{other:>11.2f}")


def choose_game() -> str:
    """Список папок игр и выбор по номеру (если игру не передали аргументом)."""
    games_dir = PROJECT_ROOT / "games"
    games = sorted(d.name for d in games_dir.iterdir() if d.is_dir() and not d.name.startswith(".")) \
        if games_dir.exists() else []
    if not games:
        raise SystemExit(f"[!] В {games_dir} нет игр.")
    print("=== Выбор игры ===")
    for n, g in enumerate(games, 1):
        print(f" {n} — {g}")
    choice = input(f"Номер игры (1-{len(games)}) [1]: ").strip() or "1"
    if choice.isdigit() and 1 <= int(choice) <= len(games):
        return games[int(choice) - 1]
    return games[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер нагрузки OCR статов")
    parser.add_argument("game", nargs="?", help="имя папки игры, например rf_next (если не указать — спросит)")
    parser.add_argument("--image", help="кадр 960x540 (по умолчанию ui_screenshots/game_main.png)")
    parser.add_argument("--reads", type=int, default=4, help="сколько чтений усреднять (по умолчанию 4)")
    args = parser.parse_args()
    asked_interactively = args.game is None
    if asked_interactively:
        args.game = choose_game()

    image_path = Path(args.image) if args.image else PROJECT_ROOT / "games" / args.game / "ui_screenshots" / "game_main.png"
    if not image_path.exists():
        raise SystemExit(f"[!] Нет кадра: {image_path}\n    Сними его через tools/grab_screens.py или укажи --image.")
    frame = load_frame(image_path)

    try:
        import vision.ocr_reader as ocr_mod
    except Exception as e:
        raise SystemExit(f"[!] Не удалось загрузить vision.ocr_reader: {e}")
    if not hasattr(ocr_mod, "ORT_TUNING"):
        raise SystemExit("[!] В ocr_reader.py нет ORT_TUNING — нужна новая версия файла.")

    modes = [
        ("как было (onnxruntime по умолчанию)", {"enabled": False}),
        ("onnxruntime: 2 потока, без активного ожидания", {"enabled": True, "intra": 2, "inter": 1, "no_spin": True}),
        ("onnxruntime: 1 поток, без активного ожидания", {"enabled": True, "intra": 1, "inter": 1, "no_spin": True}),
    ]

    import os
    print(f"Игра: {args.game} | кадр: {image_path.name} | чтений на режим: {args.reads} | логических ядер: {os.cpu_count()}")
    print("Идёт замер, это займёт около минуты...\n")

    rows = []
    first_breakdown = None
    for label, tuning in modes:
        wall, cpu, stats = measure(ocr_mod, tuning, frame, args.game, args.reads)
        rows.append((label, wall, cpu))
        if first_breakdown is None:
            first_breakdown = (stats, wall)

    print(f"{'режим':<52}{'время/чтение':>14}{'ЦП-сек/чтение':>15}{'ядер в среднем':>16}")
    for label, wall, cpu in rows:
        cores = cpu / wall if wall > 0 else 0
        print(f"{label:<52}{wall:>13.2f}с{cpu:>14.2f}с{cores:>16.1f}")
    print("\n(ЦП-сек считает процесс Python вместе с потоками onnxruntime; процессы Tesseract сюда не входят,")
    print(" их время видно в разбивке ниже.)")

    stats, wall = first_breakdown
    print_breakdown(stats, args.reads, wall)
    if asked_interactively:
        input("\nНажми Enter, чтобы закрыть...")


if __name__ == "__main__":
    main()