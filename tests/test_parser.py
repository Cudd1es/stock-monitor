import pytest
import parser
from parser import _validate_and_fix


def test_tickers_are_normalized_and_deduped():
    cfg = _validate_and_fix({"tickers": [" msft", "MSFT", "brk.b"]})
    assert cfg["tickers"] == ["MSFT", "BRK.B"]


def test_no_tickers_raises():
    with pytest.raises(ValueError):
        _validate_and_fix({"tickers": []})


def test_percent_string_threshold():
    cfg = _validate_and_fix({"tickers": ["AAPL"], "alert_threshold": "4%"})
    assert cfg["alert_threshold"] == 4.0


def test_out_of_range_threshold_falls_back_to_default():
    cfg = _validate_and_fix({"tickers": ["AAPL"], "alert_threshold": 99})
    assert cfg["alert_threshold"] == 5.0

def test_parse_ignores_text_around_json(monkeypatch):
    fake_llm = lambda prompt, model: 'Sure! {"tickers": ["tsla"]}'
    monkeypatch.setattr(parser, "ask_llm", fake_llm)
    cfg = parser.parse_user_requirement("watch tesla")
    assert cfg["tickers"] == ["TSLA"]
