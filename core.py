from typing import Any, Dict, List
from datetime import datetime
import ticker_checker

def fmt_number(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "N/A"


def find_alerts(snapshot: List[Dict[str, Any]], threshold: float) -> List[str]:
    alerts = []
    for row in snapshot:
        change_pct = row.get("change_pct") or 0.0
        if abs(change_pct) > threshold:
            alerts.append(f"{row['ticker']} moved {change_pct:.2f}% (now {fmt_number(row.get('price_now'))})")
    return alerts


def build_context(snapshot: List[Dict[str, Any]], news_map: Dict[str, List[Dict]]) -> str:
    lines = []
    for row in snapshot:
        lines.append(
            f'{row["ticker"]}: now={fmt_number(row.get("price_now"))}, '
            f'prev_close={fmt_number(row.get("prev_close"))}, change={fmt_number(row.get("change_pct"))}%'
        )
        for n in news_map.get(row["ticker"], []):
            lines.append(f"  - {n['title']} ({n.get('link', '')})")
    return "\n".join(lines) if lines else "No price snapshot."


def fetch_snapshot(ticker: str, timezone: str = "America/Toronto") -> Dict[str, Any]:
    price_now = ticker_checker.get_intraday_price_at(ticker, datetime.now().strftime("%H:%M"), timezone)
    prev_close = ticker_checker.get_previous_close(ticker)
    change_pct = None
    if price_now is not None and prev_close:
        change_pct = (price_now - prev_close) / prev_close * 100.0
    return {"ticker": ticker, "price_now": price_now, "prev_close": prev_close, "change_pct": change_pct}

