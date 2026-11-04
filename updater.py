import filecmp
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from typing import List, Tuple

# --- Явная настройка корневого пути проекта ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMMIT_FILE = os.path.join(BASE_DIR, ".current_commit")        # какой коммит сейчас установлен
REJECTED_FILE = os.path.join(BASE_DIR, ".rejected_commit")     # коммит, отклонённый из-за ошибок в файлах
BACKUP_DIR = os.path.join(BASE_DIR, ".update_backup")          # файлы, заменённые последним обновлением

# Файлы и папки, которые КАТЕГОРИЧЕСКИ НЕЛЬЗЯ затирать при обновлении
IGNORE_PATTERNS = {
    "local_node.json",
    "local_node.json.bak",
    ".env",
    ".gitignore",
    "debug_crops",
    "logs",
    "cache",
    ".git",
    ".current_commit",
    ".rejected_commit",
    ".update_backup",
    "data",             # история нафармленного и состояние оповещений (не из репозитория)
    "error_log.txt",    # локальный журнал ошибок этой машины
}

# Ошибка синтаксиса в этих файлах отклоняет обновление. В остальных .py (tools/, tests/, build.py,
# старые скрипты) ошибка даёт только предупреждение: на работу фермы она не влияет.
VALIDATE_PY_ROOT = {"Engine_Modul.py", "updater.py", "watchdog.py"}
VALIDATE_PY_DIRS = {"core", "inputs", "services", "vision"}

# Обязательные системные файлы ядра
BASE_CRITICAL_FILES = [
    "Engine_Modul.py",
    "main_config.json",
]


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_text_atomic(path: str, text: str) -> None:
    """Запись через временный файл: при обрыве питания файл не останется обрезанным."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json_dict(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект { ... }")
    return data


def _fatal(message: str) -> None:
    """Остановить запуск с понятным сообщением (окно не закрывается сразу)."""
    print(f"\n[FATAL] {message}")
    if os.environ.get("CYBERFARM_NOPAUSE") != "1":
        print("\nДля продолжения нажмите любую клавишу . . .")
        os.system("pause > nul")
    sys.exit(1)


def _configured_branch() -> str:
    """Ветка обновлений для этой машины: поле update_branch в local_node.json (необязательное)."""
    try:
        cfg = _read_json_dict(os.path.join(BASE_DIR, "local_node.json"))
        return str(cfg.get("update_branch") or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Целостность проекта
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Паспорт ноды (local_node.json)
# ---------------------------------------------------------------------------

def load_or_create_node_config(default_data: dict, filepath: str = None) -> dict:
    """
    Загружает локальный паспорт ПК или создаёт его из дефолтных данных.

    Если файл есть, но не читается (например, опечатка при ручной правке):
      * берётся последняя рабочая копия local_node.json.bak;
      * если копии нет — запуск останавливается. Раньше машина молча подставляла дефолты
        и становилась «Rig-Main» и сервером.
    """
    if filepath is None:
        filepath = os.path.join(BASE_DIR, "local_node.json")
    elif not os.path.isabs(filepath):
        filepath = os.path.join(BASE_DIR, filepath)

    backup_path = filepath + ".bak"

    if not os.path.exists(filepath):
        print(f"[INIT] Создаем локальный паспорт ноды: {filepath}")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(default_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[ERROR] Не удалось записать {filepath}: {e}")
        return default_data

    try:
        cfg = _read_json_dict(filepath)
    except Exception as e:
        print(f"[ERROR] Ошибка чтения {filepath}: {e}")
        if os.path.exists(backup_path):
            try:
                cfg = _read_json_dict(backup_path)
                print("[INIT] Беру последнюю рабочую копию local_node.json.bak. "
                      "Исправь local_node.json, иначе она будет использоваться и дальше.")
                return cfg
            except Exception as e2:
                print(f"[ERROR] Резервная копия тоже не читается: {e2}")
        _fatal(
            "local_node.json повреждён, а рабочей копии нет. Запуск остановлен, чтобы машина "
            "не выдала себя за Rig-Main. Исправь файл (проверь запятые и кавычки) и запусти снова."
        )

    # Файл прочитан — обновляем резервную копию, если содержимое изменилось
    try:
        if not os.path.exists(backup_path) or not filecmp.cmp(filepath, backup_path, shallow=False):
            shutil.copy2(filepath, backup_path)
    except Exception as e:
        print(f"[UPDATER WARN] Не удалось обновить local_node.json.bak: {e}")
    return cfg


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# План обновления, проверка и применение
# ---------------------------------------------------------------------------

Plan = List[Tuple[str, str, str]]   # (файл в архиве, файл на диске, относительный путь)


def _build_plan(extracted_root: str) -> Plan:
    """Какие файлы из архива отличаются от установленных (с учётом защищённых путей)."""
    plan: Plan = []
    for root, dirs, files in os.walk(extracted_root):
        rel_dir = os.path.relpath(root, extracted_root)
        parts = [] if rel_dir == "." else rel_dir.split(os.sep)
        if any(part in IGNORE_PATTERNS for part in parts):
            continue
        for filename in files:
            if filename in IGNORE_PATTERNS:
                continue
            src = os.path.join(root, filename)
            rel = filename if rel_dir == "." else os.path.join(rel_dir, filename)
            dst = os.path.join(BASE_DIR, rel)
            try:
                if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
                    continue
            except Exception:
                pass
            plan.append((src, dst, rel))
    return plan


def _is_runtime_py(rel: str) -> bool:
    """Относится ли .py к рабочему коду фермы (ошибка в нём блокирует обновление)."""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) == 1:
        return parts[0] in VALIDATE_PY_ROOT
    return parts[0] in VALIDATE_PY_DIRS


def _validate_plan(plan: Plan) -> Tuple[List[str], List[str]]:
    """
    Проверяет новые файлы ДО применения: синтаксис .py и корректность .json.
    Возвращает (ошибки, предупреждения). Ошибка в рабочем коде или в любом .json отклоняет
    всё обновление, чтобы не разослать поломку на все машины. Ошибки в остальных .py
    (инструменты, тесты, старые скрипты) только предупреждают.
    """
    problems: List[str] = []
    warnings: List[str] = []
    for src, _dst, rel in plan:
        low = rel.lower()
        try:
            if low.endswith(".py"):
                with open(src, "rb") as f:
                    compile(f.read(), rel, "exec")
            elif low.endswith(".json"):
                with open(src, "r", encoding="utf-8") as f:
                    json.load(f)
        except SyntaxError as e:
            line = f"{rel}: синтаксическая ошибка (строка {e.lineno}): {e.msg}"
            (problems if _is_runtime_py(rel) else warnings).append(line)
        except json.JSONDecodeError as e:
            problems.append(f"{rel}: некорректный JSON ({e})")
        except Exception as e:
            warnings.append(f"{rel}: не удалось проверить ({e})")
    return problems, warnings


def _apply_plan(plan: Plan) -> bool:
    """
    Применяет обновление в три шага:
      1) резервная копия заменяемых файлов в .update_backup (для отката);
      2) подготовка новых файлов рядом с целью (*.new);
      3) быстрая замена. Файл никогда не остаётся записанным наполовину.
    """
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    created: List[str] = []
    for _src, dst, rel in plan:
        if os.path.exists(dst):
            backup_file = os.path.join(BACKUP_DIR, rel)
            os.makedirs(os.path.dirname(backup_file), exist_ok=True)
            shutil.copy2(dst, backup_file)
        else:
            created.append(rel)
    meta = {"from_commit": _read_text(COMMIT_FILE), "created": created,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_text_atomic(os.path.join(BACKUP_DIR, "_meta.json"), json.dumps(meta, ensure_ascii=False))

    staged: List[Tuple[str, str, str]] = []
    try:
        for src, dst, rel in plan:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".new"
            shutil.copy2(src, tmp)
            staged.append((tmp, dst, rel))
    except Exception:
        for tmp, _dst, _rel in staged:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise

    for tmp, dst, rel in staged:
        os.replace(tmp, dst)
        print(f"[UPDATER] Обновлен файл: {rel}")
    return bool(staged)


def check_and_apply_update(repo_owner_repo: str, branch: str = "main") -> bool:
    """
    Проверяет целостность и SHA коммита.
    Скачивает архив, если файлы отсутствуют или вышло обновление.
    Новые файлы проверяются до применения; при ошибке в них обновление отклоняется.
    Ветку можно переопределить для конкретной машины: "update_branch" в local_node.json.
    """
    override = _configured_branch()
    if override:
        branch = override
        print(f"[UPDATER] Для этой машины задана ветка обновлений: '{branch}'.")

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
        if _read_text(COMMIT_FILE) == remote_sha:
            print("[UPDATER] Установлена последняя версия (SHA совпадает, структура цела).")
            return False

    # Коммит уже отклонялся из-за ошибок в файлах — не скачиваем заново, ждём следующий
    if remote_sha and _read_text(REJECTED_FILE) == remote_sha:
        print(f"[UPDATER] Коммит {remote_sha[:7]} ранее отклонён из-за ошибок в файлах. Жду новый коммит.")
        return False

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
        shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(temp_zip, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        extracted_root = os.path.join(extract_dir, os.listdir(extract_dir)[0])

        plan = _build_plan(extracted_root)
        problems, warnings = _validate_plan(plan)
        for line in warnings:
            print(f"[UPDATER] Предупреждение (на работу не влияет): {line}")
        if problems:
            print("[UPDATER] ОБНОВЛЕНИЕ ОТКЛОНЕНО: в новых файлах найдены ошибки, текущая версия остаётся:")
            for line in problems:
                print(f"    - {line}")
            if remote_sha:
                _write_text_atomic(REJECTED_FILE, remote_sha)
            return False

        if plan:
            updated = _apply_plan(plan)

        # Сохраняем SHA после успешного применения
        if remote_sha:
            _write_text_atomic(COMMIT_FILE, remote_sha)
        if os.path.exists(REJECTED_FILE):
            try:
                os.remove(REJECTED_FILE)
            except OSError:
                pass

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


def rollback_last_update() -> bool:
    """
    Откатывает последнее обновление: возвращает файлы из .update_backup и удаляет файлы,
    которые обновление создало. Откатанный коммит запоминается как отклонённый, чтобы
    при следующем запуске он не установился снова (до выхода нового коммита).
    Запуск вручную:  python updater.py --rollback
    """
    meta_path = os.path.join(BACKUP_DIR, "_meta.json")
    if not os.path.exists(meta_path):
        print("[UPDATER] Нет резервной копии последнего обновления — откатывать нечего.")
        return False

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    restored = 0
    for root, _dirs, files in os.walk(BACKUP_DIR):
        for filename in files:
            if root == BACKUP_DIR and filename == "_meta.json":
                continue
            src = os.path.join(root, filename)
            rel = os.path.relpath(src, BACKUP_DIR)
            dst = os.path.join(BASE_DIR, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".new"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            restored += 1

    removed = 0
    for rel in meta.get("created", []):
        path = os.path.join(BASE_DIR, rel)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass

    bad_commit = _read_text(COMMIT_FILE)
    if bad_commit:
        _write_text_atomic(REJECTED_FILE, bad_commit)
    previous = meta.get("from_commit", "")
    if previous:
        _write_text_atomic(COMMIT_FILE, previous)
    elif os.path.exists(COMMIT_FILE):
        os.remove(COMMIT_FILE)

    print(f"[UPDATER] Откат выполнен: возвращено файлов {restored}, удалено новых {removed}.")
    return True


def restart_process():
    """Безопасный перезапуск текущего процесса с поддержкой пробелов в путях."""
    print("[UPDATER] Перезапуск скрипта для применения обновлений...")
    python_exe = sys.executable
    script_path = os.path.abspath(sys.argv[0])

    args = [python_exe, script_path] + sys.argv[1:]

    subprocess.Popen(args)
    sys.exit(0)


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        rollback_last_update()
    else:
        print("Использование: python updater.py --rollback   (откатить последнее обновление)")