from typing import Any, Dict, List, Optional
from datetime import date, datetime
from zoneinfo import ZoneInfo
import analyst
import filings
import ticker_checker
import llm_interaction
from prompt_manager import PromptManager
import yaml


def fmt_number(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "N/A"


def find_alert_rows(snapshot: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
    return [row for row in snapshot if abs(row.get("change_pct") or 0.0) > threshold]


def format_alert(row: Dict[str, Any]) -> str:
    return f"{row['ticker']} moved {row['change_pct']:.2f}% (now {fmt_number(row.get('price_now'))})"


def find_alerts(snapshot: List[Dict[str, Any]], threshold: float) -> List[str]:
    return [format_alert(row) for row in find_alert_rows(snapshot, threshold)]


BENCHMARKS = ["SPY", "QQQ"]  # market reference for telling stock-specific moves from market-wide ones


def build_context(
    snapshot: List[Dict[str, Any]],
    news_map: Dict[str, List[Dict]],
    market: Optional[List[Dict[str, Any]]] = None,
    research: Optional[Dict[str, Dict[str, Any]]] = None,
    today: Optional[date] = None,
) -> str:
    """
    Plain-text input for the report LLM. Per ticker, verifiable facts (volume, filings,
    scheduled earnings, news) are kept apart from opinions (analyst ratings, targets, estimates).
    """
    today = today or datetime.now(ZoneInfo("America/Toronto")).date()
    lines = []
    if market:
        lines.append("Market: " + ", ".join(f'{m["ticker"]} {fmt_number(m.get("change_pct"))}%' for m in market))
    for row in snapshot:
        lines.append(
            f'{row["ticker"]}: now={fmt_number(row.get("price_now"))}, '
            f'prev_close={fmt_number(row.get("prev_close"))}, change={fmt_number(row.get("change_pct"))}%'
        )
        res = (research or {}).get(row["ticker"]) or {}
        facts = [f"  - {line}" for line in
                 ticker_checker.volume_fact_lines(res.get("volume"))
                 + filings.fact_lines(res.get("filings") or [])
                 + analyst.fact_lines(res.get("analyst"), today)]
        if row["ticker"] in news_map and not news_map[row["ticker"]]:
            facts.append("  - no company-specific news found")
        for n in news_map.get(row["ticker"], []):
            source = f"[{n['publisher']}] " if n.get("publisher") else ""
            facts.append(f"  - {source}{n['title']} ({n.get('link', '')})")
            if n.get("summary"):
                facts.append(f"    {n['summary']}")
        if facts:
            lines += ["  Facts:"] + facts
        opinions = analyst.opinion_lines(res.get("analyst"), row.get("price_now"), today)
        if opinions:
            lines += ["  Opinions (analyst views, not facts):"] + [f"  - {line}" for line in opinions]
    return "\n".join(lines) if lines else "No price snapshot."


def fetch_market(timezone: str = "America/Toronto") -> List[Dict[str, Any]]:
    return [fetch_snapshot(ticker, timezone) for ticker in BENCHMARKS]


def fetch_snapshot(ticker: str, timezone: str = "America/Toronto") -> Dict[str, Any]:
    price_now = ticker_checker.get_intraday_price_at(ticker, datetime.now(ZoneInfo(timezone)).strftime("%H:%M"), timezone)
    prev_close = ticker_checker.get_previous_close(ticker)
    change_pct = None
    if price_now is not None and prev_close:
        change_pct = (price_now - prev_close) / prev_close * 100.0
    return {"ticker": ticker, "price_now": price_now, "prev_close": prev_close, "change_pct": change_pct}


REPORT_KINDS = {
    "daily": "daily report",
    "intraday": "intraday alert (the market is still open and prices are live; "
                "do not describe them as closing or end-of-day figures)",
}


def generate_brief(tickers: List[str], context: str, language: str = "zh", kind: str = "daily") -> str:
    prompt = PromptManager().construct_prompt(
        name="report",
        ticker=", ".join(tickers),
        language=language,
        context=context,
        report_kind=REPORT_KINDS[kind],
    )
    brief = llm_interaction.ask_llm(prompt) or ""
    if not brief.strip():
        bullets = "\n".join(f"- {line}" for line in context.splitlines())
        brief = f"{bullets}\nLLM unavailable, showing raw data."
    return brief


def load_discord_config(path: str = "config.yaml") -> tuple:
    try:
        with open(path, "r", encoding="utf-8") as f:
            base_cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return None, None
    if not isinstance(base_cfg, dict):
        return None, None
    discord_cfg = base_cfg.get("discord") or {}
    return discord_cfg.get("webhook_url"), discord_cfg.get("mention_id")