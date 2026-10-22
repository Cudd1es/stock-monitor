import pytest
import parser
import settings
import ticker_checker
import watchlist
from settings import TickerRule

D = settings.TickerDefaults()


def R(symbol, **kw):
    fields = {"alert_threshold": D.alert_threshold, "intraday": D.intraday, "research": D.research, **kw}
    return TickerRule(symbol, **fields)


# --- apply_ops ---

def test_apply_add_update_remove():
    rules = [R("MSFT"), R("SPY")]
    out = watchlist.apply_ops(rules, [
        {"op": "add", "symbol": "NVDA", "alert_threshold": 3},
        {"op": "update", "symbol": "SPY", "intraday": False},
        {"op": "remove", "symbol": "MSFT"},
    ], D)
    assert out == [R("SPY", intraday=False), R("NVDA", alert_threshold=3.0)]
    assert rules == [R("MSFT"), R("SPY")]


def test_update_keeps_other_overrides():
    out = watchlist.apply_ops([R("TSLA", alert_threshold=8.0)], [{"op": "update", "symbol": "TSLA", "research": False}], D)
    assert out == [R("TSLA", alert_threshold=8.0, research=False)]


def test_removing_last_ticker_leaves_empty_watchlist():
    assert watchlist.apply_ops([R("MSFT")], [{"op": "remove", "symbol": "MSFT"}], D) == []


@pytest.mark.parametrize("op", [
    {"op": "add", "symbol": "MSFT"},
    {"op": "update", "symbol": "AAPL", "intraday": False},
    {"op": "remove", "symbol": "AAPL"},
    {"op": "rename", "symbol": "MSFT"},
    {"op": "update", "symbol": "MSFT", "colour": "red"},
    {"op": "update", "symbol": "MSFT", "alert_threshold": -1},
    {"op": "update", "symbol": "MSFT", "intraday": "no"},
])
def test_apply_rejects_bad_ops(op):
    with pytest.raises(ValueError):
        watchlist.apply_ops([R("MSFT")], [op], D)


# --- CLI ---

@pytest.fixture
def files(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("notify_method: console\n")
    return str(config), tmp_path / "watchlist.yaml"


def _stub(monkeypatch, ops=None, error=None, price=100.0):
    def parse(text, current, model=None):
        if error:
            raise ValueError(error)
        return ops
    monkeypatch.setattr(parser, "parse_watchlist_edit", parse)
    monkeypatch.setattr(ticker_checker, "get_previous_close", lambda t: price)


def _run(config, wl, *extra, answer="y"):
    return watchlist.main(["add NVDA", "--config", config, "--watchlist", str(wl), *extra], input_fn=lambda _: answer)


def test_cli_writes_after_confirm(monkeypatch, files):
    config, wl = files
    settings.dump_watchlist([R("MSFT")], str(wl), D)
    _stub(monkeypatch, ops=[{"op": "add", "symbol": "NVDA"}])
    assert _run(config, wl) == 0
    assert [t.symbol for t in settings.load_watchlist(str(wl), D)] == ["MSFT", "NVDA"]


def test_cli_creates_watchlist_when_missing(monkeypatch, files):
    config, wl = files
    _stub(monkeypatch, ops=[{"op": "add", "symbol": "NVDA"}])
    assert _run(config, wl) == 0
    assert settings.load_watchlist(str(wl), D) == [R("NVDA")]


@pytest.mark.parametrize("case", ["declined", "llm_invalid", "unknown_symbol", "bad_op"])
def test_cli_leaves_file_untouched(monkeypatch, files, case):
    config, wl = files
    settings.dump_watchlist([R("MSFT")], str(wl), D)
    before = wl.read_bytes()
    if case == "llm_invalid":
        _stub(monkeypatch, error="LLM did not return a JSON array")
    elif case == "unknown_symbol":
        _stub(monkeypatch, ops=[{"op": "add", "symbol": "NVDAX"}], price=None)
    elif case == "bad_op":
        _stub(monkeypatch, ops=[{"op": "remove", "symbol": "AAPL"}])
    else:
        _stub(monkeypatch, ops=[{"op": "add", "symbol": "NVDA"}])
    code = _run(config, wl, answer="n")
    assert code == (0 if case == "declined" else 1)
    assert wl.read_bytes() == before


def test_cli_no_change_does_not_prompt(monkeypatch, files, capsys):
    config, wl = files
    settings.dump_watchlist([R("MSFT")], str(wl), D)
    _stub(monkeypatch, ops=[])

    def no_prompt(_):
        raise AssertionError("should not ask")

    assert watchlist.main(["x", "--config", config, "--watchlist", str(wl)], input_fn=no_prompt) == 0
    assert "no change" in capsys.readouterr().out


def test_cli_yes_skips_prompt(monkeypatch, files):
    config, wl = files
    _stub(monkeypatch, ops=[{"op": "add", "symbol": "NVDA"}])

    def no_prompt(_):
        raise AssertionError("should not ask")

    assert watchlist.main(["add NVDA", "--config", config, "--watchlist", str(wl), "--yes"], input_fn=no_prompt) == 0
    assert settings.load_watchlist(str(wl), D) == [R("NVDA")]


def test_cli_shows_diff(monkeypatch, files, capsys):
    config, wl = files
    settings.dump_watchlist([R("MSFT"), R("SPY")], str(wl), D)
    _stub(monkeypatch, ops=[{"op": "add", "symbol": "NVDA", "alert_threshold": 3},
                            {"op": "update", "symbol": "SPY", "intraday": False},
                            {"op": "remove", "symbol": "MSFT"}])
    _run(config, wl, answer="n")
    out = capsys.readouterr().out
    assert "+ NVDA" in out and "- MSFT" in out and "~ SPY intraday: True → False" in out


def test_cli_list_prints_merged_rules(files, capsys):
    config, wl = files
    settings.dump_watchlist([R("MSFT"), R("TSLA", alert_threshold=8.0)], str(wl), D)
    assert watchlist.main(["list", "--config", config, "--watchlist", str(wl)]) == 0
    out = capsys.readouterr().out
    assert "TSLA" in out and "8.0" in out and "5.0" in out


def test_cli_list_without_watchlist_hints(files, capsys):
    config, wl = files
    assert watchlist.main(["list", "--config", config, "--watchlist", str(wl)]) == 0
    assert "watchlist.py" in capsys.readouterr().out
