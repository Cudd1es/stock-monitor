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

def test_generate_brief_fills_every_template_variable(monkeypatch):
    seen = {}

    def fake_llm(prompt, model):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(core.llm_interaction, "ask_llm", fake_llm)
    assert core.generate_brief(["MSFT"], "ctx", "en") == "ok"
    assert "MSFT" in seen["prompt"]
    assert "{{" not in seen["prompt"]

def test_missing_config_returns_none(tmp_path):
    assert core.load_discord_config(str(tmp_path / "nope.yaml")) == (None, None)


def test_reads_discord_section(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text('discord:\n  webhook_url: "https://x"\n  mention_id: "42"\n')
    assert core.load_discord_config(str(cfg)) == ("https://x", "42")


def test_no_discord_section_returns_none(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("timezone: America/Toronto\n")
    assert core.load_discord_config(str(cfg)) == (None, None)


def test_empty_config_returns_none(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("")
    assert core.load_discord_config(str(cfg)) == (None, None)