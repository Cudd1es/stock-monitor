from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import monitor

TZ = ZoneInfo("America/Toronto")


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=TZ)  # 2026-10-21 is a Wednesday


def daily(hhmm="16:30"):
    return {"schedule_mode": "daily", "schedule_time": hhmm, "interval_minutes": 0}


def interval(minutes):
    return {"schedule_mode": "interval", "schedule_time": "16:30", "interval_minutes": minutes}


# --- scheduling ---

def test_daily_runs_later_today():
    assert monitor.next_run_time(daily(), at(21, "10:00")) == at(21, "16:30")


def test_daily_after_schedule_moves_to_tomorrow():
    assert monitor.next_run_time(daily(), at(21, "16:30")) == at(22, "16:30")


def test_daily_skips_weekend():
    # Friday evening -> Monday
    assert monitor.next_run_time(daily(), at(23, "17:00")) == at(26, "16:30")


def test_interval_aligns_to_session_open():
    assert monitor.next_run_time(interval(10), at(21, "10:03")) == at(21, "10:10")


def test_interval_before_open_waits_for_open():
    assert monitor.next_run_time(interval(10), at(21, "08:00")) == at(21, "09:30")


def test_interval_last_tick_is_market_close():
    assert monitor.next_run_time(interval(30), at(21, "15:45")) == at(21, "16:00")


def test_interval_after_close_moves_to_next_open():
    assert monitor.next_run_time(interval(10), at(23, "16:00")) == at(26, "09:30")


def test_interval_longer_than_session_runs_at_open_and_close():
    assert monitor.next_run_time(interval(600), at(21, "09:30")) == at(21, "16:00")


# --- alert dedup ---

def row(ticker, pct):
    return {"ticker": ticker, "price_now": 100.0, "change_pct": pct}


def test_same_move_alerts_once_per_day(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    assert store.filter_new([row("MSFT", -6.0)], 5.0, "2026-10-21") == [row("MSFT", -6.0)]
    assert store.filter_new([row("MSFT", -7.0)], 5.0, "2026-10-21") == []


def test_move_crossing_next_threshold_step_alerts_again(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    store.filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert store.filter_new([row("MSFT", 10.5)], 5.0, "2026-10-21") == [row("MSFT", 10.5)]


def test_opposite_direction_alerts_again(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    store.filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert store.filter_new([row("MSFT", -6.0)], 5.0, "2026-10-21") == [row("MSFT", -6.0)]


def test_new_day_resets_dedup(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    store.filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert store.filter_new([row("MSFT", 6.0)], 5.0, "2026-10-22") == [row("MSFT", 6.0)]


def test_dedup_survives_restart(tmp_path):
    path = tmp_path / "state" / "alerts.json"
    monitor.AlertStore(path).filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert monitor.AlertStore(path).filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21") == []


def test_corrupt_store_starts_empty(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("not json")
    assert monitor.AlertStore(path).filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21") == [row("MSFT", 6.0)]


# --- loop ---

def test_run_loop_sleeps_until_each_scheduled_run():
    clock = [at(21, "10:03")]
    slept, ran = [], []

    def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] = clock[0] + timedelta(seconds=seconds)

    monitor.run_loop(
        interval(10), lambda: ran.append(clock[0]),
        now=lambda: clock[0], sleep=fake_sleep, max_runs=2,
    )
    assert ran == [at(21, "10:10"), at(21, "10:20")]


def test_run_loop_keeps_going_when_a_run_fails():
    clock = [at(21, "10:03")]
    ran = []

    def fake_sleep(seconds):
        clock[0] = clock[0] + timedelta(seconds=seconds)

    def tick():
        ran.append(clock[0])
        raise RuntimeError("yfinance down")

    monitor.run_loop(interval(10), tick, now=lambda: clock[0], sleep=fake_sleep, max_runs=2)
    assert len(ran) == 2
