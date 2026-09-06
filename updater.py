import os
import sys
import shutil
import zipfile
import urllib.request
import json
import subprocess
import time
from typing import List

# --- Явная настройка корневого пути проекта ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMMIT_FILE = os.path.join(BASE_DIR, ".current_commit")

# Файлы и папки, которые КАТЕГОРИЧЕСКИ НЕЛЬЗЯ затирать при обновлении
IGNORE_PATTERNS = {
    "local_node.json",
    ".env",
    ".gitignore",
    "debug_crops",
    "logs",
    "cache",
    ".git",
    ".current_commit"
}

# Обязательные системные файлы ядра
BASE_CRITICAL_FILES = [
    "Engine_Modul.py",
    "main_config.json"
]


def verify_integrity(base_dir: str) -> bool:
    """Динамически проверяет наличие системного ядра и конфигов всех игр в папке games/."""
    # 1. Проверка базовых системных файлов ядра
    for rel_path in BASE_CRITICAL_FILES:
        full_path = os.path.join(base_dir, rel_path)
        if not os.path.exists(full_path):
            print(f"[UPDATER WARN] Отсутствует критический системный файл: {rel_path}")
            return False

    # 2. Динамическая проверка папки games/ и всех конфигураций внутри
    games_dir = os.path.join(base_dir, "games")
    if not os.path.exists(games_dir):
        print("[UPDATER WARN] Директория 'games/' не найдена!")
        return False

    game_folders = [
        d for d in os.listdir(games_dir)
        if os.path.isdir(os.path.join(games_dir, d))
    ]

    if not game_folders:
        print("[UPDATER WARN] В папке 'games/' не обнаружено ни одного игрового модуля!")
        return False

    # Проверяем наличие config.json в каждой игровой директории
    for game_name in game_folders:
        game_cfg_path = os.path.join(games_dir, game_name, "config.json")
        if not os.path.exists(game_cfg_path):
            print(f"[UPDATER WARN] Отсутствует конфиг для игры '{game_name}': games/{game_name}/config.json")
            return False

    return True


def load_or_create_node_config(default_data: dict, filepath: str = None) -> dict:
    """Загружает локальный паспорт ПК или создает его из дефолтных данных."""
    if filepath is None:
        filepath = os.path.join(BASE_DIR, "local_node.json")
    elif not os.path.isabs(filepath):
        filepath = os.path.join(BASE_DIR, filepath)

    if not os.path.exists(filepath):
        print(f"[INIT] Создаем локальный паспорт ноды: {filepath}")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(default_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[ERROR] Не удалось записать {filepath}: {e}")
        return default_data

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Ошибка чтения {filepath}: {e}. Используем дефолтные значения.")
        return default_data


def get_remote_commit_sha(repo_owner_repo: str, branch: str = "main") -> str:
    """Получает SHA последнего коммита через GitHub REST API без кэширования."""
    api_url = f"https://api.github.com/repos/{repo_owner_repo}/commits/{branch}?t={int(time.time())}"
    try:
        req = urllib.request.Request(api_url, headers={
            "User-Agent": "Python-Autoupdater",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache"
        })
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("sha", "")
    except Exception as e:
        print(f"[UPDATER WARN] Не удалось получить SHA коммита: {e}")
        return ""


def check_and_apply_update(repo_owner_repo: str, branch: str = "main") -> bool:
    """
    Проверяет целостность и SHA коммита.
    Автоматически скачивает архив, если файлы отсутствуют или вышло обновление.
    """
    # 1. Проверка физической целостности файлов на диске
    files_ok = verify_integrity(BASE_DIR)
    if not files_ok and os.path.exists(COMMIT_FILE):
        print("[UPDATER] Нарушена целостность структуры проекта! Сбрасываем кэш версии...")
        try:
            os.remove(COMMIT_FILE)
        except Exception as e:
            print(f"[UPDATER WARN] Не удалось удалить {COMMIT_FILE}: {e}")

    remote_sha = get_remote_commit_sha(repo_owner_repo, branch)

    # 2. Быстрая проверка по SHA (сработает ТОЛЬКО если файлы целы)
    if files_ok and remote_sha and os.path.exists(COMMIT_FILE):
        try:
            with open(COMMIT_FILE, "r", encoding="utf-8") as f:
                local_sha = f.read().strip()
            if local_sha == remote_sha:
                print("[UPDATER] Установлена последняя версия (SHA совпадает, структура цела).")
                return False
        except Exception as e:
            print(f"[UPDATER WARN] Ошибка чтения {COMMIT_FILE}: {e}")

    # 3. Формирование URL с обходом кэша и выкачивание архива
    timestamp = int(time.time())
    url = f"https://github.com/{repo_owner_repo}/archive/refs/heads/{branch}.zip?nocache={timestamp}"
    temp_zip = os.path.join(BASE_DIR, "temp_update.zip")
    extract_dir = os.path.join(BASE_DIR, "temp_extracted")

    print(f"[UPDATER] Скачивание свежего обновления из GitHub ({repo_owner_repo} [{branch}])...")

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Python-Autoupdater",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache"
        })
        with urllib.request.urlopen(req, timeout=15) as response, open(temp_zip, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception as e:
        print(f"[UPDATER] Не удалось скачать обновление: {e}")
        return False

    updated = False
    try:
        with zipfile.ZipFile(temp_zip, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        extracted_root = os.path.join(extract_dir, os.listdir(extract_dir)[0])

        for root, dirs, files in os.walk(extracted_root):
            rel_path = os.path.relpath(root, extracted_root)

            if any(part in IGNORE_PATTERNS for part in rel_path.split(os.sep)):
                continue

            target_dir = os.path.join(BASE_DIR, rel_path) if rel_path != "." else BASE_DIR
            os.makedirs(target_dir, exist_ok=True)

            for filename in files:
                if filename in IGNORE_PATTERNS:
                    continue

                src_file = os.path.join(root, filename)
                dst_file = os.path.join(target_dir, filename)

                should_copy = True
                if os.path.exists(dst_file):
                    try:
                        with open(src_file, "rb") as f1, open(dst_file, "rb") as f2:
                            if f1.read() == f2.read():
                                should_copy = False
                    except Exception:
                        should_copy = True

                if should_copy:
                    shutil.copy2(src_file, dst_file)
                    print(f"[UPDATER] Обновлен файл: {os.path.relpath(dst_file, BASE_DIR)}")
                    updated = True

        # Сохраняем SHA после успешной сборки
        if remote_sha:
            with open(COMMIT_FILE, "w", encoding="utf-8") as f:
                f.write(remote_sha)

    except Exception as e:
        print(f"[UPDATER] Ошибка при распаковке обновления: {e}")
    finally:
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except Exception:
                pass
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)

    return updated


def restart_process():
    """Безопасный перезапуск текущего процесса с поддержкой пробелов в путях."""
    print("[UPDATER] Перезапуск скрипта для применения обновлений...")
    python_exe = sys.executable
    script_path = os.path.abspath(sys.argv[0])

    args = [python_exe, script_path] + sys.argv[1:]

    subprocess.Popen(args)
    sys.exit(0)
