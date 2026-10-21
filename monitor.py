import json
import traceback
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo
import time as _time

DEFAULT_TZ = "America/Toronto"
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
MAX_SLEEP_SECONDS = 60  # re-check the wall clock so laptop sleep doesn't make us overshoot


def _is_trading_day(day) -> bool:
    # weekends only; exchange holidays are not handled
    return day.weekday() < 5


def _next_daily(after: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    day = after.date()
    while True:
        candidate = datetime.combine(day, time(h, m), tzinfo=after.tzinfo)
        if candidate > after and _is_trading_day(day):
            return candidate
        day += timedelta(days=1)


def _next_interval(after: datetime, minutes: int) -> datetime:
    """Ticks every `minutes` from market open, plus one at market close."""
    step = timedelta(minutes=minutes)
    day = after.date()
    while True:
        if _is_trading_day(day):
            open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=after.tzinfo)
            close_dt = datetime.combine(day, MARKET_CLOSE, tzinfo=after.tzinfo)
            if after < open_dt:
                return open_dt
            if after < close_dt:
                n = (after - open_dt) // step + 1
                return min(open_dt + n * step, close_dt)
        day += timedelta(days=1)


def next_run_time(rules: Dict[str, Any], after: datetime) -> datetime:
    """
    Next scheduled run strictly after `after`, in the market timezone.
    daily: schedule_time on each weekday; interval: every interval_minutes during market hours.
    """
    after = after.astimezone(ZoneInfo(rules.get("timezone", DEFAULT_TZ)))
    if rules.get("schedule_mode") == "interval":
        return _next_interval(after, int(rules["interval_minutes"]))
    return _next_daily(after, rules["schedule_time"])


class AlertStore:
    """
    Remembers which alerts were already sent today, persisted as JSON so restarts don't re-alert.
    A ticker alerts again only when its move crosses the next multiple of the threshold
    (e.g. 5% -> 10%) or flips direction; everything resets on a new day.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def filter_new(self, rows: List[Dict[str, Any]], threshold: float, day: str) -> List[Dict[str, Any]]:
        fresh = []
        for row in rows:
            change_pct = row.get("change_pct")
            if change_pct is None or abs(change_pct) <= threshold:
                continue
            level = int(abs(change_pct) // threshold)
            direction = "up" if change_pct > 0 else "down"
            entry = self._data.get(row["ticker"])
            if not isinstance(entry, dict) or entry.get("day") != day:
                entry = {"day": day}
            if level > entry.get(direction, 0):
                entry[direction] = level
                fresh.append(row)
            self._data[row["ticker"]] = entry
        if fresh:
            self._save()
        return fresh


def run_loop(
    rules: Dict[str, Any],
    tick: Callable[[], Any],
    now: Optional[Callable[[], datetime]] = None,
    sleep: Callable[[float], Any] = _time.sleep,
    max_runs: Optional[int] = None,
) -> None:
    """Call `tick` at every scheduled time until interrupted (or `max_runs` is reached)."""
    tz = ZoneInfo(rules.get("timezone", DEFAULT_TZ))
    now = now or (lambda: datetime.now(tz))
    runs = 0
    after = now()
    while max_runs is None or runs < max_runs:
        run_at = next_run_time(rules, after)
        print(f"[MONITOR] next run at {run_at:%Y-%m-%d %H:%M %Z}")
        while (remaining := (run_at - now()).total_seconds()) > 0:
            sleep(min(remaining, MAX_SLEEP_SECONDS))
        try:
            tick()
        except Exception:
            # one failed run (network, yfinance, LLM) must not kill the monitor
            print(f"[MONITOR][error] run at {run_at:%H:%M} failed:\n{traceback.format_exc()}")
        runs += 1
        after = max(run_at, now())
