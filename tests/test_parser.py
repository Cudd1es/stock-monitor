import pytest
import parser
from settings import TickerRule


def fake_llm(monkeypatch, reply, seen=None):
    def ask(prompt, model=None):
        if seen is not None:
            seen.append(prompt)
        return reply
    monkeypatch.setattr(parser, "ask_llm", ask)


def test_parse_edit_returns_ops(monkeypatch):
    fake_llm(monkeypatch, '[{"op": "add", "symbol": "nvda", "alert_threshold": 3}]')
    assert parser.parse_watchlist_edit("加上 NVDA 跌 3% 提醒", []) == \
        [{"op": "add", "symbol": "NVDA", "alert_threshold": 3}]


def test_parse_edit_ignores_text_around_json(monkeypatch):
    fake_llm(monkeypatch, 'Sure:\n[{"op": "remove", "symbol": "SPY"}]\nDone')
    assert parser.parse_watchlist_edit("remove SPY", []) == [{"op": "remove", "symbol": "SPY"}]


def test_parse_edit_strips_code_fences(monkeypatch):
    fake_llm(monkeypatch, '```json\n[{"op": "update", "symbol": "SPY", "intraday": false}]\n```')
    assert parser.parse_watchlist_edit("SPY 不用盯盘", []) == [{"op": "update", "symbol": "SPY", "intraday": False}]


def test_prompt_includes_current_watchlist(monkeypatch):
    seen = []
    fake_llm(monkeypatch, "[]", seen)
    parser.parse_watchlist_edit("anything", [TickerRule("TSLA", 8.0, True, True)])
    assert "TSLA" in seen[0] and "8.0" in seen[0]
    assert "anything" in seen[0]


@pytest.mark.parametrize("raw", ["", "not json", '{"op": "add"}', '[{"symbol": "MSFT"}]', '["MSFT"]',
                                 '[{"op": "add"}]'])
def test_invalid_llm_output_raises(monkeypatch, raw):
    fake_llm(monkeypatch, raw)
    with pytest.raises(ValueError):
        parser.parse_watchlist_edit("add MSFT", [])
