import os
import sys
import shutil
import zipfile
import urllib.request
import json
from typing import List

# Файлы и папки, которые КАТЕГОРИЧЕСКИ НЕЛЬЗЯ затирать при обновлении
IGNORE_PATTERNS = {
    "local_node.json",
    ".env",
    ".gitignore",
    "debug_crops",
    "logs",
    "cache",
    ".git"
}

def load_or_create_node_config(default_data: dict, filepath: str = "local_node.json") -> dict:
    """Загружает локальный паспорт ПК или создает его из дефолтных данных."""
    if not os.path.exists(filepath):
        print(f"[INIT] Создаем локальный паспорт ноды: {filepath}")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Ошибка чтения {filepath}: {e}. Используем дефолтные значения.")
        return default_data


def check_and_apply_update(repo_owner_repo: str, branch: str = "main") -> bool:
    """
    Скачивает zip с GitHub и обновляет проект, пропуская локальные конфиги.
    Возвращает True, если были применены обновления (требуется рестарт).
    """
    url = f"https://github.com/{repo_owner_repo}/archive/refs/heads/{branch}.zip"
    temp_zip = "temp_update.zip"
    extract_dir = "temp_extracted"

    print(f"[UPDATER] Проверка обновлений из GitHub ({repo_owner_repo} [{branch}])...")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Python-Autoupdater"})
        with urllib.request.urlopen(req, timeout=10) as response, open(temp_zip, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception as e:
        print(f"[UPDATER] Не удалось скачать обновление: {e}")
        return False

    updated = False
    try:
        with zipfile.ZipFile(temp_zip, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        # Распакованная папка обычно имеет вид RepoName-main
        extracted_root = os.path.join(extract_dir, os.listdir(extract_dir)[0])

        for root, dirs, files in os.walk(extracted_root):
            rel_path = os.path.relpath(root, extracted_root)
            
            # Пропускаем игнорируемые директории
            if any(part in IGNORE_PATTERNS for part in rel_path.split(os.sep)):
                continue

            target_dir = os.path.join(".", rel_path) if rel_path != "." else "."
            os.makedirs(target_dir, exist_ok=True)

            for file in files:
                if file in IGNORE_PATTERNS:
                    continue

                src_file = os.path.join(root, file)
                dst_file = os.path.join(target_dir, file)

                # Простая проверка необходимости копирования
                should_copy = True
                if os.path.exists(dst_file):
                    with open(src_file, "rb") as f1, open(dst_file, "rb") as f2:
                        if f1.read() == f2.read():
                            should_copy = False

                if should_copy:
                    shutil.copy2(src_file, dst_file)
                    print(f"[UPDATER] Обновлен файл: {dst_file}")
                    updated = True

    except Exception as e:
        print(f"[UPDATER] Ошибка при распаковке обновления: {e}")
    finally:
        # Очистка временных файлов
        if os.path.exists(temp_zip):
            os.remove(temp_zip)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)

    return updated


def restart_process():
    """Бесшовный перезапуск текущего Python процесса."""
    print("[UPDATER] Перезапуск скрипта для применения обновлений...")
    os.execv(sys.executable, [sys.executable] + sys.argv)