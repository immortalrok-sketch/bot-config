"""
Гибридный модуль распознавания состояния окон (Vision + FSM).
Слой CORE.
"""
import os
import sys
import time
import json
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

import cv2
import numpy as np
import win32gui

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.state_machine import GameState
from vision.vision import capture_window_frame
from services.debug_logger import logger

try:
    from inputs.windows import arrange_windows, place_window_in_slot
except ImportError:
    try:
        from windows import arrange_windows, place_window_in_slot
    except ImportError:
        from core.windows import arrange_windows, place_window_in_slot

try:
    from services.dashboard_app import NODE_CONTROL, set_local_tracked_windows
except ImportError:
    NODE_CONTROL = {
        "farm_running": True,
        "paused_instances": set(),
        "restart_requested": False,
    }

    def set_local_tracked_windows(windows):
        pass


class StateDetector:
    CONFIDENCE_THRESHOLD = 0.4
    IDLE_MISS_THRESHOLD = 3

    def __init__(
        self,
        config: Optional[dict] = None,
        game_name: str = "rf_next",
        debug_crops: bool = False,
    ):
        self.game_name = (game_name or "rf_next").lower()
        file_cfg = self._load_config() or {}
        self.config = {**file_cfg, **(config or {})}

        for req_key in ("state_rois", "templates", "ocr_rois", "images"):
            if req_key in file_cfg and req_key not in self.config:
                self.config[req_key] = file_cfg[req_key]

        self.current_state = GameState.LAUNCHER
        self.missed_hunting_count = 0
        self.debug_crops = debug_crops
        self._logged_rois = False

    def _load_config(self) -> dict:
        try:
            game_folder = self.game_name.lower()
            possible_roots = [
                Path(__file__).resolve().parent.parent,
                Path.cwd(),
                Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd(),
            ]
            for root in possible_roots:
                cfg_path = root / "games" / game_folder / "config.json"
                if cfg_path.exists():
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    logger.info(f"[StateDetector] Конфиг: {cfg_path}")
                    return data
            logger.error(f"[StateDetector] config.json не найден для '{game_folder}'")
            return {}
        except Exception as e:
            logger.error(f"[StateDetector] Ошибка чтения config: {e}")
            return {}

    def get_image_path(self, image_name: str) -> str:
        name = image_name if str(image_name).endswith(".png") else f"{image_name}.png"
        return os.path.join(ROOT_DIR, "games", self.game_name, "images", name)

    def _resolve_template_path(self, state_key: str, roi_info: dict) -> Optional[str]:
        name = roi_info.get("template")
        if name:
            path = self.get_image_path(name)
            if os.path.exists(path):
                return path

        templates = self.config.get("templates") or {}
        images = self.config.get("images") or {}
        key = (state_key or "").lower()
        candidates = []

        if "saving" in key:
            candidates.extend(
                [
                    templates.get("auto_hunting_saving"),
                    images.get("auto_hunting_saving"),
                    "auto_hunting_saving",
                ]
            )
        if "hunt" in key:
            candidates.extend(
                [
                    templates.get("auto_hunting"),
                    images.get("auto_hunting"),
                    "auto_hunting",
                ]
            )

        short = key.replace("_zone", "").replace("_saving", "")
        candidates.extend([templates.get(short), images.get(short), short])

        seen = set()
        for c in candidates:
            if not c or c in seen:
                continue
            seen.add(c)
            path = self.get_image_path(c)
            if os.path.exists(path):
                return path
        return None

    def _match_in_roi(self, frame: np.ndarray, template_path: str, roi_cfg: dict) -> bool:
        try:
            if not os.path.exists(template_path):
                logger.warning(f"[StateDetector] Нет шаблона: {template_path}")
                return False

            tpl = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if tpl is None:
                return False

            if len(frame.shape) == 3 and frame.shape[2] == 4:
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            else:
                frame_bgr = frame

            h, w = frame_bgr.shape[:2]
            x1 = int(roi_cfg["x_pct"] * w)
            y1 = int(roi_cfg["y_pct"] * h)
            x2 = int((roi_cfg["x_pct"] + roi_cfg["w_pct"]) * w)
            y2 = int((roi_cfg["y_pct"] + roi_cfg["h_pct"]) * h)
            crop = frame_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                return False

            if self.debug_crops:
                debug_dir = os.path.join(ROOT_DIR, "debug")
                os.makedirs(debug_dir, exist_ok=True)
                cv2.imwrite(
                    os.path.join(debug_dir, f"crop_{os.path.basename(template_path)}"),
                    crop,
                )

            ch, cw = crop.shape[:2]
            th, tw = tpl.shape[:2]

            if th > ch or tw > cw:
                scale = min(ch / float(th), cw / float(tw))
                new_w = max(1, int(tw * scale))
                new_h = max(1, int(th * scale))
                tpl = cv2.resize(tpl, (new_w, new_h), interpolation=cv2.INTER_AREA)
                th, tw = tpl.shape[:2]

            if ch < th or cw < tw:
                logger.debug(
                    f"[StateDetector] Кроп ({ch},{cw}) < шаблон ({th},{tw})"
                )
                return False

            res = cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED)
            max_val = float(np.max(res))
            matched = max_val >= self.CONFIDENCE_THRESHOLD
            logger.info(
                f"[STATE] {os.path.basename(template_path)} "
                f"score={max_val:.3f} thr={self.CONFIDENCE_THRESHOLD} ok={matched}"
            )
            return matched
        except Exception as e:
            logger.error(f"[StateDetector] match error ({template_path}): {e}")
            return False

    def _map_state_key(self, state_key: str) -> Optional[GameState]:
        key = (state_key or "").lower()
        if "hunt" in key:
            return GameState.AUTO_HUNTING
        if "launcher" in key:
            return GameState.LAUNCHER
        if "idle" in key or "in_game" in key:
            return GameState.IN_GAME_IDLE
        return None

    def _is_game_window(self, hwnd: int) -> bool:
        try:
            title = (win32gui.GetWindowText(hwnd) or "").lower().strip()
        except Exception:
            return False

        if not title:
            return False

        # 1. Извлекаем все возможные идентификаторы игры из конфига
        game_cfg = self.config.get("game") or {}
        game_title = (game_cfg.get("window_title") or "").lower().strip()
        game_prefix = (game_cfg.get("window_prefix") or "").lower().strip()
        game_name = (self.game_name or "").lower().strip()

        # 2. Игнорируем окно лаунчера, если оно совпало
        launcher_title = ((self.config.get("launcher") or {}).get("window_title") or "").lower().strip()
        if launcher_title and launcher_title in title:
            return False

        # 3. Гибкий детект: ищем совпадение хотя бы с одним из параметров
        match_title = bool(game_title and game_title in title)
        match_prefix = bool(game_prefix and game_prefix in title)
        match_name = bool(game_name and game_name in title)

        return match_title or match_prefix or match_name

    def _detect_via_vision(self, hwnd: int) -> Optional[GameState]:
        frame = capture_window_frame(hwnd)
        if frame is None or not self.config:
            return None

        logger.debug(f"[VISION] hwnd={hwnd} shape={getattr(frame, 'shape', None)}")

        state_rois = self.config.get("state_rois") or {}
        if not state_rois:
            if not self._logged_rois:
                logger.warning("[StateDetector] state_rois пустой")
                self._logged_rois = True
            return None

        if not self._logged_rois:
            logger.info(f"[StateDetector] state_rois keys: {list(state_rois.keys())}")
            self._logged_rois = True

        ordered = sorted(
            state_rois.items(),
            key=lambda kv: (
                0
                if "hunt" in kv[0].lower()
                else 1
                if "idle" in kv[0].lower()
                else 2
            ),
        )

        for state_key, roi_info in ordered:
            if not isinstance(roi_info, dict):
                continue
            if not all(k in roi_info for k in ("x_pct", "y_pct", "w_pct", "h_pct")):
                continue

            template_path = self._resolve_template_path(state_key, roi_info)
            if not template_path:
                logger.warning(f"[StateDetector] Нет шаблона для зоны '{state_key}'")
                continue

            if self._match_in_roi(frame, template_path, roi_info):
                mapped = self._map_state_key(state_key)
                if mapped is not None:
                    return mapped

        return None

    def detect_state(self, hwnd: int) -> Tuple[GameState, float]:
        timestamp = time.time()

        if not hwnd or not win32gui.IsWindow(hwnd):
            self.missed_hunting_count = 0
            self.current_state = GameState.OFFLINE
            return GameState.OFFLINE, timestamp

        vision_state = self._detect_via_vision(hwnd)

        if vision_state is not None:
            self.missed_hunting_count = 0
            if self.current_state != vision_state:
                logger.info(
                    f"[StateDetector] {self.current_state.value} -> {vision_state.value}"
                )
                self.current_state = vision_state
            return vision_state, timestamp

        if self._is_game_window(hwnd):
            if self.current_state == GameState.AUTO_HUNTING:
                self.missed_hunting_count += 1
                if self.missed_hunting_count >= self.IDLE_MISS_THRESHOLD:
                    self.current_state = GameState.IN_GAME_IDLE
                    logger.info(
                        f"[FSM] AUTO_HUNTING -> IN_GAME_IDLE "
                        f"(нет шаблона охоты {self.IDLE_MISS_THRESHOLD} кадр.)"
                    )
                    return GameState.IN_GAME_IDLE, timestamp
                return self.current_state, timestamp

            if self.current_state != GameState.IN_GAME_IDLE:
                logger.info(
                    f"[StateDetector] {self.current_state.value} -> IN_GAME_IDLE (окно игры)"
                )
                self.current_state = GameState.IN_GAME_IDLE
            return GameState.IN_GAME_IDLE, timestamp

        self.missed_hunting_count = 0
        return self.current_state, timestamp


def _is_maximized_or_minimized(hwnd: int) -> bool:
    """
    True, если окно развёрнуто на весь экран или свёрнуто — то есть пользователь
    сам управляет его размером. Такие окна подстраховка выравнивания не трогает:
    после возврата к обычному размеру Windows сама вернёт окно в прежнюю ячейку.
    """
    try:
        show_cmd = win32gui.GetWindowPlacement(hwnd)[1]
        # 2 = SW_SHOWMINIMIZED, 3 = SW_SHOWMAXIMIZED
        return show_cmd in (2, 3) or bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def load_perf_cfg() -> dict:
    """
    Настройки нагрузки на процессор (необязательные), local_node.json -> "performance":
        "ocr_interval_sec": 30,          # как часто читать статы каждого окна
        "ocr_debug_interval_sec": 0,     # как часто сохранять отладочные кропы OCR, сек (0 — выключено;
                                         # при настройке новой игры поставь 10–60)
        "priority": "below_normal",      # приоритет процесса бота ("normal" — не менять)
        "cpu_threads": 0,                # ограничить бота N логическими ядрами (0 — без ограничения)
        "tesseract_single_thread": true, # Tesseract в один поток (быстрее на мелких кропах)
        "report_sec": 60                 # как часто писать в лог строку [Perf] (0 — выключить)
    """
    try:
        with open(os.path.join(ROOT_DIR, "local_node.json"), "r", encoding="utf-8") as f:
            perf = (json.load(f) or {}).get("performance") or {}
        return perf if isinstance(perf, dict) else {}
    except Exception:
        return {}


def apply_process_limits(perf: dict) -> None:
    """Понижает приоритет процесса бота (игры важнее) и при желании ограничивает ядра."""
    try:
        import psutil
        proc = psutil.Process()
        if os.name == "nt" and str(perf.get("priority", "below_normal")).lower() == "below_normal":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            logger.info("[Perf] Приоритет процесса бота: ниже обычного (игры получают процессор первыми).")
        threads = int(perf.get("cpu_threads", 0) or 0)
        if threads > 0:
            cpus = proc.cpu_affinity()
            if threads < len(cpus):
                proc.cpu_affinity(cpus[-threads:])
                logger.info(f"[Perf] Бот ограничен {threads} логическими ядрами из {len(cpus)}.")
    except Exception as e:
        logger.warning(f"[Perf] Не удалось применить ограничения процесса: {e}")
    if perf.get("tesseract_single_thread", True):
        # Tesseract по умолчанию использует все ядра и на мелких кропах тратит время на потоки
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def is_window_hung(hwnd: int) -> bool:
    """True, если Windows считает окно зависшим (статус «Не отвечает»)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsHungAppWindow(hwnd))
    except Exception:
        return False


# Процессы, которые нельзя завершать ни при каких условиях (страховка от ошибки с HWND)
_KILL_DENYLIST = {
    "explorer.exe", "python.exe", "pythonw.exe", "py.exe", "svchost.exe", "dwm.exe",
    "winlogon.exe", "csrss.exe", "services.exe", "lsass.exe", "cmd.exe", "conhost.exe",
}


def kill_window_process(hwnd: int) -> bool:
    """Принудительно завершает процесс, которому принадлежит окно (вместе с дочерними)."""
    try:
        import psutil
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid or pid == os.getpid():
            return False
        proc = psutil.Process(pid)
        name = (proc.name() or "").lower()
        if name in _KILL_DENYLIST:
            logger.error(f"[Мониторинг] Отказ завершать '{name}' (PID {pid}) — в списке защищённых.")
            return False
        for child in proc.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        proc.kill()
        logger.warning(f"[Мониторинг] Завершён процесс '{name}' (PID {pid}).")
        return True
    except Exception as e:
        logger.error(f"[Мониторинг] Не удалось завершить процесс окна {hwnd}: {e}")
        return False


class WindowStateMonitor:
    def __init__(
        self,
        game_name: str = "rf_next",
        server_ip: str = "127.0.0.1",
        pc_name: str = "Rig-Main",
        game_cfg: dict = None,
    ):
        self.game_name = game_name
        self.game_cfg = game_cfg or {
            "game_name": game_name,
            "window_width": 960,
            "window_height": 540,
            "cols": 2,
            "rows": 2,
        }
        self.detectors: Dict[int, StateDetector] = {}

        from services.dashboard_app import DashboardBridge

        self.dashboard = DashboardBridge(server_ip=server_ip, pc_name=pc_name)

        from services.recovery_pipeline import GameRelauncher

        self.relauncher = GameRelauncher(max_attempts=3, cooldown_sec=20.0)

        self.last_states: Dict[str, Any] = {}
        self._ocr_tick: Dict[str, int] = {}
        self.ocr_every_n = 3
        self._cycle_count = 0
        self.realign_every_n = 3            # проверять положение окон раз в 3 цикла (~9 сек)
        self._realign_fixes: Dict[str, int] = {}
        self._hung_since: Dict[str, float] = {}   # display_name -> когда окно перестало отвечать
        self._hung_reported: set = set()          # окна, о зависании которых уже сообщили
        self.perf_cfg = load_perf_cfg()
        self.ocr_interval_sec = float(self.perf_cfg.get("ocr_interval_sec", 30))
        self.ocr_debug_interval_sec = float(self.perf_cfg.get("ocr_debug_interval_sec", 0))
        self._ocr_next: Dict[str, float] = {}         # когда окну пора читать статы
        self._ocr_debug_next: Dict[str, float] = {}   # когда окну пора сохранять отладочные кропы
        self._perf: Dict[str, list] = {"state": [], "ocr": [], "cpu": []}
        self._perf_last_report = time.time()
        self._perf_proc = None
        self._last_good_stats: Dict[str, dict] = {}
        self._farm_paused_logged = False

    def _get_detector(self, hwnd: int, game_name: Optional[str] = None) -> StateDetector:
        target_game = game_name or self.game_name
        if hwnd not in self.detectors:
            self.detectors[hwnd] = StateDetector(
                game_name=target_game,
                debug_crops=True,
            )
        return self.detectors[hwnd]

    def _stabilize_stats(self, display_name: str, stats: dict) -> dict:
        prev = self._last_good_stats.get(display_name, {})
        cleaned = dict(stats)

        for k in ("level", "combat_power", "diamonds"):
            new_v = cleaned.get(k, 0) or 0
            old_v = prev.get(k, 0) or 0
            if not isinstance(new_v, int):
                continue
            if old_v > 0 and new_v == 0:
                cleaned[k] = old_v
                continue
            if old_v > 0 and new_v > 0:
                old_len = len(str(old_v))
                new_len = len(str(new_v))
                if k == "combat_power" and old_len >= 4 and abs(old_len - new_len) >= 1:
                    cleaned[k] = old_v
                    continue
                if old_len - new_len >= 2:
                    cleaned[k] = old_v
                    continue

        good = dict(prev)
        for k in ("level", "combat_power", "diamonds", "exp_percent"):
            v = cleaned.get(k)
            if v not in (None, 0, 0.0):
                good[k] = v
        self._last_good_stats[display_name] = good
        return cleaned

    def _ocr_debug_due(self, display_name: str, now: float) -> bool:
        """Отладочные кропы/подробный вывод OCR — не каждый запуск, а раз в ocr_debug_interval_sec."""
        interval = self.ocr_debug_interval_sec
        if interval <= 0:
            return False
        if now >= self._ocr_debug_next.get(display_name, 0.0):
            self._ocr_debug_next[display_name] = now + interval
            return True
        return False

    def _perf_report(self) -> None:
        """Раз в report_sec пишет в лог, сколько процессора берёт бот и что именно тяжёлое."""
        report_sec = float(self.perf_cfg.get("report_sec", 60))
        if report_sec <= 0:
            return
        try:
            import psutil
            if self._perf_proc is None:
                self._perf_proc = psutil.Process()
            cpu = self._perf_proc.cpu_percent(interval=None) / max(1, psutil.cpu_count() or 1)
            self._perf["cpu"].append(cpu)
        except Exception:
            pass
        now = time.time()
        if now - self._perf_last_report < report_sec:
            return

        def stat(values: list) -> str:
            if not values:
                return "нет вызовов"
            ms = [v * 1000 for v in values]
            return f"{len(ms)} выз., ср. {sum(ms) / len(ms):.0f} мс, макс {max(ms):.0f} мс"

        cpu_vals = self._perf["cpu"]
        cpu_txt = (
            f"ср. {sum(cpu_vals) / len(cpu_vals):.0f}% / пик {max(cpu_vals):.0f}% (от всего процессора)"
            if cpu_vals else "нет данных"
        )
        logger.info(
            f"[Perf] За {int(now - self._perf_last_report)} с: CPU бота {cpu_txt} | "
            f"определение состояния: {stat(self._perf['state'])} | OCR статов: {stat(self._perf['ocr'])}"
        )
        for values in self._perf.values():
            values.clear()
        self._perf_last_report = now

    def _check_hung(self, win: dict, display_name: str, hwnd: int) -> bool:
        """
        Детект зависшего окна («Не отвечает»). Настройка в config.json игры:
            "hang_detection": {"enabled": true, "show_after_sec": 20, "not_responding_sec": 90}

        Windows помечает окно «не отвечает» уже через ~5 секунд, а у игры такое бывает на
        загрузках, переходах и разворачивании окна. Поэтому:
          * первые show_after_sec секунд ничего не происходит — состояние на дашборде не меняется;
          * дольше show_after_sec — True, окно показывается как DISCONNECTED;
          * дольше not_responding_sec — процесс завершается, а дальше срабатывает обычный
            recovery (окно пропадёт -> OFFLINE -> перезапуск через лаунчер).
        """
        cfg = (win.get("game_cfg") or {}).get("hang_detection") or {}
        if not cfg.get("enabled", True):
            self._hung_since.pop(display_name, None)
            self._hung_reported.discard(display_name)
            return False

        if not is_window_hung(hwnd):
            if display_name in self._hung_reported:
                logger.info(f"[Мониторинг] '{display_name}': окно снова отвечает.")
            self._hung_since.pop(display_name, None)
            self._hung_reported.discard(display_name)
            return False

        now = time.time()
        since = self._hung_since.setdefault(display_name, now)
        limit = float(cfg.get("not_responding_sec", 90))
        show_after = min(float(cfg.get("show_after_sec", 20)), limit)
        elapsed = now - since

        if elapsed < show_after:
            return False                      # короткое подвисание — не трогаем и не шумим

        if display_name not in self._hung_reported:
            self._hung_reported.add(display_name)
            logger.warning(
                f"[Мониторинг] '{display_name}': окно не отвечает уже {int(elapsed)} сек. "
                f"Если так будет {int(limit)} сек — перезапуск."
            )
        if elapsed < limit:
            return True

        logger.error(
            f"[Мониторинг] '{display_name}': не отвечает {int(elapsed)} сек — завершаю процесс."
        )
        kill_window_process(hwnd)
        self._hung_since.pop(display_name, None)
        self._hung_reported.discard(display_name)
        return True

    def start_monitoring_loop(self, tracked_windows: list, interval_sec: int = 3):
        if not tracked_windows:
            print("[Мониторинг] Список окон пуст.")
            return

        # реестр окон для /api/screenshot и Telegram
        try:
            set_local_tracked_windows(tracked_windows)
        except Exception:
            pass

        cols = self.game_cfg.get("cols", 2)
        rows = self.game_cfg.get("rows", 2)

        active_hwnds = [
            win["hwnd"]
            for win in tracked_windows
            if win.get("hwnd") and win32gui.IsWindow(win["hwnd"])
        ]
        if active_hwnds:
            logger.info(f"[Мониторинг] Выравнивание {len(active_hwnds)} окон...")
            arrange_windows(active_hwnds, cols=cols, rows=rows)

        print(
            f"\n[Мониторинг] {len(tracked_windows)} окон, интервал {interval_sec}с, "
            f"OCR статов каждые {int(self.ocr_interval_sec)} сек на окно"
        )
        apply_process_limits(self.perf_cfg)

        try:
            while True:
                self._cycle_count += 1
                # --- Telegram / REST: пауза всей ноды ---
                farm_running = True
                paused = set()
                try:
                    farm_running = bool(NODE_CONTROL.get("farm_running", True))
                    paused = set(NODE_CONTROL.get("paused_instances") or set())
                except Exception:
                    pass

                if not farm_running:
                    if not self._farm_paused_logged:
                        logger.info("[Мониторинг] Ферма на ПАУЗЕ (NODE_CONTROL)")
                        self._farm_paused_logged = True
                    time.sleep(interval_sec)
                    continue
                if self._farm_paused_logged:
                    logger.info("[Мониторинг] Ферма СНЯТА с паузы")
                    self._farm_paused_logged = False

                for idx, win in enumerate(tracked_windows):
                    hwnd = win.get("hwnd", 0)
                    display_name = win.get("display_name", f"Win_{idx}")
                    win["slot_index"] = idx

                    # --- пауза одного окна ---
                    if display_name in paused:
                        continue

                    if hwnd and win32gui.IsWindow(hwnd):
                        win_game_name = win.get("game_name") or self.game_name
                        detector = self._get_detector(hwnd, game_name=win_game_name)
                        _t0 = time.perf_counter()
                        current_state, _ = detector.detect_state(hwnd)
                        self._perf["state"].append(time.perf_counter() - _t0)
                        if self._check_hung(win, display_name, hwnd):
                            # зависшее окно показываем красным и не сбрасываем счётчик попыток
                            current_state = GameState.DISCONNECTED
                        elif current_state in (
                            GameState.AUTO_HUNTING,
                            GameState.IN_GAME_IDLE,
                        ):
                            self.relauncher.reset_attempts(display_name)
                    else:
                        current_state = GameState.OFFLINE
                        if hwnd in self.detectors:
                            del self.detectors[hwnd]

                    prev_state = self.last_states.get(display_name)
                    if prev_state != current_state:
                        prev_val = prev_state.value if prev_state else "START"
                        logger.info(
                            f"[Мониторинг] '{display_name}': "
                            f"{prev_val} -> {current_state.value}"
                        )
                        self.last_states[display_name] = current_state

                    # recovery только если ферма не на паузе (уже выше)
                    if current_state == GameState.OFFLINE:
                        busy_hwnds = {
                            w.get("hwnd") for w in tracked_windows
                            if w is not win and w.get("hwnd")
                        }
                        new_hwnd = self.relauncher.handle_offline_window(
                            win, self.game_cfg, exclude_hwnds=busy_hwnds
                        )
                        if new_hwnd:
                            win["hwnd"] = new_hwnd
                            place_window_in_slot(
                                hwnd=new_hwnd,
                                slot_index=idx,
                                cols=cols,
                                rows=rows,
                            )
                            logger.info(
                                f"[Мониторинг] '{display_name}' перезапуск → слот #{idx}"
                            )
                            try:
                                set_local_tracked_windows(tracked_windows)
                            except Exception:
                                pass

                    # --- Подстраховка выравнивания: игра может сама сдвинуть окно после дозагрузки ---
                    live_hwnd = win.get("hwnd", 0)
                    if (
                        live_hwnd
                        and win32gui.IsWindow(live_hwnd)
                        and current_state != GameState.OFFLINE
                        and not _is_maximized_or_minimized(live_hwnd)
                    ):
                        fixes = self._realign_fixes.get(display_name, 0)
                        # если окно упорно не держится на месте — не дёргаем его каждые 9 сек
                        every = self.realign_every_n if fixes < 5 else self.realign_every_n * 10
                        if self._cycle_count % every == 0:
                            if place_window_in_slot(
                                hwnd=live_hwnd, slot_index=idx, cols=cols, rows=rows
                            ):
                                self._realign_fixes[display_name] = fixes + 1
                                logger.info(
                                    f"[Мониторинг] '{display_name}': окно съехало — "
                                    f"возвращено в слот #{idx}"
                                )
                            else:
                                self._realign_fixes[display_name] = 0

                    self.dashboard.update_status(display_name, current_state)

                    # OCR статов — по времени для каждого окна, а не каждый цикл. Окна разнесены
                    # по времени, чтобы они не читались одновременно (иначе пики нагрузки).
                    ocr_now = time.time()
                    due_at = self._ocr_next.get(display_name)
                    if due_at is None:
                        due_at = ocr_now + idx * self.ocr_interval_sec / max(1, len(tracked_windows))
                        self._ocr_next[display_name] = due_at
                    if (
                        current_state
                        in (GameState.AUTO_HUNTING, GameState.IN_GAME_IDLE)
                        and ocr_now >= due_at
                    ):
                        self._ocr_next[display_name] = ocr_now + self.ocr_interval_sec
                        try:
                            import vision.ocr_reader as ocr_module
                            from vision.ocr_reader import read_hud_stats

                            # отладочные кропы и подробный вывод — не каждый запуск, а по расписанию
                            reader = getattr(ocr_module, "_hud_reader_instance", None)
                            if reader is not None:
                                reader.debug_mode = self._ocr_debug_due(display_name, ocr_now)

                            _t0 = time.perf_counter()
                            frame = capture_window_frame(hwnd)
                            if frame is not None:
                                stats = read_hud_stats(
                                    frame,
                                    game_name=win_game_name,
                                    window_name=display_name,
                                    game_cfg=self.game_cfg,
                                )
                                stats = self._stabilize_stats(display_name, stats)
                                self.dashboard.update_stats(display_name, stats)
                            self._perf["ocr"].append(time.perf_counter() - _t0)
                        except Exception as e:
                            logger.error(f"[Stats] {display_name}: {e}")

                self._perf_report()
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\n[Мониторинг] Остановлен.")