import argparse
import time
import uuid
import core
from datetime import datetime
from typing import TypedDict, List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
import notifier
import news_collector
import ticker_checker
import monitor
import analyst
import filings
import settings as settings_mod
from settings import Settings, TickerRule

ALERT_STORE_PATH = "state/alerts.json"


# define state
class AgentState(TypedDict):
    settings: Settings                  # global settings from config.yaml
    tickers: List[TickerRule]           # watchlist, each ticker with its own rules
    kind: str                           # "daily" brief for every ticker, or "intraday" move check
    snapshot: List[Dict[str, Any]]      # stock ticker price info
    news_map: Dict[str, List[Dict]]     # news list
    research: Dict[str, Dict[str, Any]] # per ticker: analyst views, SEC filings, relative volume
    alerts: List[str]                   # alert content
    alert_rows: List[Dict[str, Any]]    # snapshot rows behind the alerts
    brief: str                          # summary content
    errors: List[str]                   # error log
    plan: List[str]                     # available actions
    _notified: bool                     # whether notified
    alert_store: str                    # path of the alert dedup store; no dedup when absent


def _intraday(state: AgentState) -> bool:
    return state.get("kind") == "intraday"

def _scope(state: AgentState) -> List[TickerRule]:
    """Tickers this run looks at: intraday checks skip the ones opted out of them."""
    return [t for t in state["tickers"] if t.intraday or not _intraday(state)]

def _thresholds(state: AgentState) -> Dict[str, float]:
    return {t.symbol: t.alert_threshold for t in state["tickers"]}

def _today(state: AgentState) -> str:
    return datetime.now(ZoneInfo(state["settings"].timezone)).date().isoformat()

def _report_tickers(state: AgentState) -> List[str]:
    """Intraday alerts only talk about the tickers that triggered; daily briefs cover all of them."""
    if _intraday(state):
        return [row["ticker"] for row in state.get("alert_rows", [])]
    return [t.symbol for t in state["tickers"]]

def _research_tickers(state: AgentState) -> List[str]:
    wanted = {t.symbol for t in state["tickers"] if t.research}
    return [symbol for symbol in _report_tickers(state) if symbol in wanted]


# define nodes
def node_price(state: AgentState) -> AgentState:
    snapshot = []
    for ticker in _scope(state):
        time.sleep(0.3)
        snapshot.append(core.fetch_snapshot(ticker.symbol, state["settings"].timezone))
    state["snapshot"] = snapshot
    return state

def record_sent_alerts(state: AgentState) -> None:
    """Mark this run's alerts as delivered so later runs don't repeat them."""
    if state.get("alert_store") and state.get("alert_rows"):
        monitor.AlertStore(state["alert_store"]).record(state["alert_rows"], _thresholds(state), _today(state))

def node_judge(state: AgentState) -> AgentState:
    thresholds = _thresholds(state)
    rows = core.find_alert_rows(state.get("snapshot", []), thresholds)
    if state.get("alert_store"):
        rows = monitor.AlertStore(state["alert_store"]).filter_new(rows, thresholds, _today(state))
    state["alert_rows"] = rows
    state["alerts"] = [core.format_alert(row) for row in rows]
    return state

def node_news(state:AgentState) -> AgentState:
    news = {}
    for ticker in _research_tickers(state):
        news[ticker] = news_collector.fetch_news_headlines(ticker, 5)
    state["news_map"] = news
    print(f"[DEBUG][node_news] news count: {len(news)}")
    print(f"[DEBUG][node_news] news: {news}")
    return state

def node_research(state: AgentState) -> AgentState:
    timezone = state["settings"].timezone
    research = {}
    for ticker in _research_tickers(state):
        research[ticker] = {
            "analyst": analyst.fetch_analyst_view(ticker),
            "filings": filings.fetch_recent_filings(ticker),
            "volume": ticker_checker.get_relative_volume(ticker, timezone),
        }
    state["research"] = research
    return state

def node_brief(state: AgentState) -> AgentState:
    cfg = state["settings"]
    tickers = _report_tickers(state)
    snapshot = [row for row in state.get("snapshot", []) if row["ticker"] in tickers]
    market = core.fetch_market(cfg.timezone)
    context = core.build_context(snapshot, state.get("news_map", {}), market, state.get("research"))
    state["brief"] = core.generate_brief(tickers, context, cfg.report_language, kind=state["kind"])
    return state

def node_notify(state:AgentState) -> AgentState:
    cfg = state["settings"]
    alerts = state.get("alerts", [])
    brief = state.get("brief", "")

    def send(message: str) -> bool:
        return notifier.notify(cfg.notify_method, message, discord_webhook=cfg.discord_webhook,
                               mention_id=cfg.discord_mention_id)

    if _intraday(state):
        # intraday: one message, the alert followed by why it moved
        alerts_sent = send("[INTRADAY ALERT]\n" + "\n".join(alerts) + "\n\n" + brief)
    else:
        alerts_sent = send("[ALERT]\n" + "\n".join(alerts)) if alerts else False
        send("[DAILY BRIEF]\n" + brief)
    if alerts_sent:
        record_sent_alerts(state)
    state["_notified"] = True
    print(f"[DEBUG][node_notify] notified via: {cfg.notify_method}, alerts: {len(alerts)}")
    return state

# define supervisor
def node_supervisor(state: AgentState) -> AgentState:
    plan: List[str] = []
    if "snapshot" not in state:
        plan.append("price")
    if "alerts" not in state:
        plan.append("judge")
    elif _intraday(state) and not state["alerts"]:
        # scheduled check with nothing new to report: stay quiet
        state["plan"] = plan
        return state
    if "news_map" not in state:
        plan.append("news")
    if "research" not in state:
        plan.append("research")
    if "brief" not in state:
        plan.append("brief")
    if not state.get("_notified", False):
        plan.append("notify")
    state["plan"] = plan
    return state

# dispatcher
def pop_next_action(state:AgentState) -> str:
    plan = state.get("plan", [])
    if not plan:
        return "__END__"
    nxt = plan.pop(0)
    state["plan"] = plan
    print(f"[DEBUG] pop next action: {nxt}")
    return nxt


# build up graph

def build_graph():
    list_of_nodes = ["price", "news", "research", "judge", "brief", "notify"]
    g = StateGraph(AgentState)

    # add nodes
    g.add_node("supervisor", node_supervisor)
    g.add_node("price", node_price)
    g.add_node("judge", node_judge)
    g.add_node("brief", node_brief)
    g.add_node("notify", node_notify)
    g.add_node("news", node_news)
    g.add_node("research", node_research)

    # entry
    g.set_entry_point("supervisor")

    # route helper
    def route(state:AgentState) -> str:
        return pop_next_action(state)

    # add edges
    # from supervisor to first action
    g.add_conditional_edges("supervisor", route, {
        "price": "price",
        "judge": "judge",
        "brief": "brief",
        "notify": "notify",
        "news": "news",
        "research": "research",
        "__END__": END,
    })

    # each node returns to router
    for node in list_of_nodes:
        g.add_edge(node, "supervisor")

    return g

def run_once(app, settings: Settings, tickers: List[TickerRule], kind: str,
             alert_store: Optional[str] = None) -> AgentState:
    init = {"settings": settings, "tickers": tickers, "kind": kind}
    if alert_store:
        init["alert_store"] = alert_store
    return app.invoke(init, config={"configurable": {"thread_id": str(uuid.uuid4())}})


def tick(app, settings: Settings, reloader, kind: str) -> Optional[AgentState]:
    """One scheduled run on the current watchlist; intraday runs dedup alerts across the day."""
    tickers = reloader.get()
    if not tickers:
        print("[MONITOR] watchlist is empty, nothing to do")
        return None
    return run_once(app, settings, tickers, kind, ALERT_STORE_PATH if kind == "intraday" else None)


def main():
    ap = argparse.ArgumentParser(description="Stock monitor (workflow mode)")
    ap.add_argument("--once", choices=["daily", "intraday"], help="run one daily brief or intraday check now and exit")
    ap.add_argument("--config", default="config.yaml", help="global settings and secrets")
    ap.add_argument("--watchlist", default="watchlist.yaml", help="tickers to watch, reloaded on every run")
    args = ap.parse_args()

    settings = settings_mod.load_settings(args.config)

    def report_watchlist_error(message: str) -> None:
        print(message)
        if settings.notify_method == "discord":
            notifier.notify("discord", message, discord_webhook=settings.discord_webhook,
                            mention_id=settings.discord_mention_id)

    reloader = settings_mod.WatchlistReloader(args.watchlist, settings.defaults, on_error=report_watchlist_error)
    print(f"[MONITOR] settings: {settings}")
    print(f"[MONITOR] watchlist: {[t.symbol for t in reloader.get()]}")  # fails fast if missing or invalid
    app = build_graph().compile(checkpointer=MemorySaver())

    if args.once:
        tick(app, settings, reloader, args.once)
        return

    try:
        monitor.run_loop(settings, lambda kind: tick(app, settings, reloader, kind))
    except KeyboardInterrupt:
        print("[MONITOR] stopped")


if __name__ == "__main__":
    main()
