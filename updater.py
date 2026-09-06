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
    Проверяет SHA коммита и скачивает zip с обходом CDN-кэша GitHub.
    Возвращает True, если были применены обновления (требуется рестарт).
    """
    remote_sha = get_remote_commit_sha(repo_owner_repo, branch)

    # 1. Быстрая проверка по локальному SHA
    if remote_sha and os.path.exists(COMMIT_FILE):
        try:
            with open(COMMIT_FILE, "r", encoding="utf-8") as f:
                local_sha = f.read().strip()
            if local_sha == remote_sha:
                print("[UPDATER] Установлена последняя версия (SHA совпадает).")
                return False
        except Exception as e:
            print(f"[UPDATER WARN] Ошибка чтения {COMMIT_FILE}: {e}")

    # 2. Формирование URL с Cache-Buster
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

            # Пропускаем игнорируемые директории
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
                    print(f"[UPDATER] Обновлен файл: {os.path.basename(dst_file)}")
                    updated = True

        # Сохраняем актуальный SHA после успешного применения
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

    # Формируем список аргументов для Popen
    args = [python_exe, script_path] + sys.argv[1:]

    subprocess.Popen(args)
    sys.exit(0)
