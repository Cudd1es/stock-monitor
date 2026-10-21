from core import find_alerts, build_context
import core


def test_drop_beyond_threshold_alerts():
    rows = [{"ticker": "MSFT", "price_now": 90.0, "change_pct": -6.0}]
    assert len(find_alerts(rows, 5.0)) == 1


def test_move_equal_to_threshold_does_not_alert():
    rows = [{"ticker": "MSFT", "price_now": 105.0, "change_pct": 5.0}]
    assert find_alerts(rows, 5.0) == []


def test_missing_price_shows_na():
    ctx = build_context([{"ticker": "MSFT", "price_now": None, "prev_close": None, "change_pct": None}], {})
    assert "now=N/A" in ctx


def test_empty_snapshot():
    assert build_context([], {}) == "No price snapshot."


def test_fetch_snapshot_computes_change(monkeypatch):
    monkeypatch.setattr(core.ticker_checker, "get_intraday_price_at", lambda *a: 110.0)
    monkeypatch.setattr(core.ticker_checker, "get_previous_close", lambda t: 100.0)
    assert core.fetch_snapshot("MSFT")["change_pct"] == 10.0


def test_fetch_snapshot_market_closed(monkeypatch):
    monkeypatch.setattr(core.ticker_checker, "get_intraday_price_at", lambda *a: None)
    monkeypatch.setattr(core.ticker_checker, "get_previous_close", lambda t: 100.0)
    assert core.fetch_snapshot("MSFT")["change_pct"] is None