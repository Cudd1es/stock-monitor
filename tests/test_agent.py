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
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [
        {"ticker": "SPY", "price_now": 500.0, "prev_close": 495.0, "change_pct": 1.01}])
    result = main_agent.generate_report.invoke({
        "ticker": "MSFT",
        "snapshot": {"price_now": None, "prev_close": 100.0, "change_pct": None},
        "news": [{"title": "headline", "link": "https://x"}],
        "language": "en",
    })
    assert result == "report"
    assert "MSFT: now=N/A" in seen["context"]
    assert "headline" in seen["context"]
    assert "Market: SPY 1.01%" in seen["context"]



def test_ticker_research_tool(monkeypatch):
    monkeypatch.setattr(main_agent.analyst, "fetch_analyst_view", lambda t: {"consensus": None})
    monkeypatch.setattr(main_agent.filings, "fetch_recent_filings", lambda t: [])
    monkeypatch.setattr(main_agent.ticker_checker, "get_relative_volume", lambda t, timezone: None)
    assert main_agent.ticker_research.invoke({"ticker": "MSFT"}) == {
        "analyst": {"consensus": None}, "filings": [], "volume": None}


def test_generate_report_includes_research(monkeypatch):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(core, "generate_brief", lambda tickers, context, language: seen.setdefault("ctx", context))
    main_agent.generate_report.invoke({
        "ticker": "MSFT",
        "snapshot": {"price_now": 1.0, "prev_close": 1.0, "change_pct": 0.0},
        "news": [],
        "research": {"analyst": None, "filings": [{"form": "8-K", "date": "2026-10-20", "items": ["5.02"], "url": "u"}]},
        "language": "en",
    })
    assert "SEC 8-K filed 2026-10-20" in seen["ctx"]
