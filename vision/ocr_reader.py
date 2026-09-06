"""
Универсальный OCR-распознаватель HUD.
RapidOCR + Tesseract fallback для diamonds/combat_power.
Слой VISION.
"""
import os
import sys
import re
import json
import cv2
import numpy as np
from collections import Counter
from typing import Optional, Dict, Any, Union, Tuple, List
from rapidocr_onnxruntime import RapidOCR

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from vision.vision import capture_game_frame, crop_right_of_template
except ImportError:
    from vision.vision import capture_game_frame
    crop_right_of_template = None

# 1. Безопасный импорт pytesseract
try:
    import pytesseract
    _HAS_TESSERACT = True
except Exception:
    _HAS_TESSERACT = False

# 2. Авто-настройка пути к Tesseract для Windows
TESS_DEFAULT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if _HAS_TESSERACT and os.path.exists(TESS_DEFAULT_PATH):
    pytesseract.pytesseract.tesseract_cmd = TESS_DEFAULT_PATH

TESS_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789"

MAX_LIMITS = {
    "level": 200,
    "exp_percent": 100.0,
    "combat_power": 999_999,
    "diamonds": 20_000,
}

DIGIT_LEN = {
    "level": (1, 3),
    "combat_power": (5, 6),
    "diamonds": (3, 5),
}

PREFERRED_LEN = {
    "combat_power": 5,
    "diamonds": 4,
    "level": 2,
}

CHAR_MAP = {
    "E": "1", "I": "1", "L": "1", "l": "1", "i": "1", "|": "1", "!": "1",
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "B": "8", "b": "8",
    "S": "5", "s": "5",
    "Z": "2", "z": "2",
    "A": "4", "G": "6", "T": "7",
}

def load_ocr_validation_config(config_path: str = "config.json") -> Tuple[Dict[str, Tuple[int, int]], Dict[str, Any]]:
    """
    Загружает и приводит диапазоны длин и макс. лимиты из config.json к кортежам Python.
    Поддерживает как корень JSON, так и вложенный блок "ocr_config".
    """
    # Берем базовые словари, объявленные выше
    digit_len = DIGIT_LEN.copy()
    max_limits = MAX_LIMITS.copy()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # Вытаскиваем секцию ocr_config, если она есть, иначе читаем из корня
        ocr_cfg = cfg.get("ocr_config", cfg)

        # Подгружаем digit_len (преобразуем списки [3, 5] в кортежи (3, 5))
        if "digit_len" in ocr_cfg:
            for key, val in ocr_cfg["digit_len"].items():
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    digit_len[key] = (int(val[0]), int(val[1]))

        # Подгружаем max_limits
        if "max_limits" in ocr_cfg:
            for key, val in ocr_cfg["max_limits"].items():
                max_limits[key] = val

    except Exception as e:
        print(f"[WARN] [OCR] Не удалось загрузить валидацию из {config_path}: {e}. Используем дефолты.")

    return digit_len, max_limits

# Загрузка валидации строго от абсолютного корня проекта (ROOT_DIR)
DEFAULT_CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
DIGIT_LEN, MAX_LIMITS = load_ocr_validation_config(DEFAULT_CONFIG_PATH)

class HUDReader:
    def __init__(self, debug_mode: bool = False):
        self.ocr = RapidOCR()
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.pending_cache: Dict[str, Dict[str, Any]] = {}
        self.loaded_configs: Dict[str, Dict[str, Any]] = {}
        self.diamond_history: Dict[str, list] = {}
        self.debug_mode = debug_mode
        self.debug_dir = os.path.join(ROOT_DIR, "debug_crops")
        os.makedirs(self.debug_dir, exist_ok=True)
        self._hclose_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 1))
        
        if not _HAS_TESSERACT:
            print("[OCR WARNING] pytesseract не найден — fallback отключен")

    def _get_default_stats(self) -> Dict[str, Any]:
        return {"level": 0, "exp_percent": 0.0, "combat_power": 0, "diamonds": 0}

    def _is_valid_crop(self, crop: np.ndarray) -> bool:
        if crop is None or crop.size == 0:
            return False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        if np.std(gray) < 5.0 or np.mean(gray) < 8.0:
            return False
        return True

    def _to_ocr_image(self, img: np.ndarray) -> np.ndarray:
        if img is None or img.size == 0:
            return img
        if len(img.shape) == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img

    def _horizontal_close(self, img: np.ndarray) -> np.ndarray:
        if img is None or img.size == 0:
            return img
        return cv2.morphologyEx(img, cv2.MORPH_CLOSE, self._hclose_kernel)

    def _clean_number(self, text: str, is_float: bool = False) -> Optional[Union[int, float]]:
        if not text or not text.strip():
            return None

        normalized_text = "".join(CHAR_MAP.get(char, char) for char in text)

        if is_float:
            formatted = normalized_text.replace(",", ".")
            match = re.search(r"\d+\.\d+|\d+", formatted)
            if not match:
                return None
            try:
                val = round(float(match.group(0)), 2)
                if 0.0 <= val <= 100.0:
                    return val
            except ValueError:
                return None
            return None

        digits_groups = re.findall(r"\d+", normalized_text)
        if not digits_groups:
            return None

        if len(digits_groups) > 1:
            if all(len(g) == 3 for g in digits_groups[1:]):
                combined = "".join(digits_groups)
                try:
                    return int(combined)
                except ValueError:
                    return None
            digits_groups = [max(digits_groups, key=len)]

        try:
            return int("".join(digits_groups))
        except ValueError:
            return None

    def _pick_winner(self, key: str, candidates: List[int]) -> Optional[int]:
        if not candidates:
            return None

        cnt = Counter(candidates)
        max_votes = max(cnt.values())

        # Требуем минимум 2 одинаковых ответа для достижения консенсуса.
        # Если все варианты выдали разный бред (42, 24, 2423) — голосование провалено.
        if max_votes < 2:
            return None

        # Отбираем лидеров голосования (те, кто набрал максимум голосов >= 2)
        leaders = [v for v, c in cnt.items() if c == max_votes]

        # Если победитель ровно один — отдаем его
        if len(leaders) == 1:
            return leaders[0]

        # ТАЙ-БРЕЙКЕР: Если ничья между лидерами (например, два по '5000' и два по '500'),
        # используем предпочтительную длину из PREFERRED_LEN
        pref = PREFERRED_LEN.get(key)
        if pref is not None:
            preferred = [v for v in leaders if len(str(v)) == pref]
            if preferred:
                return preferred[0]

        return min(leaders, key=lambda v: abs(len(str(v)) - (pref or len(str(v)))))

    def _generate_preprocessed_variants(self, crop: np.ndarray, color: str = "white") -> List[Tuple[np.ndarray, int]]:
        if crop is None or crop.size == 0:
            return []

        target_h = 128
        h, w = crop.shape[:2]
        if h == 0:
            return []

        scale = target_h / float(h)
        new_w = max(1, int(w * scale))
        resized = cv2.resize(crop, (new_w, target_h), interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        out: List[Tuple[np.ndarray, int]] = []

        if color == "orange":
            lower, upper = np.array([5, 40, 80]), np.array([30, 255, 255])
        else:
            lower, upper = np.array([0, 0, 160]), np.array([180, 60, 255])

        hsv_mask = cv2.inRange(hsv, lower, upper)
        if cv2.countNonZero(hsv_mask) > 20:
            out.append((hsv_mask, 0))

        normalized = cv2.normalize(gray, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)
        _, otsu = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out.append((otsu, 0))
        out.append((cv2.bitwise_not(otsu), 255))

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        out.append((clahe.apply(gray), 0))

        result = []
        for img, pad in out:
            bordered = cv2.copyMakeBorder(img, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=pad)
            result.append((bordered, pad))
        return result

    def _run_ocr_rapid(self, processed: np.ndarray) -> str:
        ocr_img = self._to_ocr_image(processed)
        raw_text = ""
        try:
            result, _ = self.ocr(ocr_img, use_det=False, use_cls=False)
            if result:
                raw_text = " ".join([line[1] for line in result])
        except Exception:
            pass
        if not raw_text.strip():
            try:
                result, _ = self.ocr(ocr_img, use_det=True, use_cls=False)
                if result:
                    raw_text = " ".join([line[1] for line in result])
            except Exception:
                pass
        return raw_text

    def _run_ocr_tesseract(self, processed: np.ndarray) -> str:
        if not _HAS_TESSERACT:
            return ""
        try:
            img = self._to_ocr_image(processed)
            text = pytesseract.image_to_string(img, config=TESS_CONFIG)
            text = (text or "").strip()
            if text:
                return text
            inv = cv2.bitwise_not(
                processed if len(processed.shape) == 2
                else cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
            )
            text2 = pytesseract.image_to_string(
                self._to_ocr_image(inv), config=TESS_CONFIG
            )
            return (text2 or "").strip()
        except Exception as e:
            print(f"[OCR TESS ERROR] {e}")
            return ""

    def _run_ocr(
        self,
        processed: np.ndarray,
        key: str,
        dmin: int,
        dmax: int,
        is_float: bool,
    ) -> Tuple[str, str]:
        if key not in ("diamonds", "combat_power") or is_float:
            raw_rapid = self._run_ocr_rapid(processed)
            return "rapid", raw_rapid

        raw_tess = self._run_ocr_tesseract(processed)
        val_tess = self._clean_number(raw_tess, is_float=False)

        if val_tess is not None:
            n_tess = len(str(int(val_tess)))
            if dmin <= n_tess <= dmax:
                return "tess", raw_tess

        raw_rapid = self._run_ocr_rapid(processed)
        val_rapid = self._clean_number(raw_rapid, is_float=False)

        if val_rapid is not None:
            return "rapid_fallback", raw_rapid

        return "none", raw_tess or raw_rapid

    def _is_valid_value(
        self,
        key: str,
        value: Any,
        current_val: Any = None,
        pending_info: Optional[tuple] = None,
        max_limits: Optional[Dict[str, Any]] = None,
        digit_len: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if value is None:
            return False

        active_limits = max_limits if max_limits is not None else MAX_LIMITS
        active_digit_len = digit_len if digit_len is not None else DIGIT_LEN

        if key == "exp_percent":
            return isinstance(value, (int, float)) and 0.0 <= float(value) <= 100.0

        if key not in ("level", "combat_power", "diamonds"):
            return False
        if not isinstance(value, int) or value < 0:
            return False
        if key in active_limits and value > active_limits[key]:
            return False
        if key in active_digit_len:
            dmin, dmax = active_digit_len[key]
            if not (dmin <= len(str(value)) <= dmax):
                return False

        # ocr_reader.py -> Внутри метода _is_valid_value

        if key == "diamonds":
            # УБРАНО: if value < 500: return False (OCR должен уметь считывать 131, 151 и т.д.)

            # Холодный старт: нужно 3 одинаковых считывания подряд
            if not current_val or current_val == 0:
                return bool(
                    pending_info
                    and pending_info[0] == value
                    and pending_info[1] >= 3
                )

            # Смена разрядности/длины числа (например, 131 <-> 1337) — тоже строго 3 подтверждения
            if len(str(value)) != len(str(int(current_val))):
                return bool(
                    pending_info
                    and pending_info[0] == value
                    and pending_info[1] >= 3
                )

            delta = abs(value - current_val) / max(current_val, 1)

            # Резкий скачок (>15%) — 3 подтверждения
            if delta > 0.15:
                return bool(
                    pending_info
                    and pending_info[0] == value
                    and pending_info[1] >= 3
                )

            # Небольшой скачок (>5%) — 2 подтверждения
            if delta > 0.05:
                return bool(
                    pending_info
                    and pending_info[0] == value
                    and pending_info[1] >= 2
                )

            return True

        if key == "combat_power":
            if value < 1000:
                return False
            if not current_val or current_val == 0:
                return bool(pending_info and pending_info[0] == value and pending_info[1] >= 2)
            if len(str(value)) != len(str(int(current_val))):
                return False
            delta = abs(value - current_val) / max(current_val, 1)
            if delta > 0.12:
                return bool(pending_info and pending_info[0] == value and pending_info[1] >= 2)
            return True

        if not current_val or current_val == 0:
            return bool(pending_info and pending_info[0] == value and pending_info[1] >= 2)
        delta = abs(value - current_val) / max(current_val, 1)
        if delta > 0.35:
            return bool(pending_info and pending_info[0] == value and pending_info[1] >= 2)
        return True

    def _get_rois_and_config(
        self, game_name: str, game_cfg: Optional[Dict] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        self.loaded_configs.pop(game_name, None)
        rois, ocr_cfg = {}, {}

        if game_cfg and "ocr_rois" in game_cfg:
            rois = game_cfg.get("ocr_rois", {})
            ocr_cfg = game_cfg.get("ocr_config", {})
        else:
            cfg_path = os.path.join(ROOT_DIR, "games", game_name, "config.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.loaded_configs[game_name] = data
                        rois = data.get("ocr_rois", {})
                        ocr_cfg = data.get("ocr_config", {})
                except Exception:
                    pass

        limits = MAX_LIMITS.copy()
        if "max_limits" in ocr_cfg:
            limits.update(ocr_cfg["max_limits"])

        digits = DIGIT_LEN.copy()
        if "digit_len" in ocr_cfg:
            for k, v in ocr_cfg["digit_len"].items():
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    digits[k] = (int(v[0]), int(v[1]))

        return rois, limits, digits

# vision/ocr_reader.py (внутри класса HUDReader, после метода _get_rois_and_config)

    def read_stats(
        self,
        frame: Optional[np.ndarray],
        game_name: str = "rf_next",
        window_name: str = "rf_next_1",
        game_cfg: Optional[Dict] = None,
        debug: bool = False,
    ) -> Dict[str, Any]:

        if window_name not in self.cache:
            self.cache[window_name] = self._get_default_stats()
            self.pending_cache[window_name] = {}

        if frame is None:
            return self.cache[window_name].copy()

        rois, max_limits, digit_len = self._get_rois_and_config(game_name, game_cfg)
        if not rois:
            return self.cache[window_name].copy()

        is_debug = self.debug_mode or debug
        h, w = frame.shape[:2]

        for key, cfg in rois.items():
            # Игнорируем служебные зоны поиска иконки
            if "search_zone" in key:
                continue

            crop = None

            # 1. Приоритетный поиск по иконке для diamonds (с порогом 0.85)
            if key == "diamonds" and crop_right_of_template is not None:
                img_dir = os.path.join(ROOT_DIR, "games", game_name, "images")
                icon_path = os.path.join(img_dir, "diamond_icon.png")
                mask_path = os.path.join(img_dir, "diamond_mask.png")
                if os.path.exists(icon_path):
                    crop = crop_right_of_template(
                            frame=frame,
                            template_path=icon_path,
                            mask_path=mask_path if os.path.exists(mask_path) else None,
                            search_zone=rois.get("diamond_search_zone"),
                            crop_width=38,
                            crop_height=16,
                            gap=0,
                            offset_y=1,
                            confidence=0.85,
                            save_debug=is_debug,
                            debug_dir=self.debug_dir,
                            window_name=window_name,
                    )

            # 2. Фолбэк на процентные координаты из config.json
            if crop is None and key != "diamonds":
                if not all(k in cfg for k in ("x_pct", "y_pct", "w_pct", "h_pct")):
                    continue

                x1 = int(cfg["x_pct"] * w)
                y1 = int(cfg["y_pct"] * h)
                x2 = int((cfg["x_pct"] + cfg["w_pct"]) * w)
                y2 = int((cfg["y_pct"] + cfg["h_pct"]) * h)

                crop = frame[y1:y2, x1:x2].copy()

            if not self._is_valid_crop(crop):
                continue

            if is_debug:
                cv2.imwrite(os.path.join(self.debug_dir, f"{window_name}_{key}_raw.png"), crop)

            # 3. Препроцессинг и запуск OCR
            color_theme = cfg.get("color", "white")
            variants = self._generate_preprocessed_variants(crop, color=color_theme)

            candidates: List[int] = []
            is_float = (key == "exp_percent")
            dmin, dmax = digit_len.get(key, (1, 6))

            for idx, (var_img, _) in enumerate(variants):
                # Сохраняем каждый бинарный/CLAHE вариант (v0, v1, v2...)
                if is_debug:
                    cv2.imwrite(
                        os.path.join(self.debug_dir, f"{window_name}_{key}_v{idx}.png"),
                        var_img
                    )

                engine, raw_text = self._run_ocr(var_img, key, dmin, dmax, is_float)
                val = self._clean_number(raw_text, is_float=is_float)

                if is_debug:
                    print(
                        f"[DEBUG] [OCR] win={window_name} | key={key:<12} | v{idx} | "
                        f"engine={engine:<14} | raw='{raw_text}' -> val={val}"
                    )

                if val is not None:
                    if is_float:
                        if self._is_valid_value(key, val, max_limits=max_limits, digit_len=digit_len):
                            self.cache[window_name][key] = val
                            break
                    else:
                        if key == "diamonds" and int(val) < 500:
                            continue
                        candidates.append(int(val))

            # 4. Выбор лучшего распознанного значения и валидация
            if not is_float and candidates:
                winner = self._pick_winner(key, candidates)
                curr_val = self.cache[window_name].get(key, 0)
                pending = self.pending_cache[window_name].get(key)
                is_valid = self._is_valid_value(key, winner, curr_val, pending, max_limits, digit_len)

                if is_debug:
                    print(
                        f"[DEBUG] [RESULT] win={window_name} | key={key:<12} | "
                        f"winner={winner} | curr={curr_val} | valid={is_valid}"
                    )

                if is_valid:
                    self.cache[window_name][key] = winner
                    self.pending_cache[window_name][key] = (winner, 0)
                else:
                    # Накапливаем счетчик подтверждений
                    if pending and pending[0] == winner:
                        cnt = pending[1] + 1
                        self.pending_cache[window_name][key] = (winner, cnt)
                        
                        # Если накопили 3 совпадения подряд — признаем значение валидным и обновляем кэш
                        if cnt >= 3:
                            self.cache[window_name][key] = winner
                            self.pending_cache[window_name][key] = (winner, 0)
                    else:
                        self.pending_cache[window_name][key] = (winner, 1)

        return self.cache[window_name].copy()


_hud_reader_instance = HUDReader(debug_mode=True)


def read_hud_stats(
    frame=None,
    game_name="rf_next",
    window_name="rf_next_1",
    game_cfg=None,
    hwnd=None,
    debug=False,
):
    if frame is None and hwnd is not None:
        frame = capture_game_frame(hwnd)
    return _hud_reader_instance.read_stats(
        frame,
        game_name=game_name,
        window_name=window_name,
        game_cfg=game_cfg,
        debug=debug,
    )