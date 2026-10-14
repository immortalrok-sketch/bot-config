@echo off
chcp 65001 > nul

:: Жестко переходим в папку, где лежит этот батник (в корень проекта)
cd /d "%~dp0"

echo [Builder] Запуск мастера сборки...
python build.py

pause