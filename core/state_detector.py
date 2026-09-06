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
        """ROI даёт координаты; файл — из template / templates / images."""
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

        # на всякий: ключ зоны без _zone
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

            # Вписать шаблон в размер ROI (по меньшей стороне)
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
            title = (win32gui.GetWindowText(hwnd) or "").lower()
        except Exception:
            return False
        game_title = (
            (self.config.get("game") or {}).get("window_title") or "RF"
        ).lower()
        launcher_title = (
            (self.config.get("launcher") or {}).get("window_title") or ""
        ).lower()
        if launcher_title and launcher_title in title:
            return False
        return game_title in title

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

        # Охота не найдена
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
        self._ocr_tick = 0
        self.ocr_every_n = 3
        self._last_good_stats: Dict[str, dict] = {}

    def _get_detector(self, hwnd: int) -> StateDetector:
        if hwnd not in self.detectors:
            self.detectors[hwnd] = StateDetector(
                game_name=self.game_name,
                config=self.game_cfg,
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

    def start_monitoring_loop(self, tracked_windows: list, interval_sec: int = 3):
        if not tracked_windows:
            print("[Мониторинг] Список окон пуст.")
            return

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
            f"OCR каждые {self.ocr_every_n} цикл(ов)"
        )

        try:
            while True:
                for idx, win in enumerate(tracked_windows):
                    hwnd = win.get("hwnd", 0)
                    display_name = win.get("display_name", f"Win_{idx}")
                    win["slot_index"] = idx

                    if hwnd and win32gui.IsWindow(hwnd):
                        detector = self._get_detector(hwnd)
                        current_state, _ = detector.detect_state(hwnd)
                        if current_state in (
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

                    if current_state == GameState.OFFLINE:
                        new_hwnd = self.relauncher.handle_offline_window(
                            win, self.game_cfg
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

                    self.dashboard.update_status(display_name, current_state)

                    self._ocr_tick += 1
                    if (
                        current_state
                        in (GameState.AUTO_HUNTING, GameState.IN_GAME_IDLE)
                        and self._ocr_tick >= self.ocr_every_n
                    ):
                        self._ocr_tick = 0
                        try:
                            from vision.ocr_reader import read_hud_stats
                            from vision.vision import capture_window_frame

                            frame = capture_window_frame(hwnd)
                            if frame is not None:
                                stats = read_hud_stats(
                                    frame,
                                    game_name=self.game_name,
                                    window_name=display_name,
                                    game_cfg=self.game_cfg,
                                )
                                stats = self._stabilize_stats(display_name, stats)
                                self.dashboard.update_stats(display_name, stats)
                        except Exception as e:
                            logger.error(f"[Stats] {display_name}: {e}")

                time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\n[Мониторинг] Остановлен.")