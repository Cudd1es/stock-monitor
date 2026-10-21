from datetime import date, datetime, timedelta
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


def send(store, rows, threshold, day):
    """What the workflow does: pick new alerts, then record them once they were delivered."""
    fresh = store.filter_new(rows, threshold, day)
    store.record(fresh, threshold, day)
    return fresh


def test_unsent_alert_is_offered_again(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    store.filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21")  # notification failed: never recorded
    assert store.filter_new([row("MSFT", 6.0)], 5.0, "2026-10-21") == [row("MSFT", 6.0)]


def test_same_move_alerts_once_per_day(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    assert send(store, [row("MSFT", -6.0)], 5.0, "2026-10-21") == [row("MSFT", -6.0)]
    assert send(store, [row("MSFT", -7.0)], 5.0, "2026-10-21") == []


def test_move_crossing_next_threshold_step_alerts_again(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    send(store, [row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert send(store, [row("MSFT", 10.5)], 5.0, "2026-10-21") == [row("MSFT", 10.5)]


def test_opposite_direction_alerts_again(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    send(store, [row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert send(store, [row("MSFT", -6.0)], 5.0, "2026-10-21") == [row("MSFT", -6.0)]


def test_new_day_resets_dedup(tmp_path):
    store = monitor.AlertStore(tmp_path / "alerts.json")
    send(store, [row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert send(store, [row("MSFT", 6.0)], 5.0, "2026-10-22") == [row("MSFT", 6.0)]


def test_dedup_survives_restart(tmp_path):
    path = tmp_path / "state" / "alerts.json"
    send(monitor.AlertStore(path), [row("MSFT", 6.0)], 5.0, "2026-10-21")
    assert send(monitor.AlertStore(path), [row("MSFT", 6.0)], 5.0, "2026-10-21") == []


def test_corrupt_store_starts_empty(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("not json")
    assert send(monitor.AlertStore(path), [row("MSFT", 6.0)], 5.0, "2026-10-21") == [row("MSFT", 6.0)]


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


# --- NYSE holidays ---

def test_nyse_holidays_2026():
    assert monitor.nyse_holidays(2026) == {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
        date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    }


def test_nyse_holidays_2027_weekend_observance():
    holidays = monitor.nyse_holidays(2027)
    assert date(2027, 3, 26) in holidays   # Good Friday (Easter 2027-03-28)
    assert date(2027, 6, 18) in holidays   # Juneteenth on Saturday -> Friday
    assert date(2027, 7, 5) in holidays    # July 4th on Sunday -> Monday
    assert date(2027, 12, 24) in holidays  # Christmas on Saturday -> Friday


def test_saturday_new_year_is_not_observed_on_previous_friday():
    # 2028-01-01 is a Saturday; NYSE stays open on Friday 2027-12-31
    assert monitor.is_trading_day(date(2027, 12, 31))
    assert date(2027, 12, 31) not in monitor.nyse_holidays(2028)


def test_daily_skips_thanksgiving():
    # Wed 2026-11-25 evening -> Thanksgiving Thu 11-26 closed -> Fri 11-27
    assert monitor.next_run_time(daily(), datetime(2026, 11, 25, 17, 0, tzinfo=TZ)) == \
        datetime(2026, 11, 27, 16, 30, tzinfo=TZ)


def test_interval_skips_good_friday():
    # Thu 2026-04-02 after close -> Good Friday 04-03 closed -> Mon 04-06 open
    assert monitor.next_run_time(interval(10), datetime(2026, 4, 2, 16, 0, tzinfo=TZ)) == \
        datetime(2026, 4, 6, 9, 30, tzinfo=TZ)
