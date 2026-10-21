import argparse
import time
import uuid
import core
from datetime import datetime
from typing import TypedDict, List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
import parser
import notifier
import news_collector
import ticker_checker
import monitor
import analyst
import filings

ALERT_STORE_PATH = "state/alerts.json"
DEFAULT_REQUIREMENT = "Check MSFT and META price and give me English report in discord"


# define state
class AgentState(TypedDict):
    requirement: str                    # user input about requirements
    rules: Dict[str, Any]               # parsed user requirements
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
    alerts_only: bool                   # skip news/brief/notify when no new alert fired


# define nodes
def node_parse(state:AgentState) -> AgentState:
    parsed_rules = parser.parse_user_requirement(state["requirement"])
    state["rules"] = parsed_rules
    print(f"[DEBUG][node_parse] parsed rules: \n{parsed_rules}")
    return state

def node_price(state: AgentState) -> AgentState:
    timezone = state["rules"].get("timezone", "America/Toronto")
    snapshot = []
    for ticker in state["rules"]["tickers"]:
        time.sleep(0.3)
        snapshot.append(core.fetch_snapshot(ticker, timezone))
    state["snapshot"] = snapshot
    return state

def _today(rules: Dict[str, Any]) -> str:
    return datetime.now(ZoneInfo(rules.get("timezone", monitor.DEFAULT_TZ))).date().isoformat()

def _report_tickers(state: AgentState) -> List[str]:
    """Intraday alerts only talk about the tickers that triggered; daily briefs cover all of them."""
    if state.get("alerts_only"):
        return [row["ticker"] for row in state.get("alert_rows", [])]
    return state["rules"]["tickers"]

def record_sent_alerts(state: AgentState) -> None:
    """Mark this run's alerts as delivered so later runs don't repeat them."""
    if state.get("alert_store") and state.get("alert_rows"):
        threshold = float(state["rules"]["alert_threshold"])
        monitor.AlertStore(state["alert_store"]).record(state["alert_rows"], threshold, _today(state["rules"]))

def node_judge(state: AgentState) -> AgentState:
    threshold = float(state["rules"]["alert_threshold"])
    rows = core.find_alert_rows(state.get("snapshot", []), threshold)
    if state.get("alert_store"):
        rows = monitor.AlertStore(state["alert_store"]).filter_new(rows, threshold, _today(state["rules"]))
    state["alert_rows"] = rows
    state["alerts"] = [core.format_alert(row) for row in rows]
    return state

def node_news(state:AgentState) -> AgentState:
    tickers = _report_tickers(state)
    news = {}
    for ticker in tickers:
        news[ticker] = news_collector.fetch_news_headlines(ticker, 5)
    state["news_map"] = news
    print(f"[DEBUG][node_news] news count: {len(news)}")
    print(f"[DEBUG][node_news] news: {news}")
    return state

def node_research(state: AgentState) -> AgentState:
    timezone = state["rules"].get("timezone", monitor.DEFAULT_TZ)
    research = {}
    for ticker in _report_tickers(state):
        research[ticker] = {
            "analyst": analyst.fetch_analyst_view(ticker),
            "filings": filings.fetch_recent_filings(ticker),
            "volume": ticker_checker.get_relative_volume(ticker, timezone),
        }
    state["research"] = research
    return state

def node_brief(state: AgentState) -> AgentState:
    rules = state["rules"]
    tickers = _report_tickers(state)
    snapshot = [row for row in state.get("snapshot", []) if row["ticker"] in tickers]
    market = core.fetch_market(rules.get("timezone", monitor.DEFAULT_TZ))
    context = core.build_context(snapshot, state.get("news_map", {}), market, state.get("research"))
    kind = "intraday" if state.get("alerts_only") else "daily"
    state["brief"] = core.generate_brief(tickers, context, rules.get("report_language", "zh"), kind=kind)
    return state

def node_notify(state:AgentState) -> AgentState:
    webhook, mention_id = core.load_discord_config()

    method = state["rules"].get("notify_method", "console")
    alerts = state.get("alerts", [])
    brief = state.get("brief", "")

    def send(message: str) -> bool:
        return notifier.notify(method, message, discord_webhook=webhook, mention_id=mention_id)

    if state.get("alerts_only"):
        # intraday: one message, the alert followed by why it moved
        alerts_sent = send("[INTRADAY ALERT]\n" + "\n".join(alerts) + "\n\n" + brief)
    else:
        alerts_sent = send("[ALERT]\n" + "\n".join(alerts)) if alerts else False
        send("[DAILY BRIEF]\n" + brief)
    if alerts_sent:
        record_sent_alerts(state)
    state["_notified"] = True
    print(f"[DEBUG][node_notify] notified via: {method}, alerts: {len(alerts)}")
    return state

# define supervisor
def node_supervisor(state: AgentState) -> AgentState:
    plan: List[str] = []
    if "rules" not in state:
        plan.append("parse")
    if "snapshot" not in state:
        plan.append("price")
    if "alerts" not in state:
        plan.append("judge")
    elif state.get("alerts_only") and not state["alerts"]:
        # scheduled check with nothing new to report: stay quiet
        state["plan"] = plan
        return state
    if state.get("rules", {}).get("news_enabled", True) and "news_map" not in state:
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
    list_of_nodes = ["parse", "price", "news", "research", "judge", "brief", "notify"]
    g = StateGraph(AgentState)

    # add nodes
    g.add_node("supervisor", node_supervisor)
    g.add_node("parse", node_parse)
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
        "parse": "parse",
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

def run_once(app, rules: Dict[str, Any], alert_store: Optional[str] = None, alerts_only: bool = False) -> AgentState:
    init = {"rules": rules, "alerts_only": alerts_only}
    if alert_store:
        init["alert_store"] = alert_store
    return app.invoke(init, config={"configurable": {"thread_id": str(uuid.uuid4())}})


def main():
    ap = argparse.ArgumentParser(description="Stock monitor (workflow mode)")
    ap.add_argument("requirement", nargs="?", default=DEFAULT_REQUIREMENT,
                    help="natural-language monitoring requirement")
    ap.add_argument("--once", action="store_true", help="run a single check now and exit")
    args = ap.parse_args()

    rules = parser.parse_user_requirement(args.requirement)
    print(f"[MONITOR] rules: {rules}")
    app = build_graph().compile(checkpointer=MemorySaver())

    if args.once:
        run_once(app, rules)
        return

    # interval mode only speaks up when a new alert fires; daily mode always sends the brief
    alerts_only = rules["schedule_mode"] == "interval"
    try:
        monitor.run_loop(rules, lambda: run_once(app, rules, ALERT_STORE_PATH, alerts_only))
    except KeyboardInterrupt:
        print("[MONITOR] stopped")


if __name__ == "__main__":
    main()