from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import ticker_checker

TZ = ZoneInfo("America/Toronto")


def minute_bars_like_yf_download(ticker, closes):
    """Shape of yf.download for one ticker: (Price, Ticker) MultiIndex columns, UTC index."""
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    index = pd.DatetimeIndex([now - timedelta(minutes=len(closes) - 1 - i) for i in range(len(closes))]).tz_convert("UTC")
    columns = pd.MultiIndex.from_tuples([("Close", ticker)], names=["Price", "Ticker"])
    return pd.DataFrame([[c] for c in closes], index=index, columns=columns)


def test_intraday_price_with_multiindex_columns(monkeypatch):
    df = minute_bars_like_yf_download("MSFT", [100.0, 101.0, 102.0])
    monkeypatch.setattr(ticker_checker.yf, "download", lambda **kwargs: df)
    now_hhmm = datetime.now(TZ).strftime("%H:%M")
    assert ticker_checker.get_intraday_price_at("MSFT", now_hhmm, "America/Toronto") == 102.0


# --- relative volume ---

SESSIONS = [datetime(2026, 10, d).date() for d in (13, 14, 15, 16, 19, 20, 21)]  # last one is "today"


def daily_volume(days, volumes):
    """yf.download(interval="1d") shape: tz-naive date index, (Price, Ticker) MultiIndex columns."""
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="Date")
    columns = pd.MultiIndex.from_tuples([("Volume", "NVDA")], names=["Price", "Ticker"])
    return pd.DataFrame([[v] for v in volumes], index=index, columns=columns)


def five_minute_volume(days, per_bar=100, spikes=None):
    """yf.download(interval="5m") shape: 09:30-15:55 bars, UTC index. spikes: {(day, "HH:MM"): volume}."""
    stamps, volumes = [], []
    for day in days:
        start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=TZ)
        for k in range(78):
            stamp = start + timedelta(minutes=5 * k)
            stamps.append(stamp)
            volumes.append((spikes or {}).get((day, stamp.strftime("%H:%M")), per_bar))
    index = pd.DatetimeIndex(stamps).tz_convert("UTC")
    columns = pd.MultiIndex.from_tuples([("Volume", "NVDA")], names=["Price", "Ticker"])
    return pd.DataFrame([[v] for v in volumes], index=index, columns=columns)


def fake_download(daily, intraday):
    def download(**kwargs):
        if kwargs["interval"] == "1d":
            return daily
        if isinstance(intraday, Exception):
            raise intraday
        return intraday
    return download


DAILY = daily_volume(SESSIONS, [7800] * 6 + [12000])  # past sessions 7,800/day; today 12,000 so far


def test_relative_volume_scales_average_by_typical_share_traded_so_far(monkeypatch):
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(DAILY, five_minute_volume(SESSIONS)))
    now = datetime(2026, 10, 21, 12, 47, tzinfo=TZ)  # bars 09:30..12:45 = 40 of 78 -> 4,000 expected by now
    assert ticker_checker.get_relative_volume("NVDA", "America/Toronto", now=now) == {
        "volume": 12000.0, "avg_volume": 4000.0, "ratio": 3.0, "days": 6, "as_of": "12:47"}


def test_relative_volume_ignores_bad_intraday_spikes(monkeypatch):
    # Yahoo's 5m bars sometimes carry absurd spikes; the median share across sessions shrugs them off
    spikes = {(SESSIONS[0], "09:30"): 10_000_000, (SESSIONS[-1], "11:55"): 76_000_000}
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(DAILY, five_minute_volume(SESSIONS, spikes=spikes)))
    out = ticker_checker.get_relative_volume("NVDA", now=datetime(2026, 10, 21, 12, 47, tzinfo=TZ))
    assert out["ratio"] == 3.0


def test_relative_volume_falls_back_to_elapsed_time_without_intraday_data(monkeypatch):
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(DAILY, RuntimeError("no 5m data")))
    out = ticker_checker.get_relative_volume("NVDA", now=datetime(2026, 10, 21, 12, 45, tzinfo=TZ))
    assert (out["avg_volume"], out["ratio"]) == (3900.0, 3.08)  # 195 of 390 minutes elapsed


def test_relative_volume_after_close_uses_full_day(monkeypatch):
    daily = daily_volume(SESSIONS, [7800] * 6 + [3900])
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(daily, RuntimeError("not needed")))
    out = ticker_checker.get_relative_volume("NVDA", now=datetime(2026, 10, 21, 18, 0, tzinfo=TZ))
    assert (out["volume"], out["avg_volume"], out["ratio"], out["as_of"]) == (3900.0, 7800.0, 0.5, "16:00")


def test_relative_volume_before_open_is_none(monkeypatch):
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(DAILY, five_minute_volume(SESSIONS)))
    assert ticker_checker.get_relative_volume("NVDA", now=datetime(2026, 10, 21, 9, 0, tzinfo=TZ)) is None


def test_relative_volume_needs_todays_bar(monkeypatch):
    daily = daily_volume(SESSIONS[:-1], [7800] * 6)
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(daily, five_minute_volume(SESSIONS)))
    assert ticker_checker.get_relative_volume("NVDA", now=datetime(2026, 10, 21, 11, 0, tzinfo=TZ)) is None


def test_relative_volume_needs_enough_history(monkeypatch):
    daily = daily_volume(SESSIONS[-3:], [7800, 7800, 12000])
    monkeypatch.setattr(ticker_checker.yf, "download", fake_download(daily, five_minute_volume(SESSIONS)))
    assert ticker_checker.get_relative_volume("NVDA", now=datetime(2026, 10, 21, 11, 0, tzinfo=TZ)) is None


def test_relative_volume_survives_download_failure(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(ticker_checker.yf, "download", boom)
    assert ticker_checker.get_relative_volume("NVDA") is None


def test_volume_fact_line():
    v = {"volume": 12_300_000.0, "avg_volume": 4_400_000.0, "ratio": 2.8, "days": 20, "as_of": "14:10"}
    assert ticker_checker.volume_fact_lines(v) == [
        "Volume: 2.80x the 20-day average for this time of day (12.3M vs 4.4M by 14:10)"]
    assert ticker_checker.volume_fact_lines(None) == []
