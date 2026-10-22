import pytest
import settings
from settings import TickerRule

D = settings.TickerDefaults()


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- config.yaml ---

def test_settings_defaults_when_fields_missing(tmp_path):
    s = settings.load_settings(write(tmp_path, "config.yaml", "{}"))
    assert (s.timezone, s.daily_brief, s.intraday_minutes) == ("America/Toronto", "16:10", None)
    assert (s.notify_method, s.report_language) == ("console", "zh")
    assert s.defaults == settings.TickerDefaults(5.0, True, True)
    assert (s.discord_webhook, s.discord_mention_id) == (None, None)


def test_settings_reads_schedule_defaults_and_discord(tmp_path):
    s = settings.load_settings(write(tmp_path, "config.yaml", """
schedule: {daily_brief: "16:30", intraday_minutes: 10}
notify_method: discord
defaults: {alert_threshold: 3}
discord: {webhook_url: "https://hook", mention_id: "42"}
"""))
    assert (s.daily_brief, s.intraday_minutes, s.notify_method) == ("16:30", 10, "discord")
    assert s.defaults == settings.TickerDefaults(3.0, True, True)
    assert (s.discord_webhook, s.discord_mention_id) == ("https://hook", "42")


def test_settings_intraday_only(tmp_path):
    s = settings.load_settings(write(tmp_path, "config.yaml",
                                     "schedule: {daily_brief: null, intraday_minutes: 15}"))
    assert (s.daily_brief, s.intraday_minutes) == (None, 15)


@pytest.mark.parametrize("text", [
    "schedule: {daily_brief: null, intraday_minutes: null}",
    "schedule: {daily_brief: '25:00'}",
    "schedule: {intraday_minutes: 0}",
    "schedule: {intraday_minutes: 391}",
    "notify_method: email",
    "report_language: fr",
    "defaults: {alert_treshold: 3}",
    "defaults: {alert_threshold: 0}",
    "timezone: Mars/Base",
    "timezone:",
    "schedule: '16:10'",
    "defaults: [1]",
    "discord: hook",
    "schedule: {",
    "notify_method: discord",
])
def test_invalid_settings_raise(tmp_path, text):
    with pytest.raises(ValueError):
        settings.load_settings(write(tmp_path, "config.yaml", text))


def test_missing_config_raises(tmp_path):
    with pytest.raises(ValueError):
        settings.load_settings(str(tmp_path / "config.yaml"))


# --- watchlist.yaml ---

def test_watchlist_merges_defaults(tmp_path):
    path = write(tmp_path, "w.yaml", """
tickers:
  - {symbol: msft}
  - {symbol: TSLA, alert_threshold: 8}
  - {symbol: SPY, intraday: false, research: false}
""")
    assert settings.load_watchlist(path, D) == [
        TickerRule("MSFT", 5.0, True, True),
        TickerRule("TSLA", 8.0, True, True),
        TickerRule("SPY", 5.0, False, False),
    ]


def test_empty_watchlist_is_valid(tmp_path):
    assert settings.load_watchlist(write(tmp_path, "w.yaml", "tickers: []"), D) == []


def test_missing_watchlist_raises(tmp_path):
    with pytest.raises(ValueError, match="watchlist.py"):
        settings.load_watchlist(str(tmp_path / "w.yaml"), D)


@pytest.mark.parametrize("entry", [
    "{symbol: MSFT, colour: red}",
    "{alert_threshold: 3}",
    "{symbol: MSFT, alert_threshold: 0}",
    "{symbol: MSFT, alert_threshold: true}",
    "{symbol: MSFT, alert_threshold: '8'}",
    "{symbol: MSFT, intraday: 'no'}",
    "MSFT",
])
def test_invalid_entry_raises(tmp_path, entry):
    with pytest.raises(ValueError):
        settings.load_watchlist(write(tmp_path, "w.yaml", f"tickers: [{entry}]"), D)


def test_tickers_must_be_a_list(tmp_path):
    with pytest.raises(ValueError):
        settings.load_watchlist(write(tmp_path, "w.yaml", "tickers: MSFT"), D)


def test_duplicate_symbol_raises(tmp_path):
    with pytest.raises(ValueError, match="MSFT"):
        settings.load_watchlist(write(tmp_path, "w.yaml", "tickers: [{symbol: MSFT}, {symbol: msft}]"), D)


def test_normalize_symbol_keeps_share_class():
    assert settings.normalize_symbol(" brk-b ") == "BRK-B"
    assert settings.normalize_symbol("brk.b") == "BRK.B"


def test_normalize_symbol_rejects_empty():
    with pytest.raises(ValueError):
        settings.normalize_symbol(" $ ")


def test_dump_then_load_round_trips_and_writes_only_overrides(tmp_path):
    path = tmp_path / "state" / "w.yaml"
    rules = [TickerRule("MSFT", 5.0, True, True), TickerRule("TSLA", 8.0, True, False)]
    settings.dump_watchlist(rules, str(path), D)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(settings.HEADER)
    assert "alert_threshold: 5" not in text and "research: false" in text
    assert settings.load_watchlist(str(path), D) == rules


# --- hot reload ---

GOOD = "tickers: [{symbol: MSFT}]"
BAD = "tickers: [{symbol: MSFT, colour: red}]"


def test_reloader_picks_up_edits(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text(GOOD)
    r = settings.WatchlistReloader(str(path), D)
    assert [t.symbol for t in r.get()] == ["MSFT"]
    path.write_text("tickers: [{symbol: MSFT}, {symbol: NVDA}]")
    assert [t.symbol for t in r.get()] == ["MSFT", "NVDA"]


def test_reloader_keeps_last_good_and_reports_once(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text(GOOD)
    errors = []
    r = settings.WatchlistReloader(str(path), D, on_error=errors.append)
    good = r.get()
    path.write_text(BAD)
    assert r.get() == good
    assert r.get() == good
    assert len(errors) == 1
    assert "colour" in errors[0]


def test_reloader_reports_again_after_recovering(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text(GOOD)
    errors = []
    r = settings.WatchlistReloader(str(path), D, on_error=errors.append)
    r.get()
    for text in (BAD, GOOD, BAD):
        path.write_text(text)
        r.get()
    assert len(errors) == 2


def test_reloader_first_load_failure_raises(tmp_path):
    with pytest.raises(ValueError):
        settings.WatchlistReloader(str(tmp_path / "missing.yaml"), D).get()


def test_settings_repr_hides_discord_secrets(tmp_path):
    s = settings.load_settings(write(tmp_path, "config.yaml",
                                     'notify_method: discord\ndiscord: {webhook_url: "https://hook/secret", mention_id: "42"}'))
    assert "secret" not in repr(s) and "42" not in repr(s)
    assert s.discord_webhook == "https://hook/secret"
