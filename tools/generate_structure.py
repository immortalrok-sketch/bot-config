import os
import ast

EXCLUDE_DIRS = {'__pycache__', '.git', '.venv', 'venv', '.vs', 'logs', 'tests'}
EXCLUDE_FILES = {'generate_structure.py', 'organize.py'}
MODULES_FILE = "MODULES.txt"
ARCH_FILE = "ARCHITECTURE.md"


def build_file_map(root_dir):
    """Сканирует проект и создает карту путей: поддерживает как короткие имена, так и пути вида 'folder/module'."""
    file_map = {}
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel_path = os.path.relpath(root, root_dir)
        for f in files:
            if f.endswith('.py') and f not in EXCLUDE_FILES:
                name_no_ext = f[:-3]
                full_rel_path = name_no_ext if rel_path == "." else f"{rel_path}/{name_no_ext}"
                
                # Сохраняем полный путь (core/profile_loader) и короткое имя (profile_loader)
                file_map[full_rel_path] = full_rel_path
                file_map[name_no_ext] = full_rel_path
    return file_map


def get_file_info(file_path):
    """Возвращает количество строк кода (LOC) и первичное описание модуля."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.splitlines()
        loc = len([l for l in lines if l.strip() and not l.strip().startswith('#')])

        tree = ast.parse(content)
        doc = ast.get_docstring(tree)
        doc_first_line = doc.strip().split('\n')[0] if doc else "Описание отсутствует"
        return loc, doc_first_line
    except Exception:
        return 0, "Ошибка чтения"


def get_internal_imports(file_path, file_map):
    """Распознает внутренние импорты с учетом вложенных папок (например, from core.profile_loader import ...)."""
    dependencies = set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Преобразуем точки в слэши: core.profile_loader -> core/profile_loader
                    mod_path = alias.name.replace('.', '/')
                    mod_name = alias.name.split('.')[0]
                    
                    if mod_path in file_map:
                        dependencies.add(file_map[mod_path])
                    elif mod_name in file_map:
                        dependencies.add(file_map[mod_name])

            elif isinstance(node, ast.ImportFrom) and node.module:
                # Преобразуем точки в слэши: core.profile_loader -> core/profile_loader
                mod_path = node.module.replace('.', '/')
                mod_name = node.module.split('.')[-1]
                
                if mod_path in file_map:
                    dependencies.add(file_map[mod_path])
                elif mod_name in file_map:
                    dependencies.add(file_map[mod_name])
    except Exception:
        pass
    return dependencies


def build_system_maps(root_dir):
    file_map = build_file_map(root_dir)
    modules_output = ["=== АВТОМАТИЧЕСКАЯ КАРТА МОДУЛЕЙ СИСТЕМЫ (MODULES.TXT) ===\n"]
    mermaid_connections = set()
    all_nodes = set()

    total_files = 0
    total_loc = 0

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        py_files = [f for f in files if f.endswith('.py') and f not in EXCLUDE_FILES]

        if not py_files:
            continue

        rel_path = os.path.relpath(root, root_dir)
        folder_name = "Корневая папка" if rel_path == "." else f"Папка: {rel_path}/"
        modules_output.append(f"\n📁 {folder_name}")
        modules_output.append("-" * 50)

        for file in sorted(py_files):
            file_path = os.path.join(root, file)
            loc, doc = get_file_info(file_path)
            total_files += 1
            total_loc += loc

            modules_output.append(f"  📄 {file:<25} | LOC: {loc:<4} | {doc}")

            source_node = file.replace('.py', '') if rel_path == "." else f"{rel_path}/{file.replace('.py', '')}"
            imports = get_internal_imports(file_path, file_map)

            all_nodes.add(source_node)
            for imp in imports:
                if source_node != imp:
                    mermaid_connections.add((source_node, imp))
                    all_nodes.add(imp)

    modules_output.append("\n" + "=" * 50)
    modules_output.append(f"ИТОГО: Файлов: {total_files} | Всего строк кода (LOC): {total_loc}")

    with open(MODULES_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(modules_output))

    nodes_by_folder = {}
    for node in all_nodes:
        if "/" in node:
            folder, name = node.split("/", 1)
        else:
            folder, name = "root", node

        if folder not in nodes_by_folder:
            nodes_by_folder[folder] = []
        nodes_by_folder[folder].append(node)

    arch_content = [
        "# Динамическая архитектурная карта CyberFarm Engine\n",
        "```mermaid",
        "graph TB"
    ]

    for folder, nodes in sorted(nodes_by_folder.items()):
        folder_title = "Точка Входа" if folder == "root" else f"Слой {folder.upper()}"
        arch_content.append(f"    subgraph {folder} [\"{folder_title}\"]")
        for n in sorted(nodes):
            arch_content.append(f"        {n}")
        arch_content.append("    end\n")

    for src, dst in sorted(mermaid_connections):
        arch_content.append(f"    {src} --> {dst}")

    arch_content.extend([
        "```\n",
        "## Назначение слоев",
        "* **core/** — Управление конфигурациями, профилями и глобальным циклом.",
        "* **vision/** — Компьютерное зрение, детекция состояний и OCR.",
        "* **inputs/** — Эмуляция ввода и управление HWND окнами.",
        "* **services/** — Фоновые сервисы, веб-панель и восстановления.",
        "* **games/** — Конфиги и пресеты под конкретные игры."
    ])

    with open(ARCH_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(arch_content))

    print(f"[Успех] Структура пересобрана с группировкой по слоям!")


build_module_map = build_system_maps

if __name__ == "__main__":
    ROOT = os.path.dirname(os.path.abspath(__file__))
    build_system_maps(ROOT)