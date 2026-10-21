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
