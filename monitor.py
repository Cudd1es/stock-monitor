import json
import traceback
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import time as _time

DEFAULT_TZ = "America/Toronto"
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
MAX_SLEEP_SECONDS = 60  # re-check the wall clock so laptop sleep doesn't make us overshoot


def _easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of the month; n=-1 for the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """Saturday holidays are observed on Friday, Sunday holidays on Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset:
    """Full-day NYSE closures from the standard rules (early closes and one-off closures not included)."""
    days = {
        _nth_weekday(year, 1, 0, 3),            # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),            # Washington's Birthday
        _easter(year) - timedelta(days=2),      # Good Friday
        _nth_weekday(year, 5, 0, -1),           # Memorial Day
        _observed(date(year, 7, 4)),            # Independence Day
        _nth_weekday(year, 9, 0, 1),            # Labor Day
        _nth_weekday(year, 11, 3, 4),           # Thanksgiving
        _observed(date(year, 12, 25)),          # Christmas
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    if date(year, 1, 1).weekday() != 5:
        # a Saturday New Year's Day is not observed on the Friday before (that's the prior year's Dec 31)
        days.add(_observed(date(year, 1, 1)))
    return frozenset(days)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def _next_daily(after: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    day = after.date()
    while True:
        candidate = datetime.combine(day, time(h, m), tzinfo=after.tzinfo)
        if candidate > after and is_trading_day(day):
            return candidate
        day += timedelta(days=1)


def _next_interval(after: datetime, minutes: int) -> datetime:
    """Ticks every `minutes` from market open, plus one at market close."""
    step = timedelta(minutes=minutes)
    day = after.date()
    while True:
        if is_trading_day(day):
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

    @staticmethod
    def _level(row: Dict[str, Any], threshold: float) -> Optional[Tuple[str, int]]:
        change_pct = row.get("change_pct")
        if change_pct is None or abs(change_pct) <= threshold:
            return None
        return ("up" if change_pct > 0 else "down"), int(abs(change_pct) // threshold)

    def _entry(self, ticker: str, day: str) -> Dict[str, Any]:
        entry = self._data.get(ticker)
        return entry if isinstance(entry, dict) and entry.get("day") == day else {"day": day}

    def filter_new(self, rows: List[Dict[str, Any]], threshold: float, day: str) -> List[Dict[str, Any]]:
        """Rows worth alerting on. Nothing is recorded: call `record` once the alert was delivered."""
        fresh = []
        for row in rows:
            level = self._level(row, threshold)
            if level and level[1] > self._entry(row["ticker"], day).get(level[0], 0):
                fresh.append(row)
        return fresh

    def record(self, rows: List[Dict[str, Any]], threshold: float, day: str) -> None:
        for row in rows:
            level = self._level(row, threshold)
            if not level:
                continue
            entry = self._entry(row["ticker"], day)
            entry[level[0]] = max(level[1], entry.get(level[0], 0))
            self._data[row["ticker"]] = entry
        if rows:
            self._save()


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
