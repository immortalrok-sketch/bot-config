"""
Удалённое управление фермой через Telegram (aiogram 3.x).
Только Main (watchdog). Watchdog :5001, Engine API :5000.
"""
import os
import sys
import re
import time
import asyncio
import threading
import cv2
import aiohttp
from typing import List, Dict, Any, Callable, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramConflictError

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from inputs.windows import get_game_windows
from vision.vision import capture_window_frame

try:
    from services.dashboard_app import apply_node_command
except ImportError:
    apply_node_command = None

WATCHDOG_PORT = 5001
ENGINE_PORT = 5000
HTTP_TIMEOUT = 2.5


class TelegramControlBot:
    def __init__(
        self,
        token: str,
        admin_chat_id: int,
        window_title: str = "RF",
        get_nodes_provider: Optional[Callable[[], Dict[str, Dict[str, Any]]]] = None,
    ):
        self.token = token
        self.admin_chat_id = int(admin_chat_id)
        self.window_title = window_title
        self.get_nodes_provider = get_nodes_provider or self._default_nodes_provider
        self.bot = Bot(token=self.token)
        self.dp = Dispatcher()
        self._thread = None
        self._loop = None
        self._seen_callbacks = set()
        self._nodes_cache = None
        self._nodes_cache_ts = 0.0
        self._nodes_cache_ttl = 8.0
        self._register_handlers()

    def _is_admin(self, user_id: int) -> bool:
        return user_id == self.admin_chat_id

    def _get_tracked_windows_safe(self) -> List[Any]:
        all_hwnds = []
        titles = self.window_title if isinstance(self.window_title, list) else [self.window_title]
        for title in titles:
            if title:
                all_hwnds.extend(get_game_windows(title) or [])
        return list(set(all_hwnds))

    def _default_nodes_provider(self) -> Dict[str, Dict[str, Any]]:
        hwnds = self._get_tracked_windows_safe()
        return {
            "Rig-Main": {
                "name": "Rig-Main",
                "is_local": True,
                "status": "online",
                "windows": hwnds,
                "ip": "127.0.0.1",
            }
        }

    def _get_all_nodes(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if self._nodes_cache and (now - self._nodes_cache_ts) < self._nodes_cache_ttl:
            return self._nodes_cache
        try:
            nodes = self.get_nodes_provider()
            result = nodes or self._default_nodes_provider()
        except Exception as e:
            print(f"[TelegramBot] nodes: {e}", flush=True)
            result = self._default_nodes_provider()
        self._nodes_cache = result
        self._nodes_cache_ts = now
        return result

    def _node_ip(self, node: dict) -> Optional[str]:
        if node.get("is_local"):
            return "127.0.0.1"
        return node.get("ip")

    async def _post_json(self, url: str, payload: dict = None) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload or {},
                    timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            print(f"[TelegramBot] POST {url}: {e}", flush=True)
            return False

    async def _get_ok(self, url: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            print(f"[TelegramBot] GET {url}: {e}", flush=True)
            return False

    async def _dispatch_engine(self, node: dict, action: str, instance=None) -> bool:
        """Всегда HTTP — TG в watchdog, не в Engine."""
        ip = self._node_ip(node)
        if not ip:
            return False
        return await self._post_json(
            f"http://{ip}:{ENGINE_PORT}/api/node_command",
            {"action": action, "instance": instance},
        )

    async def _dispatch_watchdog_start(self, node: dict) -> bool:
        ip = self._node_ip(node)
        if not ip:
            return False
        ok = await self._get_ok(f"http://{ip}:{WATCHDOG_PORT}/start_engine")
        if not ok:
            ok = await self._post_json(f"http://{ip}:{WATCHDOG_PORT}/start_engine", {})
        return ok

    def _sort_node_ids(self, nodes: dict) -> list:
        def key(pc_id: str):
            name = (nodes.get(pc_id, {}).get("name") or pc_id).lower()
            pid = (pc_id or "").lower()
            if "main" in name or pid == "rig-main":
                return (-1, 0, pc_id)
            nums = re.findall(r"\d+", pc_id)
            if nums:
                return (0, int(nums[0]), pc_id)
            return (1, 0, pc_id)

        return sorted(nodes.keys(), key=key)

    def _build_main_menu(self) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton(text="▶️ Старт ВСЕ", callback_data="global_start"),
                InlineKeyboardButton(text="⏸ Пауза ВСЕ", callback_data="global_pause"),
            ],
            [
                InlineKeyboardButton(text="🟢 Запустить ботов", callback_data="global_boot"),
                InlineKeyboardButton(text="⏹ Выключить ботов", callback_data="global_exit"),
            ],
            [
                InlineKeyboardButton(text="🗙 Закрыть окна ВСЕ", callback_data="global_close_wins"),
            ],
            [InlineKeyboardButton(text="--- Узлы фермы (ПК) ---", callback_data="ignore")],
        ]
        nodes = self._get_all_nodes()
        for pc_id in self._sort_node_ids(nodes):
            node = nodes[pc_id]
            st = node.get("status", "offline")
            icon = "🟢" if st in ("online", "active") else ("🟡" if st == "idle" else "🔴")
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{icon} {node.get('name', pc_id)}",
                    callback_data=f"pc_select:{pc_id}",
                )
            ])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    def _build_pc_menu(self, pc_id: str) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton(text="▶️ Старт", callback_data=f"node_cmd:{pc_id}:start"),
                InlineKeyboardButton(text="⏸ Пауза", callback_data=f"node_cmd:{pc_id}:pause"),
            ],
            [
                InlineKeyboardButton(text="🟢 Запустить бота", callback_data=f"node_boot:{pc_id}"),
                InlineKeyboardButton(text="⏹ Выключить бота", callback_data=f"node_cmd:{pc_id}:exit"),
            ],
            [
                InlineKeyboardButton(text="🗙 Закрыть окна", callback_data=f"node_cmd:{pc_id}:close_windows"),
            ],
        ]
        nodes = self._get_all_nodes()
        node = nodes.get(pc_id, {})
        pc_name = node.get("name", pc_id)
        wins = node.get("windows") or []

        items = []
        for x in wins:
            if isinstance(x, dict):
                items.append(
                    (str(x.get("display_name") or x.get("hwnd")),
                     str(x.get("display_name") or "win"))
                )
            else:
                items.append((str(x), str(x)))

        if items:
            keyboard.append([InlineKeyboardButton(
                text=f"--- Окна ({pc_name}) ---", callback_data="ignore"
            )])
            for wid, title in items:
                keyboard.append([InlineKeyboardButton(
                    text=f"🖥 {title}", callback_data=f"win_select:{pc_id}:{wid}"
                )])
        else:
            keyboard.append([InlineKeyboardButton(
                text="⚠️ Нет окон в реестре", callback_data="ignore"
            )])

        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    def _build_window_menu(self, pc_id: str, wid: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📸 Скриншот", callback_data=f"win_shot:{pc_id}:{wid}")],
            [
                InlineKeyboardButton(text="▶️ Старт окна", callback_data=f"win_act:{pc_id}:{wid}:start"),
                InlineKeyboardButton(text="⏸ Пауза окна", callback_data=f"win_act:{pc_id}:{wid}:pause"),
            ],
            [
                InlineKeyboardButton(text="🗙 Закрыть окно", callback_data=f"win_act:{pc_id}:{wid}:close_window"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"pc_select:{pc_id}")],
        ])

    def _build_confirm(self, action: str, scope: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚠️ ДА", callback_data=f"confirm:{action}:{scope}"),
            InlineKeyboardButton(text="❌ Нет", callback_data="back_to_main"),
        ]])

    def _register_handlers(self):
        @self.dp.callback_query(F.data == "global_close_wins")
        async def cb_gclose_wins(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            await callback.message.edit_text(
                "⚠️ **Закрыть игровые окна на ВСЕХ нодах?**\n"
                "(пауза + закрытие клиентов)",
                reply_markup=self._build_confirm("close_windows", "ALL"),
                parse_mode="Markdown",
            )
            await callback.answer()

        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            uid = message.from_user.id if message.from_user else None
            print(f"[TelegramBot] /start id={uid} admin={self.admin_chat_id}", flush=True)
            try:
                if not self._is_admin(uid):
                    print("[TelegramBot] REJECT not admin", flush=True)
                    return
                self._nodes_cache = None
                await message.answer(
                    "CyberFarm — управление\n"
                    "Старт/Пауза — автоматика\n"
                    "Запустить/Выключить — процесс бота",
                    reply_markup=self._build_main_menu(),
                )
                print("[TelegramBot] /start OK", flush=True)
            except Exception as e:
                print(f"[TelegramBot] /start ERROR: {e}", flush=True)
                import traceback
                traceback.print_exc()

        @self.dp.callback_query(F.data == "back_to_main")
        async def cb_back(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            await callback.message.edit_text(
                "🎮 **CyberFarm — управление**",
                reply_markup=self._build_main_menu(),
                parse_mode="Markdown",
            )
            await callback.answer()

        @self.dp.callback_query(F.data.startswith("pc_select:"))
        async def cb_pc(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            pc_id = callback.data.split(":", 1)[1]
            name = self._get_all_nodes().get(pc_id, {}).get("name", pc_id)
            await callback.message.edit_text(
                f"💻 **{name}**",
                reply_markup=self._build_pc_menu(pc_id),
                parse_mode="Markdown",
            )
            await callback.answer()

        @self.dp.callback_query(F.data.startswith("win_select:"))
        async def cb_win(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            parts = callback.data.split(":")
            if len(parts) < 3:
                return
            pc_id, wid = parts[1], parts[2]
            await callback.message.edit_text(
                f"🖥 `{wid}`",
                reply_markup=self._build_window_menu(pc_id, wid),
                parse_mode="Markdown",
            )
            await callback.answer()

        @self.dp.callback_query(F.data.startswith("win_shot:"))
        async def cb_shot(callback: types.CallbackQuery):
            await callback.answer()
            if not self._is_admin(callback.from_user.id):
                return
            if callback.id in self._seen_callbacks:
                return
            self._seen_callbacks.add(callback.id)
            if len(self._seen_callbacks) > 200:
                self._seen_callbacks.clear()

            parts = callback.data.split(":")
            if len(parts) < 3:
                return
            pc_id, target_win = parts[1], parts[2]
            node = self._get_all_nodes().get(pc_id, {})
            photo_bytes = None

            if node.get("is_local"):
                hwnd = None
                try:
                    from services.dashboard_app import _local_tracked
                    for w in _local_tracked or []:
                        if isinstance(w, dict) and w.get("display_name") == target_win:
                            hwnd = w.get("hwnd")
                            break
                except Exception:
                    pass
                if hwnd is None:
                    try:
                        hwnd = int(target_win)
                    except ValueError:
                        hwnd = None
                if hwnd is not None:
                    frame = capture_window_frame(hwnd)
                    if frame is not None:
                        ok, buf = cv2.imencode(".jpg", frame)
                        if ok:
                            photo_bytes = buf.tobytes()
            else:
                ip = self._node_ip(node)
                if ip:
                    url = f"http://{ip}:{ENGINE_PORT}/api/screenshot?instance={target_win}"
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                url, timeout=aiohttp.ClientTimeout(total=5)
                            ) as resp:
                                if resp.status == 200:
                                    photo_bytes = await resp.read()
                    except Exception as e:
                        print(f"[TG shot] {e}", flush=True)

            if not photo_bytes:
                await callback.message.answer(f"❌ Нет кадра `{pc_id}/{target_win}`")
                return
            await callback.message.answer_photo(
                BufferedInputFile(photo_bytes, filename=f"{pc_id}_{target_win}.jpg"),
                caption=f"📸 `{pc_id}` | `{target_win}`",
                parse_mode="Markdown",
            )

        @self.dp.callback_query(F.data.startswith("win_act:"))
        async def cb_win_act(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            _, pc_id, wid, action = callback.data.split(":", 3)
            node = self._get_all_nodes().get(pc_id, {})
            ok = await self._dispatch_engine(node, action, wid)
            if ok:
                await callback.answer("✅ " + action)
            else:
                await callback.answer("❌ " + action, show_alert=True)

        @self.dp.callback_query(F.data.startswith("node_cmd:"))
        async def cb_node_cmd(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            _, pc_id, action = callback.data.split(":")
            if action == "exit":
                await callback.message.edit_text(
                    f"⚠️ **Выключить бота** на `{pc_id}`?",
                    reply_markup=self._build_confirm("exit", pc_id),
                    parse_mode="Markdown",
                )
                await callback.answer()
                return
            node = self._get_all_nodes().get(pc_id, {})
            ok = await self._dispatch_engine(node, action, None)
            if ok:
                await callback.answer(f"✅ {action} → {pc_id}")
            else:
                await callback.answer(f"❌ {action} → {pc_id}", show_alert=True)

        @self.dp.callback_query(F.data.startswith("node_boot:"))
        async def cb_node_boot(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            pc_id = callback.data.split(":", 1)[1]
            node = self._get_all_nodes().get(pc_id, {})
            ok = await self._dispatch_watchdog_start(node)
            if ok:
                await callback.answer("✅ Watchdog start")
            else:
                await callback.answer("❌ Watchdog недоступен (порт 5001?)", show_alert=True)

        @self.dp.callback_query(F.data == "global_start")
        async def cb_gstart(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            nodes = list(self._get_all_nodes().values())
            results = await asyncio.gather(
                *[self._dispatch_engine(n, "start", None) for n in nodes],
                return_exceptions=True,
            )
            n = sum(1 for r in results if r is True)
            await callback.answer(f"▶️ Старт: {n}")

        @self.dp.callback_query(F.data == "global_pause")
        async def cb_gpause(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            nodes = list(self._get_all_nodes().values())
            results = await asyncio.gather(
                *[self._dispatch_engine(n, "pause", None) for n in nodes],
                return_exceptions=True,
            )
            n = sum(1 for r in results if r is True)
            await callback.answer(f"⏸ Пауза: {n}")

        @self.dp.callback_query(F.data == "global_boot")
        async def cb_gboot(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            nodes = list(self._get_all_nodes().values())
            results = await asyncio.gather(
                *[self._dispatch_watchdog_start(n) for n in nodes],
                return_exceptions=True,
            )
            n = sum(1 for r in results if r is True)
            await callback.answer(f"🟢 Запуск: {n} нод")

        @self.dp.callback_query(F.data == "global_exit")
        async def cb_gexit(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            await callback.message.edit_text(
                "⚠️ **Выключить ботов на ВСЕХ нодах?**\n(игры не закрываются)",
                reply_markup=self._build_confirm("exit", "ALL"),
                parse_mode="Markdown",
            )
            await callback.answer()

        @self.dp.callback_query(F.data.startswith("confirm:"))
        async def cb_confirm(callback: types.CallbackQuery):
            if not self._is_admin(callback.from_user.id):
                return
            _, action, scope = callback.data.split(":", 2)
            if scope == "ALL":
                nodes = list(self._get_all_nodes().values())
                results = await asyncio.gather(
                    *[self._dispatch_engine(n, action, None) for n in nodes],
                    return_exceptions=True,
                )
                n = sum(1 for r in results if r is True)
            else:
                node = self._get_all_nodes().get(scope, {})
                n = 1 if await self._dispatch_engine(node, action, None) else 0
            await callback.message.edit_text(
                f"⏹ Готово: {action} × {n}",
                reply_markup=self._build_main_menu(),
            )
            await callback.answer()

        @self.dp.callback_query(F.data == "ignore")
        async def cb_ignore(callback: types.CallbackQuery):
            await callback.answer()

    def _run_bot_loop(self):
        import logging
        logging.getLogger("aiogram").setLevel(logging.INFO)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _runner():
            await self.bot.delete_webhook(drop_pending_updates=True)
            print("[TelegramBot] webhook сброшен, polling...", flush=True)
            await self.dp.start_polling(self.bot, drop_pending_updates=True)

        try:
            self._loop.run_until_complete(_runner())
        except TelegramConflictError:
            print("[TelegramBot] Конфликт токена", flush=True)
        except Exception as e:
            print(f"[TelegramBot] polling ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()

    def start_in_background(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_bot_loop, daemon=True)
        self._thread.start()
        print("[TelegramBot] Запущен", flush=True)


def init_telegram_bot(token, admin_chat_id, window_title="RF", get_nodes_provider=None):
    bot = TelegramControlBot(token, admin_chat_id, window_title, get_nodes_provider)
    bot.start_in_background()
    return bot