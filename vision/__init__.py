"""
Пакет компьютерного зрения (Vision).
Инициализация публичного API модуля зрения для взаимодействия с движком.
"""
from .vision import (
    capture_window_frame,
    capture_game_frame,
    find_template_in_frame,
    find_template_in_window,
)

__all__ = [
    "capture_window_frame",
    "capture_game_frame",
    "find_template_in_frame",
    "find_template_in_window",
]
