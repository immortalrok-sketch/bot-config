"""
Модуль управления автозапуском игр через их нативные лаунчеры.
Слой CORE.
"""
import os
import time
import random
import win32gui
from typing import Dict, Any

from vision.vision import find_template_in_window
import inputs.windows as windows
import inputs.mouse_input as mouse
from services.debug_logger import logger
from core.profile_loader import GameProfile


def human_sleep(min_sec: float, max_sec: float) -> None:
    """Человекоподобная рандомизированная задержка."""
    time.sleep(random.uniform(min_sec, max_sec))


def _resolve_image_path(game_name: str, img_filename: str) -> str:
    """Сборка корректного пути к файлам шаблонов."""
    if not img_filename:
        return ""
    if img_filename.startswith("games/"):
        return os.path.normpath(img_filename)
    return os.path.normpath(f"games/{game_name}/images/{img_filename}")


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

    # Фокусируем окно лаунчера на передний план
    windows.focus_window(launcher_hwnd)
    human_sleep(1.5, 2.5)  # Задержка на отрисовку UI после фокуса

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

                if hasattr(mouse, 'human_click'):
                    mouse.human_click(sx, sy)
                else:
                    windows.click_hwnd(launcher_hwnd, lx, ly)

                wait_acc = game_cfg.get("timings", {}).get("wait_between_accounts_sec", 12)
                human_sleep(wait_acc, wait_acc + 2.5)
            else:
                logger.warning(f"[GameLauncher] Шаблон 2-го аккаунта не найден. Пробуем кликать старт...")

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
        if hasattr(mouse, 'human_click'):
            mouse.human_click(sx, sy)
        else:
            windows.click_hwnd(launcher_hwnd, lx, ly)

        human_sleep(3.0, 4.5)
        return True
    else:
        logger.error(f"[GameLauncher] Кнопка 'Играть' не распознана за {max_retries} попыток!")
        return False