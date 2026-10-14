"""
Инструмент съёмки скриншотов интерфейса для новой игры.
Слой TOOLS.

Запуск:  python tools/grab_screens.py

Как работает:
  1. Выбираешь игру из games/.
  2. Заголовки окон лаунчера и игры берутся из games/<игра>/config.json
     (если там заглушки — предложит выбрать окно из списка открытых).
  3. Дальше по шагам снимаешь кадры горячими клавишами. Они глобальные,
     поэтому работают, когда в фокусе игра, а не консоль:

         F9         — снять текущий шаг
         F10        — пропустить шаг
         Ctrl+Alt+Q — выход

Кадры сохраняются в games/<игра>/ui_screenshots/ в том же разрешении (960x540),
в котором их видит движок, поэтому ROI и шаблоны потом совпадают с боем.
Файл с тем же именем перезаписывается — удобно переснимать неудачный кадр.
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GAMES_DIR = PROJECT_ROOT / "games"

# Виртуальные коды клавиш Windows
VK_F9, VK_F10, VK_Q = 0x78, 0x79, 0x51
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000

# Звуковой сигнал после F9 (высокий — снято, низкий — ошибка). По умолчанию выключен.
SOUND_FEEDBACK = False

# (тип окна, имя файла, подсказка)
STEPS = [
    ("launcher", "launcher.png",
     "Лаунчер: страница игры, видны кнопка «Играть» и аккаунты"),
    ("game", "game_main.png",
     "Игра: главный экран с ВКЛЮЧЁННЫМ авто-боем "
     "(уровень, % опыта, боевая мощь, бриллианты, иконка авто-боя)"),
    ("game", "game_saving.png",
     "Игра: режим экономии при авто-бое (надпись или другой ориентир)"),
]

# Заглушки, которые остаются в шаблонах конфига, пока заголовок не вписан
PLACEHOLDER_TITLES = {
    "", "launcher window", "game window", "launcher title", "game window title",
    "заголовок окна лаунчера", "заголовок окна игры",
}


# ---------------------------------------------------------------------------
# Работа с конфигом и окнами
# ---------------------------------------------------------------------------

def list_games() -> list:
    if not GAMES_DIR.exists():
        return []
    return sorted(d.name for d in GAMES_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))


def load_cfg(game: str) -> dict:
    path = GAMES_DIR / game / "config.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] Не удалось прочитать {path}: {e}")
        return {}


def title_from_cfg(cfg: dict, kind: str) -> str:
    section = "launcher" if kind == "launcher" else "game"
    return str((cfg.get(section) or {}).get("window_title", "") or "").strip()


def is_placeholder(title: str) -> bool:
    return title.strip().lower() in PLACEHOLDER_TITLES


def list_visible_windows() -> list:
    """[(hwnd, заголовок)] всех видимых окон с непустым заголовком."""
    import win32gui
    found = []

    def cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title and win32gui.IsWindowVisible(hwnd):
            found.append((hwnd, title))
        return True

    win32gui.EnumWindows(cb, None)
    return found


def find_windows(title: str) -> list:
    import inputs.windows as windows
    return windows.get_game_windows(title, only_visible=True)


def ensure_visible(hwnd: int) -> None:
    """Если окно свёрнуто (например, лаунчер в трее), разворачивает его и ждёт отрисовки."""
    import time
    import win32con
    import win32gui
    try:
        if win32gui.IsIconic(hwnd):
            print("[i] Окно свёрнуто — разворачиваю.")
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(1.5)
    except Exception:
        pass


def grab(kind: str, hwnd: int):
    """Кадр так, как его видит движок: для лаунчера PrintWindow, для игры обычный захват."""
    from vision.vision import capture_game_frame, capture_ui_frame
    return (capture_ui_frame if kind == "launcher" else capture_game_frame)(hwnd)


def save_png(path: Path, frame) -> bool:
    """Сохраняет через imencode: cv2.imwrite молча не пишет в пути с кириллицей."""
    import cv2
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        return False
    with open(path, "wb") as f:
        f.write(buf.tobytes())
    return True


# ---------------------------------------------------------------------------
# Сессия съёмки
# ---------------------------------------------------------------------------

class ShotSession:
    def __init__(self, game: str, cfg: dict, steps=STEPS, ask=input):
        self.game = game
        self.cfg = cfg
        self.steps = list(steps)
        self.ask = ask
        self.idx = 0
        self.titles = {}
        self.saved = []
        self.skipped = []
        self.shots_dir = GAMES_DIR / game / "ui_screenshots"

    @property
    def done(self) -> bool:
        return self.idx >= len(self.steps)

    def prepare_titles(self) -> None:
        """Заголовки из конфига; если там заглушки — выбор из списка открытых окон."""
        kinds = []
        for step in self.steps:                  # в порядке шагов: сначала лаунчер, потом игра
            if step[0] not in kinds:
                kinds.append(step[0])
        for kind in kinds:
            label = "лаунчера" if kind == "launcher" else "игры"
            title = title_from_cfg(self.cfg, kind)
            if title and not is_placeholder(title):
                self.titles[kind] = title
                print(f"[Заголовок {label}] из конфига: '{title}'")
                continue

            print(f"\n[Заголовок {label}] в config.json не задан (стоит заглушка).")
            print("Открой это окно и выбери его в списке (или Enter — пропустить):")
            wins = list_visible_windows()
            for n, (hwnd, t) in enumerate(wins, 1):
                print(f"  {n:>2}. {t}")
            choice = self.ask("Номер окна: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(wins):
                self.titles[kind] = wins[int(choice) - 1][1]
                print(f"  -> '{self.titles[kind]}'. Впиши в config.json короткую часть этого заголовка.")
            else:
                print("  -> пропущено, шаги для этого окна работать не будут.")

    def announce(self) -> None:
        if self.done:
            return
        kind, filename, hint = self.steps[self.idx]
        print(f"\nШаг {self.idx + 1}/{len(self.steps)}: {hint}")
        print(f"   файл: {filename} | F9 — снять, F10 — пропустить, Ctrl+Alt+Q — выход")

    def capture_current(self) -> bool:
        if self.done:
            return False
        kind, filename, _ = self.steps[self.idx]

        title = self.titles.get(kind)
        if not title:
            print(f"[!] Для этого шага не задан заголовок окна ({kind}).")
            return False

        hwnds = find_windows(title)
        if not hwnds:
            print(f"[!] Окно '{title}' не найдено. Открой его и нажми F9 снова.")
            return False
        if len(hwnds) > 1:
            print(f"[i] Найдено окон: {len(hwnds)}, снимаю первое.")

        ensure_visible(hwnds[0])
        frame = grab(kind, hwnds[0])
        if frame is None:
            print("[!] Кадр не получен (окно свёрнуто или закрыто). Попробуй ещё раз.")
            return False

        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / filename
        if not save_png(path, frame):
            print(f"[!] Не удалось сохранить {path}")
            return False

        print(f"[OK] {filename} сохранён ({frame.shape[1]}x{frame.shape[0]})")
        self.saved.append(filename)
        self.idx += 1
        self.announce()
        return True

    def skip_current(self) -> None:
        if self.done:
            return
        _, filename, _ = self.steps[self.idx]
        print(f"[->] Пропущен: {filename}")
        self.skipped.append(filename)
        self.idx += 1
        self.announce()

    def summary(self) -> None:
        print("\n=== Итог ===")
        print(f"Папка: {self.shots_dir}")
        print(f"Снято: {', '.join(self.saved) if self.saved else 'ничего'}")
        if self.skipped:
            print(f"Пропущено: {', '.join(self.skipped)}")
        print("Дальше: tools/roi_selector.py (разметка зон) и нарезка шаблонов из этих кадров.")


# ---------------------------------------------------------------------------
# Горячие клавиши (только Windows, без сторонних библиотек)
# ---------------------------------------------------------------------------

def run_hotkey_loop(session: ShotSession) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WM_HOTKEY = 0x0312
    keys = {
        1: ("capture", VK_F9, MOD_NOREPEAT, "F9"),
        2: ("skip", VK_F10, MOD_NOREPEAT, "F10"),
        3: ("quit", VK_Q, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, "Ctrl+Alt+Q"),
    }

    for hk_id, (_, vk, mods, name) in keys.items():
        if not user32.RegisterHotKey(None, hk_id, mods, vk):
            print(f"[!] Не удалось занять клавишу {name} (её уже использует другая программа).")

    session.announce()
    try:
        msg = wintypes.MSG()
        while not session.done and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message != WM_HOTKEY:
                continue
            action = keys.get(msg.wParam, ("", 0, 0, ""))[0]
            if action == "capture":
                ok = session.capture_current()
                if SOUND_FEEDBACK:
                    import winsound
                    winsound.Beep(1000 if ok else 300, 150 if ok else 300)
            elif action == "skip":
                session.skip_current()
            elif action == "quit":
                break
    finally:
        for hk_id in keys:
            user32.UnregisterHotKey(None, hk_id)


def choose_game() -> str:
    games = list_games()
    if not games:
        print("[!] В папке games/ нет игр. Сначала создай профиль: tools/prepare_new_game.py")
        return ""
    print("=== Выбор игры ===")
    for n, g in enumerate(games, 1):
        print(f" {n} — {g}")
    choice = input(f"Номер игры (1-{len(games)}) [1]: ").strip() or "1"
    if choice.isdigit() and 1 <= int(choice) <= len(games):
        return games[int(choice) - 1]
    return games[0]


def main() -> None:
    game = choose_game()
    if not game:
        return

    session = ShotSession(game, load_cfg(game))
    session.prepare_titles()

    if os.name != "nt":
        print("[!] Горячие клавиши работают только в Windows.")
        return

    print("\nГотово к съёмке. Переключайся на нужное окно и жми F9.")
    run_hotkey_loop(session)
    session.summary()


if __name__ == "__main__":
    main()