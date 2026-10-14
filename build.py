# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import subprocess

def main():
    print("[Builder] Старт сборки портативной версии CyberFarm...")

    # 1. Гарантированно проверяем и устанавливаем PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("[Builder] PyInstaller не найден. Устанавливаем...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 2. Создаем папку builder, если её нет
    root_path = os.path.dirname(os.path.abspath(__file__))
    builder_dir = os.path.join(root_path, "builder")
    os.makedirs(builder_dir, exist_ok=True)

    # 3. Автоматически генерируем правильный .spec файл прямо из кода
    spec_path = os.path.join(builder_dir, "cyber_farm.spec")
    spec_content = f"""# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
ROOT_PATH = {repr(root_path)}

hidden_imports = [
    'cv2', 'numpy', 'aiogram', 'win32gui', 'win32con', 'win32api', 'rapidocr_onnxruntime'
]
hidden_imports += collect_submodules('aiogram')
hidden_imports += collect_submodules('rapidocr_onnxruntime')

added_datas = []
games_path = os.path.join(ROOT_PATH, 'games')
if os.path.exists(games_path):
    added_datas.append((games_path, 'games'))

main_config_path = os.path.join(ROOT_PATH, 'main_config.json')
if os.path.exists(main_config_path):
    added_datas.append((main_config_path, '.'))

a = Analysis(
    [os.path.join(ROOT_PATH, 'Engine_Modul.py')],
    pathex=[ROOT_PATH],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Engine_Modul',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False, upx=True, console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='CyberFarm',
)
"""
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)
    print(f"[Builder] Спек-файл успешно подготовлен: {spec_path}")

    # 4. Запускаем компиляцию через программный вызов PyInstaller
    import PyInstaller.__main__
    print("[Builder] Компиляция проекта (это может занять пару минут)...")
    PyInstaller.__main__.run([spec_path])

    print("\n========================================================")
    print("[Builder] Готово! Портативная папка собрана в: dist\\CyberFarm")
    print("========================================================")

if __name__ == "__main__":
    main()
