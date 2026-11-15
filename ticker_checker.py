from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
from typing import List, Optional, Tuple

def get_previous_close(ticker: str) -> Optional[float]:
    """
       Return previous close for ticker; None if unavailable.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.history(period="2d", interval="1d")
        if info is None or info.empty:
            return None
        if len(info) >= 2:
            return float(info["Close"].iloc[-2])
        else:
            return float(info["Close"].iloc[-1])
    except Exception:
        return None


def get_intraday_price_at(ticker: str, target_hhmm: str, timezone_str: str = "America/Toronto") -> Optional[float]:
    """
    get today's intraday price close to target_hhmm from ticker.
    """
    try:
        tz = ZoneInfo(timezone_str)
        now_local = datetime.now(tz)
        target_h, target_m = map(int, target_hhmm.split(":"))
        target_dt = now_local.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        df = yf.download(tickers=ticker, period="1d", interval="1m", auto_adjust=False, progress=False)
        if df is None or df.empty:
            # retry with 5m bars
            df = yf.download(tickers=ticker, period="5d", interval="5m", auto_adjust=False, progress=False)
            if df is None or df.empty:
                return None

        df_local = df.tz_convert(tz) if df.index.tz is not None else df.tz_localize(tz)
        df_today = df_local[df_local.index.date == target_dt.date()]
        if df_today.empty:
            return None
        df_until = df_today[df_today.index <= target_dt]
        if df_until.empty:
            return None

        close = df_until["Close"]
        # yf.download returns (Price, Ticker) MultiIndex columns, so "Close" is a one-column DataFrame
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return float(close.iloc[-1])
    except Exception:
        return None

def get_change_pct_vs_prev_close(ticker: str, price_now: float) -> Optional[float]:
    """
    Compute percentage change versus previous close.
    """
    prev_close = get_previous_close(ticker)
    if prev_close is None or prev_close == 0:
        return None
    return (price_now - prev_close) / prev_close * 100.0


SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)
SESSION_MINUTES = 390
VOLUME_LOOKBACK_DAYS = 20
MIN_VOLUME_HISTORY_DAYS = 5


def _volume_series(df) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    volume = df["Volume"]
    # yf.download returns (Price, Ticker) MultiIndex columns, so "Volume" is a one-column DataFrame
    return volume.iloc[:, 0] if isinstance(volume, pd.DataFrame) else volume


def _typical_share_traded_by(ticker: str, tz: ZoneInfo, today, cutoff: dtime) -> float:
    """
    Median share of a full session's volume traded by `cutoff` over recent sessions.
    Yahoo's 5m volume bars can contain absurd spikes, so they are only used for this
    shape and the median keeps one bad bar from skewing it. Falls back to elapsed time.
    """
    elapsed = (cutoff.hour * 60 + cutoff.minute) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)
    fallback = min(max(elapsed / SESSION_MINUTES, 0.0), 1.0)
    try:
        volume = _volume_series(yf.download(tickers=ticker, period="1mo", interval="5m",
                                            auto_adjust=False, progress=False))
    except Exception:
        return fallback
    if volume is None:
        return fallback
    volume = volume.tz_convert(tz) if volume.index.tz is not None else volume.tz_localize(tz)
    shares = []
    for day, bars in volume.groupby(volume.index.date):
        total = bars.sum()
        if day >= today or len(bars) < 70 or total <= 0:  # skip today and partial sessions
            continue
        # a 5m bar is stamped with its start time; count bars that started by the cutoff
        shares.append(bars[[t <= cutoff for t in bars.index.time]].sum() / total)
    if len(shares) < MIN_VOLUME_HISTORY_DAYS:
        return fallback
    return float(pd.Series(shares).median())


def get_relative_volume(ticker: str, timezone_str: str = "America/Toronto",
                        now: Optional[datetime] = None) -> Optional[dict]:
    """
    Today's volume so far vs the volume usually traded by this time of day.
    A ratio well above 1 means unusually heavy trading. None when data is missing.
    """
    try:
        tz = ZoneInfo(timezone_str)
        now = (now or datetime.now(tz)).astimezone(tz)
        cutoff = min(now.time().replace(second=0, microsecond=0), SESSION_CLOSE)
        if cutoff <= SESSION_OPEN:
            return None
        # daily bars agree with Yahoo's quote volume; the 5m bars often don't
        daily = _volume_series(yf.download(tickers=ticker, period="2mo", interval="1d",
                                           auto_adjust=False, progress=False))
        if daily is None:
            return None
        by_date = {ts.date(): float(v) for ts, v in daily.items()}
        if not by_date.get(now.date()):
            return None
        history = [v for d, v in sorted(by_date.items()) if d < now.date()][-VOLUME_LOOKBACK_DAYS:]
        if len(history) < MIN_VOLUME_HISTORY_DAYS:
            return None
        share = 1.0 if cutoff >= SESSION_CLOSE else _typical_share_traded_by(ticker, tz, now.date(), cutoff)
        expected = sum(history) / len(history) * share
        if expected <= 0:
            return None
        return {
            "volume": by_date[now.date()],
            "avg_volume": round(expected, 1),
            "ratio": round(by_date[now.date()] / expected, 2),
            "days": len(history),
            "as_of": cutoff.strftime("%H:%M"),
        }
    except Exception:
        return None


def _human(n: float) -> str:
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


def volume_fact_lines(v: Optional[dict]) -> List[str]:
    if not v:
        return []
    return [f"Volume: {v['ratio']:.2f}x the {v['days']}-day average for this time of day "
            f"({_human(v['volume'])} vs {_human(v['avg_volume'])} by {v['as_of']})"]
