import core
import main_agent


def test_ticker_price_returns_core_snapshot_as_dict(monkeypatch):
    snapshot = {"ticker": "MSFT", "price_now": 110.0, "prev_close": 100.0, "change_pct": 10.0}
    monkeypatch.setattr(core, "fetch_snapshot", lambda ticker, timezone: snapshot)
    assert main_agent.ticker_price.invoke({"ticker": "MSFT"}) == snapshot


def test_generate_report_tolerates_snapshot_without_ticker_key(monkeypatch):
    seen = {}

    def fake_brief(tickers, context, language):
        seen["context"] = context
        return "report"

    monkeypatch.setattr(core, "generate_brief", fake_brief)
    result = main_agent.generate_report.invoke({
        "ticker": "MSFT",
        "snapshot": {"price_now": None, "prev_close": 100.0, "change_pct": None},
        "news": [{"title": "headline", "link": "https://x"}],
        "language": "en",
    })
    assert result == "report"
    assert "MSFT: now=N/A" in seen["context"]
    assert "headline" in seen["context"]

