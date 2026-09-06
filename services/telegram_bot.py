"""
Модуль удаленного управления фермой через Telegram Bot (aiogram 3.x) с иерархическим меню.
Слой SERVICES.
"""
import os
import sys
import io
import asyncio
import threading
import cv2
from typing import List, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramConflictError

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from inputs.windows import get_game_windows
from vision.vision import capture_window_frame

# Глобальный флаг состояния всей фермы
IS_FARM_RUNNING = True


class TelegramControlBot:
    """Сервис фонового Telegram-бота с иерархическим инлайн-меню и проверкой whitelist по admin_chat_id."""

    def __init__(self, token: str, admin_chat_id: int, window_title: str = "RF"):
        self.token = token
        self.admin_chat_id = int(admin_chat_id)
        self.window_title = window_title

        self.bot = Bot(token=self.token)
        self.dp = Dispatcher()
        self._thread = None
        self._loop = None

        self._register_handlers()

    def _is_admin(self, user_id: int) -> bool:
        """Проверка безопасности: доступ только для владельца фермы."""
        return user_id == self.admin_chat_id

    def _get_tracked_windows_safe(self) -> List[Any]:
        """Интерфейс для получения списка актуальных окон из системы."""
        return get_game_windows(self.window_title)

    def _build_main_menu(self) -> InlineKeyboardMarkup:
        """Генерация главного меню (Глобальные команды + список компьютеров/нод)."""
        keyboard = [
            [
                InlineKeyboardButton(text="🔷 Старт ВСЕ", callback_data="global_start"),
                InlineKeyboardButton(text="🔹 Стоп ВСЕ", callback_data="global_stop")
            ],
            [
                InlineKeyboardButton(text="🔄 Рестарт ВСЕ", callback_data="global_restart"),
                InlineKeyboardButton(text="🌐 Закрыть ВСЕ", callback_data="global_close")
            ]
        ]

        # Уровень структуры: Список ПК / Нод фермы (на текущем ПК базово выводим Компьютер #1)
        # В дальнейшем при масштабировании здесь будет список всех доступных нод
        keyboard.append([InlineKeyboardButton(text="--- Узлы фермы (ПК) ---", callback_data="ignore")])
        
        # Проверяем, есть ли вообще запущенные окна, чтобы отобразить узел ПК активным
        hwnds = self._get_tracked_windows_safe()
        status_icon = "🟢" if hwnds else "⚪"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status_icon} Компьютер #1 (Локальный)", 
                callback_data="pc_select_1"
            )
        ])

        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    def _build_pc_menu(self) -> InlineKeyboardMarkup:
        """Меню конкретного компьютера: отображает список его активных окон."""
        keyboard = []
        hwnds = self._get_tracked_windows_safe()
        
        if hwnds:
            keyboard.append([InlineKeyboardButton(text="--- Активные окна на ПК #1 ---", callback_data="ignore")])
            for idx, hwnd in enumerate(hwnds, start=1):
                btn_text = f"🖥️ Окно #{idx} (HWND: {hwnd})"
                keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"win_select_{hwnd}")])
        else:
            keyboard.append([InlineKeyboardButton(text="⚠️ На этом ПК нет окон", callback_data="ignore")])

        keyboard.append([
            InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="back_to_main")
        ])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    def _build_window_menu(self, hwnd: str) -> InlineKeyboardMarkup:
        """Меню управления конкретным окном/инстансом."""
        keyboard = [
            [
                InlineKeyboardButton(text="📸 Скриншот", callback_data=f"win_shot_{hwnd}"),
            ],
            [
                InlineKeyboardButton(text="🟢 Запустить окно", callback_data=f"win_start_{hwnd}"),
                InlineKeyboardButton(text="🔹 Остановить окно", callback_data=f"win_stop_{hwnd}")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к списку окон", callback_data="pc_select_1")
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    def _register_handlers(self):
        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            if not self._is_admin(message.from_user.id):
                return
            await message.answer(
                "🎮 **Панель управления CyberFarm**\nВыберите действие или перейдите к узлу компьютера:",
                reply_markup=self._build_main_menu(),
                parse_mode="Markdown"
            )

        @self.dp.callback_query(F.data == "back_to_main")
        async def cb_back_to_main(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                await callback.answer("Доступ запрещен", show_alert=True)
                return
            
            await callback.message.edit_text(
                "🎮 **Панель управления CyberFarm**\nВыберите действие или перейдите к узлу компьютера:",
                reply_markup=self._build_main_menu(),
                parse_mode="Markdown"
            )
            await callback.answer()

        @self.dp.callback_query(F.data == "pc_select_1")
        async def cb_select_pc(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            await callback.message.edit_text(
                "💻 **Компьютер #1 (Локальный)**\nВыберите нужное игровое окно для контроля:",
                reply_markup=self._build_pc_menu(),
                parse_mode="Markdown"
            )
            await callback.answer()

        @self.dp.callback_query(F.data == "global_start")
        async def cb_global_start(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            global IS_FARM_RUNNING
            IS_FARM_RUNNING = True
            await callback.answer("🔷 Вся ферма запущена!")
            await callback.message.edit_text(
                "🔷 **Ферма ЗАПУЩЕНА** (Глобально).\nВсе модули автоматизации активны.",
                reply_markup=self._build_main_menu(),
                parse_mode="Markdown"
            )

        @self.dp.callback_query(F.data == "global_stop")
        async def cb_global_stop(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            global IS_FARM_RUNNING
            IS_FARM_RUNNING = False
            await callback.answer("🔹 Вся ферма остановлена!")
            await callback.message.edit_text(
                "🔹 **Ферма ОСТАНОВЛЕНА** (Глобально).\nАвтоматизация на паузе.",
                reply_markup=self._build_main_menu(),
                parse_mode="Markdown"
            )

        @self.dp.callback_query(F.data.in_({"global_restart", "global_close"}))
        async def cb_global_stub(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            action = "рестарт" if "restart" in callback.data else "закрытие"
            await callback.answer(f"Команда '{action}' принята в разработку.", show_alert=True)

        @self.dp.callback_query(F.data.startswith("win_select_"))
        async def cb_select_window(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            hwnd = callback.data.replace("win_select_", "")
            await callback.message.edit_text(
                f"🖥️ **Управление окном (HWND: `{hwnd}`)**\nВыберите нужную команду:",
                reply_markup=self._build_window_menu(hwnd),
                parse_mode="Markdown"
            )
            await callback.answer()

        @self.dp.callback_query(F.data.startswith("win_shot_"))
        async def cb_window_screenshot(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            
            hwnd_str = callback.data.replace("win_shot_", "")
            try:
                hwnd = int(hwnd_str)
            except ValueError:
                hwnd = hwnd_str

            await callback.answer("📸 Делаем скриншот окна...")

            # Захватываем кадр конкретного окна по HWND
            frame = capture_window_frame(hwnd)
            if frame is None:
                await callback.message.answer(f"❌ Не удалось захватить окно HWND: `{hwnd}`")
                return

            is_success, buffer = cv2.imencode(".jpg", frame)
            if not is_success:
                await callback.message.answer("❌ Ошибка кодирования изображения.")
                return

            photo_bytes = io.BytesIO(buffer).getvalue()
            input_file = BufferedInputFile(photo_bytes, filename=f"window_{hwnd}.jpg")

            await callback.message.answer_photo(
                photo=input_file,
                caption=f"📸 Скриншот окна | HWND: `{hwnd}`"
            )

        @self.dp.callback_query(F.data == "ignore")
        async def cb_ignore(callback: types.CallbackQuery):
            await callback.answer()

    def _run_bot_loop(self):
        """Асинхронный цикл опроса Telegram в отдельном потоке с заглушкой внутренних ошибок aiogram."""
        import logging
        
        # Полностью глушим внутренний спам aiogram (включая сообщения о повторных попытках getUpdates)
        logging.getLogger("aiogram").setLevel(logging.CRITICAL)
        logging.getLogger("aiogram.event").setLevel(logging.CRITICAL)

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self.dp.start_polling(self.bot))
        except TelegramConflictError:
            print("[TelegramBot] Конфликт токена: Бот уже запущен на другом ПК. Поллинг остановлен.", flush=True)
        except Exception as e:
            print(f"[TelegramBot] Ошибка в цикле бота: {e}", flush=True)

    def start_in_background(self):
        """Запуск бота в фоновом режиме-демоне."""
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(target=self._run_bot_loop, daemon=True)
        self._thread.start()
        print("[TelegramBot] Сервис иерархического бота успешно запущен в фоновом потоке.", flush=True)


def init_telegram_bot(token: str, admin_chat_id: int, window_title: str = "RF") -> TelegramControlBot:
    """Безопасная инициализация бота."""
    bot_service = TelegramControlBot(token=token, admin_chat_id=admin_chat_id, window_title=window_title)
    bot_service.start_in_background()
    return bot_service