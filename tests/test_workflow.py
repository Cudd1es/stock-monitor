import llm_interaction
import main_workflow


def test_brief_falls_back_to_raw_data_when_llm_fails(monkeypatch):
    monkeypatch.setattr(llm_interaction, "ask_llm", lambda prompt, model=None: "")
    state = {
        "rules": {"alert_threshold": 5.0, "tickers": ["MSFT"]},
        "snapshot": [{"ticker": "MSFT", "price_now": 100.0, "prev_close": 99.0, "change_pct": 1.01}],
    }
    out = main_workflow.node_brief(state)
    assert "- MSFT: now=100.00" in out["brief"]


def test_brief_works_without_news(monkeypatch):
    monkeypatch.setattr(llm_interaction, "ask_llm", lambda prompt, model=None: "report")
    state = {"rules": {"alert_threshold": 5.0, "tickers": ["MSFT"]}, "snapshot": []}
    assert main_workflow.node_brief(state)["brief"] == "report"


def _rules(**kw):
    return {"alert_threshold": 5.0, "tickers": ["MSFT"], "timezone": "America/Toronto", **kw}


def test_judge_dedups_alerts_across_runs(tmp_path):
    store = str(tmp_path / "alerts.json")
    snapshot = [{"ticker": "MSFT", "price_now": 90.0, "prev_close": 100.0, "change_pct": -10.0}]
    first = main_workflow.node_judge({"rules": _rules(), "snapshot": snapshot, "alert_store": store})
    second = main_workflow.node_judge({"rules": _rules(), "snapshot": snapshot, "alert_store": store})
    assert len(first["alerts"]) == 1
    assert second["alerts"] == []


def test_alerts_only_run_stops_after_judge_when_nothing_moved():
    state = {"rules": _rules(), "snapshot": [], "alerts": [], "alerts_only": True}
    assert main_workflow.node_supervisor(state)["plan"] == []


def test_alerts_only_run_sends_brief_when_alert_fires():
    state = {"rules": _rules(), "snapshot": [], "alerts": ["MSFT moved -10%"], "alerts_only": True}
    assert main_workflow.node_supervisor(state)["plan"] == ["news", "brief", "notify"]


def test_judge_runs_before_news():
    state = {"rules": _rules(), "snapshot": []}
    assert main_workflow.node_supervisor(state)["plan"][0] == "judge"
