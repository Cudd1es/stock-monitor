from datetime import date, datetime
from types import SimpleNamespace
import pandas as pd
import analyst

TODAY = date(2026, 10, 21)


def fake_ticker(**overrides):
    ud = pd.DataFrame(
        [
            {"Firm": "Cantor Fitzgerald", "ToGrade": "Overweight", "FromGrade": "Overweight", "Action": "main",
             "priceTargetAction": "Raises", "currentPriceTarget": 400.0, "priorPriceTarget": 350.0},
            {"Firm": "Rosenblatt", "ToGrade": "Buy", "FromGrade": "Neutral", "Action": "up",
             "priceTargetAction": "Raises", "currentPriceTarget": 390.0, "priorPriceTarget": 0.0},
            {"Firm": "Old Firm", "ToGrade": "Sell", "FromGrade": "Hold", "Action": "down",
             "priceTargetAction": "Lowers", "currentPriceTarget": 100.0, "priorPriceTarget": 150.0},
        ],
        index=pd.DatetimeIndex([datetime(2026, 10, 20, 10, 34), datetime(2026, 9, 29, 12, 14),
                                datetime(2026, 8, 1, 9, 0)], name="GradeDate"),
    )
    attrs = {
        "upgrades_downgrades": ud,
        "recommendations": pd.DataFrame([{"period": "0m", "strongBuy": 10, "buy": 49, "hold": 2, "sell": 1, "strongSell": 0},
                                         {"period": "-1m", "strongBuy": 9, "buy": 48, "hold": 2, "sell": 1, "strongSell": 0}]),
        "analyst_price_targets": {"current": 221.33, "high": 515.0, "low": 180.0, "mean": 328.71695, "median": 315.0},
        "calendar": {"Earnings Date": [date(2026, 11, 17)], "Earnings High": 2.7, "Earnings Low": 2.34,
                     "Earnings Average": 2.55, "Revenue Average": 65_000_000_000},
    }
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


def view(monkeypatch, **overrides):
    monkeypatch.setattr(analyst.yf, "Ticker", lambda t: fake_ticker(**overrides))
    return analyst.fetch_analyst_view("NVDA", today=TODAY)


def test_consensus_counts(monkeypatch):
    assert view(monkeypatch)["consensus"] == {"strong_buy": 10, "buy": 49, "hold": 2, "sell": 1, "strong_sell": 0}


def test_price_target(monkeypatch):
    assert view(monkeypatch)["price_target"] == {"mean": 328.72, "median": 315.0, "low": 180.0, "high": 515.0}


def test_rating_changes_recent_only_newest_first(monkeypatch):
    changes = view(monkeypatch)["rating_changes"]
    assert [c["firm"] for c in changes] == ["Cantor Fitzgerald", "Rosenblatt"]
    assert changes[0] == {"date": "2026-10-20", "firm": "Cantor Fitzgerald", "action": "maintained",
                          "from_grade": "Overweight", "to_grade": "Overweight",
                          "target": 400.0, "prior_target": 350.0}
    assert changes[1]["action"] == "upgrade"
    assert changes[1]["prior_target"] is None  # 0.0 means "no prior target"


def test_earnings(monkeypatch):
    assert view(monkeypatch)["earnings"] == {"date": "2026-11-17", "eps_avg": 2.55, "eps_low": 2.34, "eps_high": 2.7,
                                             "revenue_avg": 65_000_000_000.0}


def test_each_piece_fails_independently(monkeypatch):
    class Flaky:
        recommendations = pd.DataFrame()
        analyst_price_targets = {}
        calendar = {}

        @property
        def upgrades_downgrades(self):
            raise RuntimeError("yahoo 404")

    monkeypatch.setattr(analyst.yf, "Ticker", lambda t: Flaky())
    assert analyst.fetch_analyst_view("NVDA", today=TODAY) == {
        "consensus": None, "price_target": None, "rating_changes": [], "earnings": None}


# --- formatting ---

def test_fact_lines_show_days_until_earnings(monkeypatch):
    assert analyst.fact_lines(view(monkeypatch), TODAY) == ["Next earnings report: 2026-11-17 (in 27 days)"]


def test_fact_lines_empty_without_earnings():
    assert analyst.fact_lines({"earnings": None}, TODAY) == []


def test_opinion_lines(monkeypatch):
    lines = analyst.opinion_lines(view(monkeypatch), price_now=221.33, today=TODAY)
    assert lines == [
        "Consensus of 62 analysts: 10 strong buy, 49 buy, 2 hold, 1 sell, 0 strong sell",
        "Price target: mean 328.72 (+48.5% vs current 221.33), median 315.00, range 180.00-515.00",
        "EPS estimate for the 2026-11-17 report: 2.55 (range 2.34-2.70)",
        "Rating change 2026-10-20 (yesterday): Cantor Fitzgerald maintained Overweight, target 350.00 -> 400.00",
        "Rating change 2026-09-29: Rosenblatt upgrade Neutral -> Buy, target 390.00",
    ]


def test_opinion_lines_without_price_skip_upside():
    v = {"consensus": None, "price_target": {"mean": 10.0, "median": 9.0, "low": 8.0, "high": 12.0},
         "rating_changes": [], "earnings": None}
    assert analyst.opinion_lines(v, price_now=None, today=TODAY) == [
        "Price target: mean 10.00, median 9.00, range 8.00-12.00"]


def test_fact_lines_earnings_today_and_tomorrow():
    e = {"date": "2026-10-21", "eps_avg": None}
    assert analyst.fact_lines({"earnings": e}, TODAY) == ["Next earnings report: 2026-10-21 (today)"]
    assert analyst.fact_lines({"earnings": {**e, "date": "2026-10-22"}}, TODAY) == \
        ["Next earnings report: 2026-10-22 (tomorrow)"]


def test_unchanged_target_is_not_shown_as_a_move():
    ch = {"date": "2026-10-01", "firm": "JP Morgan", "action": "reiterated", "from_grade": "Overweight",
          "to_grade": "Overweight", "target": 920.0, "prior_target": 920.0}
    v = {"consensus": None, "price_target": None, "rating_changes": [ch], "earnings": None}
    assert analyst.opinion_lines(v, None, TODAY) == [
        "Rating change 2026-10-01: JP Morgan reiterated Overweight, target 920.00 (unchanged)"]
