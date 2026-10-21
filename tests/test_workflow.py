import core
import llm_interaction
import main_workflow


def test_brief_falls_back_to_raw_data_when_llm_fails(monkeypatch):
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(llm_interaction, "ask_llm", lambda prompt, model=None: "")
    state = {
        "rules": {"alert_threshold": 5.0, "tickers": ["MSFT"]},
        "snapshot": [{"ticker": "MSFT", "price_now": 100.0, "prev_close": 99.0, "change_pct": 1.01}],
    }
    out = main_workflow.node_brief(state)
    assert "- MSFT: now=100.00" in out["brief"]


def test_brief_works_without_news(monkeypatch):
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(llm_interaction, "ask_llm", lambda prompt, model=None: "report")
    state = {"rules": {"alert_threshold": 5.0, "tickers": ["MSFT"]}, "snapshot": []}
    assert main_workflow.node_brief(state)["brief"] == "report"


def _rules(**kw):
    return {"alert_threshold": 5.0, "tickers": ["MSFT"], "timezone": "America/Toronto", **kw}


def test_judge_dedups_alerts_across_runs(tmp_path):
    store = str(tmp_path / "alerts.json")
    snapshot = [{"ticker": "MSFT", "price_now": 90.0, "prev_close": 100.0, "change_pct": -10.0}]
    first = main_workflow.node_judge({"rules": _rules(), "snapshot": snapshot, "alert_store": store})
    main_workflow.record_sent_alerts(first)
    second = main_workflow.node_judge({"rules": _rules(), "snapshot": snapshot, "alert_store": store})
    assert len(first["alerts"]) == 1
    assert second["alerts"] == []


def test_alerts_only_run_stops_after_judge_when_nothing_moved():
    state = {"rules": _rules(), "snapshot": [], "alerts": [], "alert_rows": [], "alerts_only": True}
    assert main_workflow.node_supervisor(state)["plan"] == []


def test_alerts_only_run_sends_brief_when_alert_fires():
    state = {"rules": _rules(), "snapshot": [], "alerts": ["MSFT moved -10%"],
             "alert_rows": [{"ticker": "MSFT", "change_pct": -10.0}], "alerts_only": True}
    assert main_workflow.node_supervisor(state)["plan"] == ["news", "research", "brief", "notify"]


def test_judge_runs_before_news():
    state = {"rules": _rules(), "snapshot": []}
    assert main_workflow.node_supervisor(state)["plan"][0] == "judge"


def test_brief_compares_against_market(monkeypatch):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [
        {"ticker": "SPY", "price_now": 500.0, "prev_close": 495.0, "change_pct": 1.01}])
    monkeypatch.setattr(core, "generate_brief", lambda tickers, context, language, kind="daily": seen.setdefault("ctx", context))
    main_workflow.node_brief({"rules": _rules(), "snapshot": []})
    assert seen["ctx"].startswith("Market: SPY 1.01%")


SNAPSHOT = [
    {"ticker": "MSFT", "price_now": 90.0, "prev_close": 100.0, "change_pct": -10.0},
    {"ticker": "NVDA", "price_now": 101.0, "prev_close": 100.0, "change_pct": 1.0},
]


def _alerted_state(tmp_path, **kw):
    state = {"rules": _rules(tickers=["MSFT", "NVDA"], notify_method="discord"), "snapshot": SNAPSHOT,
             "alert_store": str(tmp_path / "alerts.json"), **kw}
    return main_workflow.node_judge(state)


def _capture_notify(monkeypatch, ok=True):
    sent = []
    monkeypatch.setattr(core, "load_discord_config", lambda: ("https://hook", None))
    monkeypatch.setattr(main_workflow.notifier, "notify",
                        lambda method, message, discord_webhook, mention_id=None: sent.append(message) or ok)
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
    main_workflow.node_notify({**_alerted_state(tmp_path, alerts_only=True), "brief": "why it moved"})
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
    main_workflow.node_news(_alerted_state(tmp_path, alerts_only=True))
    assert fetched == ["MSFT"]


def test_intraday_brief_only_for_alerted_tickers(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])

    def fake_brief(tickers, context, language, kind="daily"):
        seen.update(tickers=tickers, context=context, kind=kind)
        return "b"

    monkeypatch.setattr(core, "generate_brief", fake_brief)
    main_workflow.node_brief(_alerted_state(tmp_path, alerts_only=True))
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


def test_research_only_for_alerted_tickers_intraday(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(main_workflow.analyst, "fetch_analyst_view", lambda t: fetched.append(("a", t)) or {})
    monkeypatch.setattr(main_workflow.filings, "fetch_recent_filings", lambda t: fetched.append(("f", t)) or [])
    monkeypatch.setattr(main_workflow.ticker_checker, "get_relative_volume",
                        lambda t, timezone: fetched.append(("v", t)) or None)
    out = main_workflow.node_research(_alerted_state(tmp_path, alerts_only=True))
    assert fetched == [("a", "MSFT"), ("f", "MSFT"), ("v", "MSFT")]
    assert out["research"] == {"MSFT": {"analyst": {}, "filings": [], "volume": None}}


def test_brief_passes_research_into_context(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(core, "fetch_market", lambda timezone="America/Toronto": [])
    monkeypatch.setattr(core, "generate_brief",
                        lambda tickers, context, language, kind="daily": seen.setdefault("ctx", context))
    research = {"MSFT": {"analyst": None, "filings": [{"form": "8-K", "date": "2026-10-20", "items": ["5.02"], "url": "u"}]}}
    main_workflow.node_brief({**_alerted_state(tmp_path), "research": research})
    assert "SEC 8-K filed 2026-10-20" in seen["ctx"]
