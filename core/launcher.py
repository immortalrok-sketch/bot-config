"""
Модуль управления автозапуском игр через их нативные лаунчеры.
Слой CORE.

Два пути запуска:
  1. launcher_clicks (в config.json игры) — клики по ROI без распознавания.
  2. Резерв — старая логика с поиском кнопок по шаблонам (для игр без launcher_clicks).
"""
import os
import re
import time
import random
import operator
from typing import Dict, Any, List

import win32api
import win32con
import win32gui

from vision.vision import find_template_in_launcher_window as find_template_in_window
import inputs.windows as windows
import inputs.mouse_input as mouse
from services.debug_logger import logger
from core.profile_loader import GameProfile


def human_sleep(min_sec: float, max_sec: float) -> None:
    """Человекоподобная рандомизированная задержка."""
    time.sleep(random.uniform(min_sec, max_sec))


# ---------------------------------------------------------------------------
# КЛИКИ ПО ROI
# ---------------------------------------------------------------------------

_COND_RE = re.compile(r"^\s*slot_index\s*(==|!=|>=|<=|>|<)\s*(\d+)\s*$")
_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


def _check_condition(condition: Any, slot_index: int) -> bool:
    """
    Проверяет поле "when" шага. Поддерживается:
      "always" (или отсутствие поля), "slot_index > 1", "slot_index == 2" и т.п.
    Без eval — только разбор простого сравнения.
    """
    if not condition or str(condition).strip().lower() == "always":
        return True

    match = _COND_RE.match(str(condition))
    if not match:
        logger.warning(f"[GameLauncher] Непонятное условие '{condition}' — шаг пропущен.")
        return False

    op, value = match.group(1), int(match.group(2))
    return _OPS[op](slot_index, value)


def _click_inside_roi(hwnd: int, roi: Dict[str, float], margin: float = 0.2) -> bool:
    """
    Кликает в случайную точку ВНУТРИ ROI (с отступом margin от краёв,
    чтобы не попадать на границу кнопки). Координаты — доли от реального
    размера клиентской области окна, поэтому не зависят от разрешения.
    """
    if not all(k in roi for k in ("x_pct", "y_pct", "w_pct", "h_pct")):
        logger.error(f"[GameLauncher] В ROI не хватает x_pct/y_pct/w_pct/h_pct: {roi}")
        return False

    _, _, w, h = windows.get_client_area_rect(hwnd)
    if w <= 10 or h <= 10:
        logger.error(f"[GameLauncher] Невалидная клиентская зона окна {hwnd} ({w}x{h}).")
        return False

    x1 = roi["x_pct"] * w
    x2 = (roi["x_pct"] + roi["w_pct"]) * w
    y1 = roi["y_pct"] * h
    y2 = (roi["y_pct"] + roi["h_pct"]) * h

    pad_x = (x2 - x1) * margin
    pad_y = (y2 - y1) * margin

    rx = int(random.uniform(x1 + pad_x, x2 - pad_x))
    ry = int(random.uniform(y1 + pad_y, y2 - pad_y))
    sx, sy = windows.client_to_screen(hwnd, rx, ry)

    # Защита: кликаем только если точка действительно внутри видимого окна лаунчера.
    # У свёрнутого/скрытого окна координаты уходят далеко за экран (около -32000),
    # и мышь улетела бы в угол экрана.
    wl, wt, ww, wh = windows.get_window_position(hwnd)
    vx = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    vy = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    vw = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    vh = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    inside_window = wl <= sx < wl + ww and wt <= sy < wt + wh
    inside_screen = vx <= sx < vx + vw and vy <= sy < vy + vh
    if not (inside_window and inside_screen):
        logger.error(
            f"[GameLauncher] Клик ОТМЕНЁН: точка экрана ({sx}, {sy}) вне окна лаунчера "
            f"(окно: x={wl}, y={wt}, {ww}x{wh}; клиентская зона {w}x{h}; свёрнуто="
            f"{bool(win32gui.IsIconic(hwnd))}). Мышь не двигалась."
        )
        return False

    logger.info(f"[GameLauncher] Клик: окно({rx}, {ry}) -> экран({sx}, {sy})")

    if hasattr(mouse, "human_click"):
        mouse.human_click(sx, sy)
    else:
        windows.click_hwnd(hwnd, rx, ry)
    return True


def run_launcher_clicks(hwnd: int, steps: List[Dict[str, Any]], slot_index: int) -> bool:
    """
    Выполняет шаги из config.json -> "launcher_clicks" по порядку.
    Формат шага:
      {
        "step": "play_button",            # имя для логов (необязательно)
        "roi": {x_pct, y_pct, w_pct, h_pct},
        "wait_after_sec": [3.0, 4.5],     # пауза после клика (необязательно)
        "when": "always" | "slot_index > 1"   # условие (необязательно)
      }
    """
    executed = 0

    for idx, step in enumerate(steps, start=1):
        name = step.get("step", f"step_{idx}")

        if not _check_condition(step.get("when"), slot_index):
            logger.info(f"[GameLauncher] Шаг '{name}' пропущен по условию '{step.get('when')}'.")
            continue

        roi = step.get("roi")
        if not roi:
            logger.error(f"[GameLauncher] У шага '{name}' нет ROI — пропуск.")
            continue

        # Если лаунчер успел свернуться/скрыться — возвращаем его перед кликом
        if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
            logger.warning(f"[GameLauncher] Перед шагом '{name}' окно лаунчера свёрнуто — возвращаю.")
            windows.focus_window(hwnd)
            human_sleep(1.0, 1.5)

        logger.info(f"[GameLauncher] Шаг '{name}' (слот #{slot_index})...")
        if not _click_inside_roi(hwnd, roi):
            logger.error(f"[GameLauncher] Шаг '{name}' не выполнен — запуск прерван.")
            return False

        executed += 1
        wait = step.get("wait_after_sec", [1.0, 2.0])
        human_sleep(wait[0], wait[1])

    if executed == 0:
        logger.error("[GameLauncher] Ни один шаг launcher_clicks не выполнился.")
        return False

    return True


# ---------------------------------------------------------------------------
# СТАРЫЙ ПУТЬ (РАСПОЗНАВАНИЕ ПО ШАБЛОНАМ) — РЕЗЕРВ
# ---------------------------------------------------------------------------

def _resolve_image_path(game_name: str, img_filename: str) -> str:
    """Сборка корректного пути к файлам шаблонов."""
    if not img_filename:
        return ""
    if img_filename.startswith("games/"):
        return os.path.normpath(img_filename)
    return os.path.normpath(f"games/{game_name}/images/{img_filename}")


def _run_launcher_vision(game_cfg: Dict[str, Any], game_name: str, launcher_hwnd: int, slot_index: int) -> bool:
    """Старая логика: поиск аккаунта и кнопки 'Играть' по шаблонам."""
    images_cfg = game_cfg.get("images", {})
    rec_cfg = game_cfg.get("recognition", {})
    confidence = rec_cfg.get("confidence_play_button", 0.70)

    # Переключение аккаунта для slot_index > 1
    if slot_index > 1:
        acc_inactive_file = images_cfg.get("acc_inactive", "acc_inactive.png")
        acc_path = _resolve_image_path(game_name, acc_inactive_file)

        if os.path.exists(acc_path):
            logger.info(f"[GameLauncher] Слот #{slot_index}: Поиск 2-го аккаунта ({acc_path})...")

            match_acc = None
            for _ in range(3):
                match_acc = find_template_in_window(launcher_hwnd, acc_path, confidence=confidence)
                if match_acc:
                    break
                human_sleep(0.8, 1.5)

            if match_acc and isinstance(match_acc, dict) and "center" in match_acc:
                lx, ly = match_acc["center"]
                sx, sy = windows.client_to_screen(launcher_hwnd, lx, ly)
                logger.info(f"[GameLauncher] Клик по переключению аккаунта ({sx}, {sy})...")

                if hasattr(mouse, "human_click"):
                    mouse.human_click(sx, sy)
                else:
                    windows.click_hwnd(launcher_hwnd, lx, ly)

                wait_acc = game_cfg.get("timings", {}).get("wait_between_accounts_sec", 12)
                human_sleep(wait_acc, wait_acc + 2.5)
            else:
                logger.warning("[GameLauncher] Шаблон 2-го аккаунта не найден. Пробуем кликать старт...")

    # Поиск кнопки "Играть" с повторными попытками
    btn_file = images_cfg.get("play_button") or game_cfg.get("launcher", {}).get("start_button_image") or "play.png"
    template_path = _resolve_image_path(game_name, btn_file)

    if not os.path.exists(template_path):
        logger.error(f"[GameLauncher] Файл кнопки 'Играть' не найден: '{template_path}'")
        return False

    logger.info(f"[GameLauncher] Поиск кнопки 'Играть' (Шаблон: '{template_path}')...")

    match_play = None
    max_retries = 5

    for attempt in range(1, max_retries + 1):
        match_play = find_template_in_window(launcher_hwnd, template_path, confidence=confidence)
        if match_play and isinstance(match_play, dict) and "center" in match_play:
            break
        logger.info(f"[GameLauncher] Попытка {attempt}/{max_retries}: Кнопка пока не отрисовалась, ждем...")
        human_sleep(1.5, 2.5)

    if match_play and isinstance(match_play, dict) and "center" in match_play:
        lx, ly = match_play["center"]
        sx, sy = windows.client_to_screen(launcher_hwnd, lx, ly)

        logger.info(f"[GameLauncher] Кнопка найдена ({sx}, {sy})! Запуск игрового клиента...")
        if hasattr(mouse, "human_click"):
            mouse.human_click(sx, sy)
        else:
            windows.click_hwnd(launcher_hwnd, lx, ly)

        human_sleep(3.0, 4.5)
        return True

    logger.error(f"[GameLauncher] Кнопка 'Играть' не распознана за {max_retries} попыток!")
    return False


# ---------------------------------------------------------------------------
# ТОЧКА ВХОДА
# ---------------------------------------------------------------------------

def run_launcher_logic(game_cfg: Dict[str, Any], slot_index: int = 1) -> bool:
    """Запускает авто-старт игры через лаунчер."""
    game_name = game_cfg.get("_game_name") or game_cfg.get("game_name") or "ymir"

    try:
        profile = GameProfile(game_name)
        launcher_exe = profile.get_launcher_path()
    except Exception as e:
        logger.warning(f"[GameLauncher] Ошибка профиля '{game_name}': {e}")
        launcher_exe = ""

    launcher_title = (
        game_cfg.get("launcher_title")
        or game_cfg.get("launcher", {}).get("window_title")
        or game_name
    )

    logger.info(f"[GameLauncher] Старт авто-запуска слота #{slot_index} для профиля '{game_name}'...")

    # Ищем ТОЛЬКО видимое окно лаунчера
    hwnds = windows.get_game_windows(launcher_title, only_visible=True)
    launcher_hwnd = hwnds[0] if hwnds else None

    # Если окно не найдено или оно скрыто в трее — разворачиваем через вызов .exe
    if not launcher_hwnd:
        if not launcher_exe:
            logger.error(f"[GameLauncher] Отмена: путь к EXE не задан в конфиге для '{game_name}'.")
            return False

        logger.info(f"[GameLauncher] Лаунчер свернут в трей или не открыт. Разворачиваем через EXE: {launcher_exe}")
        windows.launch_process(launcher_exe)

        startup_wait = game_cfg.get("launcher", {}).get("startup_wait_sec", 10)
        human_sleep(startup_wait, startup_wait + 2.0)

        hwnds = windows.get_game_windows(launcher_title, only_visible=True)
        launcher_hwnd = hwnds[0] if hwnds else None

    if not launcher_hwnd:
        logger.error(f"[GameLauncher] Видимое окно лаунчера '{game_name}' (Заголовок: '{launcher_title}') не найдено!")
        return False

    # Фокусируем окно лаунчера на передний план. Клики идут реальной мышью по
    # экранным координатам, поэтому без подтверждённого фокуса кликать нельзя.
    focused = False
    for _ in range(3):
        if windows.focus_window(launcher_hwnd):
            focused = True
            break
        human_sleep(0.5, 1.0)

    if not focused:
        logger.error("[GameLauncher] Лаунчер не вышел на передний план — клики отменены, чтобы не попасть в чужое окно.")
        return False

    human_sleep(1.5, 2.5)  # Задержка на отрисовку UI после фокуса

    # --- Путь 1: клики по ROI из конфига ---
    click_steps = game_cfg.get("launcher_clicks")
    if click_steps:
        logger.info(f"[GameLauncher] Режим: клики по ROI ({len(click_steps)} шаг(ов) в конфиге).")
        return run_launcher_clicks(launcher_hwnd, click_steps, slot_index)

    # --- Путь 2 (резерв): распознавание по шаблонам ---
    logger.info("[GameLauncher] launcher_clicks не задан — режим распознавания по шаблонам.")
    return _run_launcher_vision(game_cfg, game_name, launcher_hwnd, slot_index)