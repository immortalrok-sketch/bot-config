# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Корневая папка проекта (на уровень выше папки builder/)
ROOT_PATH = os.path.abspath(os.path.join(SPECPATH, ".."))

# Автоматический сбор скрытых импортов для тяжелых библиотек
hidden_imports = [
    'cv2',
    'numpy',
    'aiogram',
    'win32gui',
    'win32con',
    'win32api',
    'rapidocr_onnxruntime',
]
hidden_imports += collect_submodules('aiogram')
hidden_imports += collect_submodules('rapidocr_onnxruntime')

# Собираем внешние файлы и папки, которые должны лежать рядом с exe
added_datas = []

# 1. Копируем всю папку games со всеми подпапками (картинки, конфиги игр, ROI)
games_path = os.path.join(ROOT_PATH, 'games')
if os.path.exists(games_path):
    added_datas.append((games_path, 'games'))

# 2. Копируем главный конфиг, если он есть в корне
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
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Engine_Modul',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CyberFarm',
)
