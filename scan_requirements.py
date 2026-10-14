"""
Собирает чистый список зависимостей проекта.

Как использовать:
  1. Положи этот файл в корень проекта (рядом с Engine_Modul.py).
  2. Рядом должен лежать requirements.txt (результат pip freeze).
  3. Запусти ТЕМ ЖЕ Python, которым запускаешь движок:  python scan_requirements.py
  4. Получишь requirements_engine.txt — только то, что реально импортируется в коде,
     с версиями из твоего freeze, и отчёт «какой пакет нужен какому файлу».
"""
import ast
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
FREEZE_FILE = os.path.join(ROOT, "requirements.txt")
OUT_FILE = os.path.join(ROOT, "requirements_engine.txt")

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".vs", "build", "dist", "logs", "debug_crops", "node_modules"}

# Запасная таблица «имя в import -> имя в pip», если пакет не установлен в этом Python.
FALLBACK_MAP = {
    "cv2": "opencv-python",
    "win32api": "pywin32", "win32con": "pywin32", "win32gui": "pywin32",
    "win32process": "pywin32", "win32ui": "pywin32", "win32file": "pywin32",
    "pythoncom": "pywin32", "pywintypes": "pywin32",
    "pynvml": "nvidia-ml-py", "wmi": "WMI", "yaml": "PyYAML", "PIL": "pillow",
    "skimage": "scikit-image", "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "dateutil": "python-dateutil", "bs4": "beautifulsoup4", "serial": "pyserial",
}


# Пакеты, которые в коде подключены «необязательно» (try/except), но на деле нужны всегда.
FORCE_REQUIRED = {
    "requests": "воркеры шлют статус на Main через requests; без него молча ничего не отправляется",
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_freeze(path: str) -> dict:
    """{нормализованное имя: строка 'Имя==версия'}"""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*(\S+)", line)
            if m:
                result[norm(m.group(1))] = f"{m.group(1)}=={m.group(2)}"
    return result


def local_top_names() -> set:
    """Имена своих модулей/папок проекта — их игнорируем."""
    names = set()
    for entry in os.listdir(ROOT):
        full = os.path.join(ROOT, entry)
        if os.path.isdir(full) and entry not in SKIP_DIRS:
            names.add(entry)
        elif entry.endswith(".py"):
            names.add(entry[:-3])
    # подпапки первого уровня тоже могут быть импортированы как модули (core.x -> x внутри пакета)
    for entry in list(names):
        full = os.path.join(ROOT, entry)
        if os.path.isdir(full):
            for sub in os.listdir(full):
                if sub.endswith(".py"):
                    names.add(sub[:-3])
    return names


class ImportCollector(ast.NodeVisitor):
    def __init__(self):
        self.found = []   # (top_name, in_try)
        self._try_depth = 0

    def visit_Try(self, node):
        self._try_depth += 1
        for child in node.body:
            self.visit(child)
        self._try_depth -= 1
        for part in (node.handlers, node.orelse, node.finalbody):
            for child in part:
                self.visit(child)

    def visit_Import(self, node):
        for alias in node.names:
            self.found.append((alias.name.split(".")[0], self._try_depth > 0))

    def visit_ImportFrom(self, node):
        if node.level == 0 and node.module:
            self.found.append((node.module.split(".")[0], self._try_depth > 0))


def main():
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    if not stdlib:
        print("[!] Нужен Python 3.10+ (sys.stdlib_module_names). Запусти тем Python, которым запускаешь движок.")
        return

    freeze = read_freeze(FREEZE_FILE)
    if not freeze:
        print(f"[!] Не найден или пуст {FREEZE_FILE}. Положи рядом результат pip freeze.")
        return

    try:
        from importlib.metadata import packages_distributions
        installed_map = packages_distributions()      # {import_name: [dist, ...]}
    except Exception:
        installed_map = {}

    local = local_top_names()
    usage = defaultdict(lambda: {"files": set(), "optional_only": True})   # import_name -> инфо

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py") or fn == os.path.basename(__file__):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    tree = ast.parse(f.read())
            except SyntaxError as e:
                print(f"[!] Пропускаю {rel}: синтаксическая ошибка ({e.msg}, строка {e.lineno})")
                continue
            collector = ImportCollector()
            collector.visit(tree)
            for name, in_try in collector.found:
                if name in stdlib or name in local or name == "__future__":
                    continue
                usage[name]["files"].add(rel)
                if not in_try:
                    usage[name]["optional_only"] = False

    # import-имя -> pip-пакеты
    dist_usage = defaultdict(lambda: {"imports": set(), "files": set(), "optional_only": True})
    unresolved = []
    for name, info in usage.items():
        dists = installed_map.get(name)
        if dists:
            # если один import даёт несколько pip-пакетов (например, opencv-python и -headless),
            # берём те, что есть в freeze
            in_freeze = [d for d in dists if norm(d) in freeze]
            targets = in_freeze or list(dists)
        elif name in FALLBACK_MAP:
            targets = [FALLBACK_MAP[name]]
        elif norm(name) in freeze:
            targets = [freeze[norm(name)].split("==")[0]]
        else:
            targets = None
        if not targets:
            unresolved.append((name, sorted(info["files"])))
            continue
        for d in targets:
            key = norm(d)
            dist_usage[key]["imports"].add(name)
            dist_usage[key]["files"].update(info["files"])
            if not info["optional_only"]:
                dist_usage[key]["optional_only"] = False
            dist_usage[key]["display"] = d

    for forced in FORCE_REQUIRED:
        if forced in dist_usage:
            dist_usage[forced]["optional_only"] = False

    lines_required, lines_optional, missing = [], [], []
    for key in sorted(dist_usage):
        info = dist_usage[key]
        pinned = freeze.get(key)
        if pinned is None:
            missing.append(info["display"])
            pinned = info["display"]          # без версии — пусть pip подберёт
        (lines_optional if info["optional_only"] else lines_required).append(pinned)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Сгенерировано scan_requirements.py — только то, что реально импортируется в коде проекта.\n")
        f.write("# Зависимости пакетов (aiohttp, pydantic и т.п.) pip подтянет сам.\n\n")
        f.write("# --- Обязательные ---\n")
        f.write("\n".join(lines_required) + "\n")
        if lines_optional:
            f.write("\n# --- Опциональные (импорт внутри try/except, без них код работает с ограничениями) ---\n")
            f.write("\n".join(lines_optional) + "\n")

    print("=" * 70)
    print(f"Готово: {OUT_FILE}")
    print("=" * 70)
    print(f"\nОбязательные ({len(lines_required)}) и опциональные ({len(lines_optional)}):\n")
    for key in sorted(dist_usage):
        info = dist_usage[key]
        tag = "опц." if info["optional_only"] else "ОБЯЗ"
        files = ", ".join(sorted(info["files"])[:4]) + (" ..." if len(info["files"]) > 4 else "")
        print(f"  [{tag}] {info['display']:<24} <- {files}")

    for forced, why in FORCE_REQUIRED.items():
        if forced in dist_usage:
            print(f"\n[i] {forced}: помечен обязательным принудительно — {why}.")

    in_freeze_unused = [v for k, v in freeze.items() if k not in dist_usage]
    print(f"\nВ freeze {len(freeze)} пакетов, проекту напрямую нужно {len(dist_usage)}.")
    print(f"Остальные ({len(in_freeze_unused)}) — это либо зависимости зависимостей (их подтянет pip),")
    print("либо пакеты от других проектов (питомец, эксперименты).")

    if missing:
        print("\n[!] Импортируются в коде, но НЕТ в freeze (записаны без версии):")
        for m in missing:
            print(f"      {m}")
    if unresolved:
        print("\n[?] Не удалось сопоставить с pip-пакетом (проверь вручную — опечатка, свой модуль или не установлен):")
        for name, files in unresolved:
            print(f"      {name:<22} <- {', '.join(files[:3])}")


if __name__ == "__main__":
    main()
    # чтобы окно не закрывалось сразу, если запустил двойным кликом
    try:
        input("\nНажми Enter, чтобы закрыть...")
    except EOFError:
        pass