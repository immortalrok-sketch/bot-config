"""
Учёт нафармленной валюты и оповещение о пороге. Слой SERVICES.

FarmLedger
    По показаниям баланса каждого окна считает, сколько НАФАРМЛЕНО (рост баланса),
    и хранит историю в data/farm/<дата>.jsonl. Файлы только дописываются, ничего не
    перезаписывается: обрыв питания в худшем случае оборвёт последнюю строку.

    Правила подсчёта (см. record):
      * 0 / None — «неизвестно» (сброс, офлайн, шум чтения): игнорируется, база не меняется;
      * первое показание окна после запуска — только база, прироста нет;
      * рост баланса засчитывается как фарм, если он не больше max_gain за одно показание;
      * слишком крупный скачок — не фарм (перевод, покупка, сбой чтения), пишется как «исключено»;
      * падение (продажа, трата, перевод) не вычитается; чтобы шум чтения вида 1690 -> 169 -> 1690
        не дал ложный прирост, новая база принимается только если низкое значение держится
        drop_confirm_sec секунд;
      * перевод между окнами одной игры: падение на одном окне и такой же рост на другом
        в пределах transfer_window_sec — рост не засчитывается как фарм.

DiamondAlerts
    Оповещения о сумме бриллиантов по игре. Настройка в local_node.json на главной машине.

    Режим «шаг» — сообщение при каждой новой пройденной отметке (10 000, 20 000, 30 000...):
        "alerts": {"diamonds_step": 10000}
    Если сумма уменьшилась (продажа) и стала ниже rearm_ratio * последней отметки, отметки
    считаются заново: после продажи снова придёт сообщение на 10 000 и т.д.

    Режим «один порог» — одно сообщение при достижении числа:
        "alerts": {"diamonds_total": 10000}
    Повторно — только после того, как сумма опустится ниже rearm_ratio * порог.

    Оба параметра — число для всех игр либо {"default": 10000, "rf_next": 5000}.
    rearm_ratio (по умолчанию 0.8) — общий. Если указаны оба режима, действует «шаг».
"""
import atexit
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "farm"
ALERTS_STATE_FILE = PROJECT_ROOT / "data" / "alerts_state.json"
NODE_CONFIG_FILE = PROJECT_ROOT / "local_node.json"


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


class FarmLedger:
    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        clock: Callable[[], float] = time.time,
        max_gain_for: Optional[Callable[[str], int]] = None,
        drop_confirm_sec: float = 60.0,
        transfer_min: int = 100,
        transfer_window_sec: float = 600.0,
        transfer_tolerance: float = 0.10,
        bucket_sec: int = 300,
        keep_days: int = 100,
        load_days: int = 31,
    ):
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.max_gain_for = max_gain_for or (lambda game: 1000)
        self.drop_confirm_sec = drop_confirm_sec
        self.transfer_min = transfer_min
        self.transfer_window_sec = transfer_window_sec
        self.transfer_tolerance = transfer_tolerance
        self.bucket_sec = bucket_sec
        self.keep_days = keep_days
        self.load_days = load_days

        self._lock = threading.RLock()
        self._base: Dict[Tuple[str, str], int] = {}
        self._pending: Dict[Tuple[str, str], dict] = {}
        self._recent_drops: List[dict] = []
        # день -> игра -> {"gain": int, "skipped": int}
        self._days: Dict[str, Dict[str, Dict[str, int]]] = {}
        # день -> "ПК/окно" -> нафармлено (для будущих отчётов по слабому окну)
        self._wdays: Dict[str, Dict[str, int]] = {}
        self._buckets: Dict[Tuple[int, str, str, str], int] = {}
        self._lines: List[dict] = []

        self._load()

    # ------------------------------------------------------------------ чтение истории

    def _apply_to_aggregates(self, rec: dict) -> None:
        day = _day(rec["t"])
        game = rec.get("g", "")
        slot = self._days.setdefault(day, {}).setdefault(game, {"gain": 0, "skipped": 0})
        if "d" in rec:
            slot["gain"] += int(rec["d"])
            wkey = f"{rec.get('pc', '')}/{rec.get('w', '')}"
            wd = self._wdays.setdefault(day, {})
            wd[wkey] = wd.get(wkey, 0) + int(rec["d"])
        elif "x" in rec:
            slot["skipped"] += int(rec["x"])

    def _load(self) -> None:
        if not self.data_dir.exists():
            return
        today = datetime.fromtimestamp(self.clock()).date()
        load_from = today - timedelta(days=self.load_days)
        delete_before = today - timedelta(days=self.keep_days)
        for path in sorted(self.data_dir.glob("*.jsonl")):
            try:
                file_day = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_day < delete_before:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            if file_day < load_from:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                try:
                    rec = json.loads(line)
                    self._apply_to_aggregates(rec)
                except Exception:
                    continue          # оборванная при отключении света строка и т.п.

    # ------------------------------------------------------------------ учёт показаний

    def record(self, pc: str, window: str, game: str, value) -> None:
        """Принять очередное показание баланса окна."""
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return                     # «неизвестно»: ни базу, ни счётчики не трогаем

        now = self.clock()
        key = (pc, window)
        with self._lock:
            base = self._base.get(key)
            if base is None:
                self._base[key] = value
                return

            delta = value - base
            if delta == 0:
                self._pending.pop(key, None)
                return

            if delta < 0:
                self._on_drop(key, game, base, value, now)
                return

            # delta > 0: если до этого был «провал», то это был шум или возврат к базе
            self._pending.pop(key, None)
            self._on_rise(key, pc, window, game, delta, now)
            self._base[key] = value

    def _on_drop(self, key, game, base, value, now) -> None:
        size = base - value
        pend = self._pending.get(key)
        if pend is None:
            drop = None
            if size >= self.transfer_min:
                drop = {"t": now, "g": game, "key": key, "size": size, "used": False}
                self._recent_drops.append(drop)
            self._pending[key] = {"since": now, "drop": drop}
            return
        if pend["drop"] is not None:
            pend["drop"]["size"] = max(pend["drop"]["size"], size)
        elif size >= self.transfer_min:
            drop = {"t": now, "g": game, "key": key, "size": size, "used": False}
            self._recent_drops.append(drop)
            pend["drop"] = drop
        if now - pend["since"] >= self.drop_confirm_sec:
            self._base[key] = value        # низкое значение держится — это реальная трата/продажа
            self._pending.pop(key, None)

    def _match_transfer(self, game, key, delta, now) -> bool:
        self._recent_drops = [
            d for d in self._recent_drops if now - d["t"] <= self.transfer_window_sec
        ]
        for d in self._recent_drops:
            if d["used"] or d["g"] != game or d["key"] == key:
                continue
            tolerance = max(5, self.transfer_tolerance * delta)
            if abs(d["size"] - delta) <= tolerance:
                d["used"] = True
                return True
        return False

    def _on_rise(self, key, pc, window, game, delta, now) -> None:
        if delta >= self.transfer_min and self._match_transfer(game, key, delta, now):
            self._skip(pc, window, game, delta, "transfer", now)
            return
        if delta > int(self.max_gain_for(game)):
            self._skip(pc, window, game, delta, "jump", now)
            return
        self._gain(pc, window, game, delta, now)

    def _gain(self, pc, window, game, delta, now) -> None:
        self._apply_to_aggregates({"t": now, "pc": pc, "w": window, "g": game, "d": delta})
        bucket = int(now // self.bucket_sec) * self.bucket_sec
        bkey = (bucket, pc, window, game)
        self._buckets[bkey] = self._buckets.get(bkey, 0) + delta

    def _skip(self, pc, window, game, amount, why, now) -> None:
        rec = {"t": int(now), "pc": pc, "w": window, "g": game, "x": int(amount), "why": why}
        self._apply_to_aggregates(rec)
        self._lines.append(rec)

    # ------------------------------------------------------------------ запись на диск

    def flush(self, force: bool = False) -> None:
        """Дописать накопленное в файлы дня. Вызывать раз в минуту и при выходе."""
        with self._lock:
            now = self.clock()
            current = int(now // self.bucket_sec) * self.bucket_sec
            lines = list(self._lines)
            self._lines.clear()
            ready = [k for k in self._buckets if force or k[0] < current]
            for k in ready:
                b, pc, w, g = k
                lines.append({"t": b, "pc": pc, "w": w, "g": g, "d": self._buckets.pop(k)})
        if not lines:
            return
        by_day: Dict[str, List[dict]] = {}
        for rec in lines:
            by_day.setdefault(_day(rec["t"]), []).append(rec)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for day, recs in by_day.items():
                with open(self.data_dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
                    for rec in recs:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[FarmLedger] Не удалось записать историю: {e}")
            with self._lock:
                self._lines.extend(lines)      # вернуть в очередь, попробуем в следующий раз

    # ------------------------------------------------------------------ данные для панели

    def totals(self, game: str) -> Dict[str, int]:
        """Нафармлено по игре: сегодня, 7 дней, 30 дней (скользящие, включая сегодня)."""
        today = datetime.fromtimestamp(self.clock()).date()

        def span(n: int, field: str) -> int:
            total = 0
            with self._lock:
                for i in range(n):
                    day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                    total += self._days.get(day, {}).get(game, {}).get(field, 0)
            return total

        return {
            "today": span(1, "gain"),
            "week": span(7, "gain"),
            "month": span(30, "gain"),
            "skipped_month": span(30, "skipped"),
        }

    def window_totals(self, days: int = 1) -> Dict[str, int]:
        """Нафармлено каждым окном за последние N дней (для отчёта по слабому окну)."""
        today = datetime.fromtimestamp(self.clock()).date()
        result: Dict[str, int] = {}
        with self._lock:
            for i in range(days):
                day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                for wkey, val in self._wdays.get(day, {}).items():
                    result[wkey] = result.get(wkey, 0) + val
        return result


class DiamondAlerts:
    """Одно сообщение в Telegram, когда сумма по игре дошла до порога."""

    def __init__(
        self,
        node_cfg_path: Path = NODE_CONFIG_FILE,
        state_path: Path = ALERTS_STATE_FILE,
        send: Optional[Callable[[str, int, str], bool]] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.node_cfg_path = Path(node_cfg_path)
        self.state_path = Path(state_path)
        self.send = send or self._send_telegram
        self.clock = clock
        self._state = self._load_state()
        self._last_warn = 0.0

    # --- состояние (чтобы после перезапуска не слать то же сообщение повторно)

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"fired": {}}
        except Exception:
            return {"fired": {}}

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except Exception as e:
            print(f"[Алерты] Не удалось сохранить состояние: {e}")

    # --- настройки

    def _load_cfg(self) -> dict:
        try:
            with open(self.node_cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _threshold(alerts_cfg: dict, game: str, key: str = "diamonds_total") -> int:
        value = alerts_cfg.get(key)
        if isinstance(value, dict):
            value = value.get(game, value.get("default"))
        try:
            return int(value) if value else 0
        except (TypeError, ValueError):
            return 0

    def _warn(self, text: str) -> None:
        now = self.clock()
        if now - self._last_warn >= 600:       # не чаще раза в 10 минут
            self._last_warn = now
            print(f"[Алерты] {text}")

    # --- отправка

    @staticmethod
    def _send_telegram(token: str, chat_id: int, text: str) -> bool:
        try:
            import requests
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=8,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # --- проверка

    def check(self, games: List[dict], complete: bool = True) -> List[str]:
        """
        games    — [{"game": "RF_NEXT", "amount": 10482, "usd": 52.41}, ...]
        complete — все ли машины на связи. Если нет, суммы занижены, и «сбрасывать»
                   сработавшие оповещения нельзя (иначе при возврате связи придёт повтор).
        Возвращает список отправленных сообщений.
        """
        cfg = self._load_cfg()
        if not cfg.get("is_server_pc", True):
            return []
        alerts_cfg = cfg.get("alerts") or {}
        if not alerts_cfg:
            return []
        tg = cfg.get("telegram") or {}
        token, chat_id = tg.get("token"), tg.get("admin_chat_id")
        rearm_ratio = float(alerts_cfg.get("rearm_ratio", 0.8))

        sent: List[str] = []
        fired: dict = self._state.setdefault("fired", {})
        milestones: dict = self._state.setdefault("milestone", {})
        changed = False

        def deliver(text: str) -> bool:
            if not token or not chat_id:
                self._warn("В local_node.json нет токена/chat_id Telegram — оповещение не отправлено.")
                return False
            if self.send(token, chat_id, text):
                sent.append(text)
                return True
            self._warn("Не удалось отправить оповещение в Telegram — повторю позже.")
            return False

        for item in games:
            game = str(item.get("game", "")).lower()
            if not game:
                continue
            amount = int(item.get("amount", 0) or 0)
            usd = item.get("usd", 0)

            # --- режим «шаг»: сообщение на каждой новой отметке ---
            step = self._threshold(alerts_cfg, game, "diamonds_step")
            if step > 0:
                reached = (amount // step) * step           # последняя пройденная отметка
                last = int(milestones.get(game, 0))
                if reached > last:
                    text = (
                        f"💎 {game.upper()}: на ферме накопилось {_fmt(amount)} бриллиантов "
                        f"(≈ ${usd}). Отметка {_fmt(reached)} (оповещение каждые {_fmt(step)})."
                    )
                    if deliver(text):
                        milestones[game] = reached
                        changed = True
                elif complete and last > 0 and amount < last * rearm_ratio:
                    milestones[game] = reached              # была продажа/трата — отметки заново
                    changed = True
                continue

            # --- режим «один порог» ---
            threshold = self._threshold(alerts_cfg, game)
            if threshold <= 0:
                continue
            if not fired.get(game) and amount >= threshold:
                text = (
                    f"💎 {game.upper()}: на ферме накопилось {_fmt(amount)} бриллиантов "
                    f"(≈ ${usd}). Порог оповещения: {_fmt(threshold)}."
                )
                if deliver(text):
                    fired[game] = True
                    changed = True
            elif fired.get(game) and complete and amount < threshold * rearm_ratio:
                fired[game] = False
                changed = True

        if changed:
            self._save_state()
        return sent