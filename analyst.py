from datetime import date, timedelta
from typing import Any, Dict, List, Optional
import yfinance as yf

RATING_LOOKBACK_DAYS = 30
MAX_RATING_CHANGES = 5

ACTIONS = {
    "up": "upgrade",
    "down": "downgrade",
    "init": "initiated",
    "main": "maintained",
    "reit": "reiterated",
}


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def _consensus(t) -> Optional[Dict[str, int]]:
    df = t.recommendations
    if df is None or df.empty:
        return None
    row = df.iloc[0]  # "0m" = current month
    return {
        "strong_buy": int(row["strongBuy"]), "buy": int(row["buy"]), "hold": int(row["hold"]),
        "sell": int(row["sell"]), "strong_sell": int(row["strongSell"]),
    }


def _price_target(t) -> Optional[Dict[str, float]]:
    pt = t.analyst_price_targets or {}
    if _num(pt.get("mean")) is None:
        return None
    return {k: round(_num(pt.get(k)), 2) if _num(pt.get(k)) is not None else None
            for k in ("mean", "median", "low", "high")}


def _rating_changes(t, today: date) -> List[Dict[str, Any]]:
    df = t.upgrades_downgrades
    if df is None or df.empty:
        return []
    cutoff = today - timedelta(days=RATING_LOOKBACK_DAYS)
    changes = []
    for when, row in df.sort_index(ascending=False).iterrows():
        if when.date() < cutoff:
            continue
        prior = _num(row.get("priorPriceTarget"))
        changes.append({
            "date": when.date().isoformat(),
            "firm": str(row.get("Firm") or ""),
            "action": ACTIONS.get(row.get("Action"), str(row.get("Action") or "")),
            "from_grade": str(row.get("FromGrade") or ""),
            "to_grade": str(row.get("ToGrade") or ""),
            "target": _num(row.get("currentPriceTarget")) or None,
            "prior_target": prior or None,  # 0.0 means "no prior target"
        })
        if len(changes) >= MAX_RATING_CHANGES:
            break
    return changes


def _earnings(t) -> Optional[Dict[str, Any]]:
    cal = t.calendar or {}
    dates = cal.get("Earnings Date") or []
    if not dates:
        return None
    return {
        "date": dates[0].isoformat(),
        "eps_avg": _num(cal.get("Earnings Average")),
        "eps_low": _num(cal.get("Earnings Low")),
        "eps_high": _num(cal.get("Earnings High")),
        "revenue_avg": _num(cal.get("Revenue Average")),
    }


def fetch_analyst_view(ticker: str, today: Optional[date] = None) -> Dict[str, Any]:
    """
    Wall Street's view of a ticker from yfinance: consensus rating, price targets,
    recent rating changes and the next earnings report. Each piece fails independently.
    Only plain types are returned so the result can live in graph state.
    """
    today = today or date.today()
    t = yf.Ticker(ticker)
    view: Dict[str, Any] = {"consensus": None, "price_target": None, "rating_changes": [], "earnings": None}
    for key, fetch in (("consensus", _consensus), ("price_target", _price_target),
                       ("rating_changes", lambda x: _rating_changes(x, today)), ("earnings", _earnings)):
        try:
            view[key] = fetch(t) or view[key]
        except Exception as e:
            print(f"[WARN][analyst] {ticker} {key} unavailable: {e}")
    return view


def _fmt(value: Optional[float]) -> str:
    return f"{value:.2f}" if value is not None else "N/A"


def fact_lines(view: Optional[Dict[str, Any]], today: date) -> List[str]:
    """Scheduled events are facts; estimates about them are opinions (see opinion_lines)."""
    earnings = (view or {}).get("earnings")
    if not earnings:
        return []
    days = (date.fromisoformat(earnings["date"]) - today).days
    when = {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")
    return [f"Next earnings report: {earnings['date']} ({when})"]


def _when(day: str, today: date) -> str:
    delta = (today - date.fromisoformat(day)).days
    return {0: f"{day} (today)", 1: f"{day} (yesterday)"}.get(delta, day)


def opinion_lines(view: Optional[Dict[str, Any]], price_now: Optional[float], today: date) -> List[str]:
    if not view:
        return []
    lines = []
    c = view.get("consensus")
    if c:
        lines.append(
            f"Consensus of {sum(c.values())} analysts: {c['strong_buy']} strong buy, {c['buy']} buy, "
            f"{c['hold']} hold, {c['sell']} sell, {c['strong_sell']} strong sell"
        )
    pt = view.get("price_target")
    if pt:
        upside = ""
        if price_now and pt.get("mean") is not None:
            upside = f" ({(pt['mean'] / price_now - 1) * 100:+.1f}% vs current {price_now:.2f})"
        lines.append(f"Price target: mean {_fmt(pt.get('mean'))}{upside}, median {_fmt(pt.get('median'))}, "
                     f"range {_fmt(pt.get('low'))}-{_fmt(pt.get('high'))}")
    e = view.get("earnings")
    if e and e.get("eps_avg") is not None:
        lines.append(f"EPS estimate for the {e['date']} report: {_fmt(e['eps_avg'])} "
                     f"(range {_fmt(e.get('eps_low'))}-{_fmt(e.get('eps_high'))})")
    for ch in view.get("rating_changes") or []:
        grade = ch["to_grade"] if ch["from_grade"] in ("", ch["to_grade"]) else f"{ch['from_grade']} -> {ch['to_grade']}"
        target = ""
        if ch.get("target"):
            prior = ch.get("prior_target")
            if prior == ch["target"]:
                target = f", target {_fmt(ch['target'])} (unchanged)"
            elif prior:
                target = f", target {_fmt(prior)} -> {_fmt(ch['target'])}"
            else:
                target = f", target {_fmt(ch['target'])}"
        lines.append(f"Rating change {_when(ch['date'], today)}: {ch['firm']} {ch['action']} {grade}{target}")
    return lines
