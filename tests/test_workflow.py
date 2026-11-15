import core
import llm_interaction
import main_workflow
from settings import Settings, TickerRule

SETTINGS = Settings(intraday_minutes=10, notify_method="discord", discord_webhook="https://hook")


def rule(symbol, alert_threshold=5.0, intraday=True, research=True):
    return TickerRule(symbol, alert_threshold, intraday, research)


def _state(tickers=None, kind="daily", **kw):
    return {"settings": SETTINGS, "tickers": tickers or [rule("MSFT")], "kind": kind, **kw}


def test_brief_falls_back_to_raw_data_when_llm_fails(monkeypatch):
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(llm_interaction, "ask_llm", lambda prompt, model=None: "")
    state = _state(snapshot=[{"ticker": "MSFT", "price_now": 100.0, "prev_close": 99.0, "change_pct": 1.01}])
    out = main_workflow.node_brief(state)
    assert "- MSFT: now=100.00" in out["brief"]


def test_brief_works_without_news(monkeypatch):
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(llm_interaction, "ask_llm", lambda prompt, model=None: "report")
    assert main_workflow.node_brief(_state(snapshot=[]))["brief"] == "report"


def test_judge_dedups_alerts_across_runs(tmp_path):
    store = str(tmp_path / "alerts.json")
    snapshot = [{"ticker": "MSFT", "price_now": 90.0, "prev_close": 100.0, "change_pct": -10.0}]
    first = main_workflow.node_judge(_state(snapshot=snapshot, alert_store=store))
    main_workflow.record_sent_alerts(first)
    second = main_workflow.node_judge(_state(snapshot=snapshot, alert_store=store))
    assert len(first["alerts"]) == 1
    assert second["alerts"] == []


def test_judge_uses_each_tickers_threshold():
    snapshot = [{"ticker": "MSFT", "price_now": 94.0, "prev_close": 100.0, "change_pct": -6.0},
                {"ticker": "TSLA", "price_now": 94.0, "prev_close": 100.0, "change_pct": -6.0}]
    out = main_workflow.node_judge(_state([rule("MSFT"), rule("TSLA", alert_threshold=8.0)], snapshot=snapshot))
    assert [r["ticker"] for r in out["alert_rows"]] == ["MSFT"]


def test_intraday_run_stops_after_judge_when_nothing_moved():
    state = _state(kind="intraday", snapshot=[], alerts=[], alert_rows=[])
    assert main_workflow.node_supervisor(state)["plan"] == []


def test_intraday_run_sends_brief_when_alert_fires():
    state = _state(kind="intraday", snapshot=[], alerts=["MSFT moved -10%"],
                   alert_rows=[{"ticker": "MSFT", "change_pct": -10.0}])
    assert main_workflow.node_supervisor(state)["plan"] == ["news", "research", "brief", "notify"]


def test_daily_run_starts_with_price_then_judge():
    assert main_workflow.node_supervisor(_state())["plan"][:2] == ["price", "judge"]


def test_brief_compares_against_market(monkeypatch):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [
        {"ticker": "SPY", "price_now": 500.0, "prev_close": 495.0, "change_pct": 1.01}])
    monkeypatch.setattr(core, "generate_brief", lambda tickers, context, language, kind="daily": seen.setdefault("ctx", context))
    main_workflow.node_brief(_state(snapshot=[]))
    assert seen["ctx"].startswith("Market: SPY 1.01%")


def _record_snapshots(monkeypatch):
    fetched = []
    monkeypatch.setattr(main_workflow.time, "sleep", lambda s: None)
    monkeypatch.setattr(core, "fetch_snapshot", lambda t, timezone="America/Toronto": fetched.append(t) or
                        {"ticker": t, "price_now": 100.0, "prev_close": 100.0, "change_pct": 0.0})
    return fetched


def test_intraday_prices_only_intraday_tickers(monkeypatch):
    fetched = _record_snapshots(monkeypatch)
    tickers = [rule("MSFT"), rule("SPY", intraday=False)]
    main_workflow.node_price(_state(tickers, kind="intraday"))
    assert fetched == ["MSFT"]
    fetched.clear()
    main_workflow.node_price(_state(tickers, kind="daily"))
    assert fetched == ["MSFT", "SPY"]


def test_intraday_with_no_intraday_tickers_stays_quiet(monkeypatch, tmp_path):
    fetched = _record_snapshots(monkeypatch)
    sent = _capture_notify(monkeypatch)
    app = main_workflow.build_graph().compile()
    out = main_workflow.run_once(app, SETTINGS, [rule("SPY", intraday=False)], "intraday", str(tmp_path / "a.json"))
    assert fetched == [] and sent == []
    assert out["alerts"] == []


SNAPSHOT = [
    {"ticker": "MSFT", "price_now": 90.0, "prev_close": 100.0, "change_pct": -10.0},
    {"ticker": "NVDA", "price_now": 101.0, "prev_close": 100.0, "change_pct": 1.0},
]


def _alerted_state(tmp_path, tickers=None, **kw):
    state = _state(tickers or [rule("MSFT"), rule("NVDA")], snapshot=SNAPSHOT,
                   alert_store=str(tmp_path / "alerts.json"), **kw)
    return main_workflow.node_judge(state)


def _capture_notify(monkeypatch, ok=True):
    sent = []

    def fake_notify(method, message, discord_webhook, mention_id=None):
        assert (method, discord_webhook) == ("discord", "https://hook")
        sent.append(message)
        return ok

    monkeypatch.setattr(main_workflow.notifier, "notify", fake_notify)
    return sent


def test_alert_is_recorded_only_after_successful_send(monkeypatch, tmp_path):
    _capture_notify(monkeypatch, ok=True)
    main_workflow.node_notify({**_alerted_state(tmp_path), "brief": "b"})
    assert _alerted_state(tmp_path)["alerts"] == []


def test_failed_send_leaves_alert_to_retry(monkeypatch, tmp_path):
    _capture_notify(monkeypatch, ok=False)
    out = main_workflow.node_notify({**_alerted_state(tmp_path), "brief": "b"})
    assert out["_notified"] is True  # this run is over either way
    assert len(_alerted_state(tmp_path)["alerts"]) == 1


def test_intraday_alert_is_one_message_with_intraday_title(monkeypatch, tmp_path):
    sent = _capture_notify(monkeypatch)
    main_workflow.node_notify({**_alerted_state(tmp_path, kind="intraday"), "brief": "why it moved"})
    assert len(sent) == 1
    assert sent[0].startswith("[INTRADAY ALERT]\nMSFT moved -10.00%")
    assert sent[0].endswith("why it moved")
    assert "DAILY BRIEF" not in sent[0]


def test_daily_run_keeps_alert_and_daily_brief(monkeypatch, tmp_path):
    sent = _capture_notify(monkeypatch)
    main_workflow.node_notify({**_alerted_state(tmp_path), "brief": "b"})
    assert [m.split("\n")[0] for m in sent] == ["[ALERT]", "[DAILY BRIEF]"]


def test_intraday_news_only_for_alerted_tickers(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(main_workflow.news_collector, "fetch_news_headlines",
                        lambda t, k: fetched.append(t) or [])
    main_workflow.node_news(_alerted_state(tmp_path, kind="intraday"))
    assert fetched == ["MSFT"]


def test_intraday_brief_only_for_alerted_tickers(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])

    def fake_brief(tickers, context, language, kind="daily"):
        seen.update(tickers=tickers, context=context, kind=kind)
        return "b"

    monkeypatch.setattr(core, "generate_brief", fake_brief)
    main_workflow.node_brief(_alerted_state(tmp_path, kind="intraday"))
    assert seen["tickers"] == ["MSFT"]
    assert "NVDA" not in seen["context"]
    assert seen["kind"] == "intraday"


def test_daily_brief_covers_every_ticker(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(core, "generate_brief",
                        lambda tickers, context, language, kind="daily": seen.update(tickers=tickers, kind=kind) or "b")
    main_workflow.node_brief(_alerted_state(tmp_path))
    assert seen == {"tickers": ["MSFT", "NVDA"], "kind": "daily"}


def _record_research(monkeypatch):
    fetched = []
    monkeypatch.setattr(main_workflow.analyst, "fetch_analyst_view", lambda t: fetched.append(("a", t)) or {})
    monkeypatch.setattr(main_workflow.filings, "fetch_recent_filings", lambda t: fetched.append(("f", t)) or [])
    monkeypatch.setattr(main_workflow.ticker_checker, "get_relative_volume",
                        lambda t, timezone: fetched.append(("v", t)) or None)
    return fetched


def test_research_only_for_alerted_tickers_intraday(monkeypatch, tmp_path):
    fetched = _record_research(monkeypatch)
    out = main_workflow.node_research(_alerted_state(tmp_path, kind="intraday"))
    assert fetched == [("a", "MSFT"), ("f", "MSFT"), ("v", "MSFT")]
    assert out["research"] == {"MSFT": {"analyst": {}, "filings": [], "volume": None}}


def test_research_false_skips_news_and_research(monkeypatch, tmp_path):
    fetched = _record_research(monkeypatch)
    news = []
    monkeypatch.setattr(main_workflow.news_collector, "fetch_news_headlines", lambda t, k: news.append(t) or [])
    state = _alerted_state(tmp_path, tickers=[rule("MSFT"), rule("NVDA", research=False)])
    main_workflow.node_news(state)
    main_workflow.node_research(state)
    assert news == ["MSFT"]
    assert {t for _, t in fetched} == {"MSFT"}
    assert "NVDA" not in state["news_map"]


def test_brief_passes_research_into_context(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(core, "generate_brief",
                        lambda tickers, context, language, kind="daily": seen.setdefault("ctx", context))
    research = {"MSFT": {"analyst": None, "filings": [{"form": "8-K", "date": "2026-10-20", "items": ["5.02"], "url": "u"}]}}
    main_workflow.node_brief({**_alerted_state(tmp_path), "research": research})
    assert "SEC 8-K filed 2026-10-20" in seen["ctx"]


class FakeReloader:
    def __init__(self, tickers):
        self.tickers = tickers

    def get(self):
        return self.tickers


class FakeApp:
    def __init__(self):
        self.calls = []

    def invoke(self, init, config):
        self.calls.append(init)
        return init


def test_empty_watchlist_tick_is_skipped(capsys):
    app = FakeApp()
    main_workflow.tick(app, SETTINGS, FakeReloader([]), "daily")
    assert app.calls == []
    assert "[MONITOR] watchlist is empty" in capsys.readouterr().out


def test_tick_uses_alert_store_only_for_intraday():
    app = FakeApp()
    main_workflow.tick(app, SETTINGS, FakeReloader([rule("MSFT")]), "intraday")
    main_workflow.tick(app, SETTINGS, FakeReloader([rule("MSFT")]), "daily")
    assert [c["kind"] for c in app.calls] == ["intraday", "daily"]
    assert app.calls[0]["alert_store"] == main_workflow.ALERT_STORE_PATH
    assert "alert_store" not in app.calls[1]
