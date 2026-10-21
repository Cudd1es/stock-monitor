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
