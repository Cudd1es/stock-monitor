import time
from datetime import datetime
import uuid

from typing import TypedDict, List, Dict, Any

import yaml
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

import parser
import ticker_checker
import llm_interaction
import notifier
import news_collector

# define state
class AgentState(TypedDict):
    requirement: str                    # user input about requirements
    rules: Dict[str, Any]               # parsed user requirements
    snapshot: List[Dict[str, Any]]      # stock ticker price info
    news_map: Dict[str, List[Dict]]     # news list
    alerts: List[str]                   # alert content
    brief: str                          # summary content
    errors: List[str]                   # error log
    plan: List[str]                     # available actions


# define nodes
def node_parse(state:AgentState) -> AgentState:
    parsed_rules = parser.parse_user_requirement(state["requirement"])
    state["rules"] = parsed_rules
    print(f"[DEBUG][node_parse] parsed rules: \n{parsed_rules}")
    return state

def node_price(state:AgentState) -> AgentState:
    tickers = state["rules"]["tickers"]
    snapshot = []

    now = datetime.now()
    current_time = now.strftime("%H:%M")
    timezone = state["rules"].get("timezone", "America/Toronto")
    for ticker in tickers:
        time.sleep(0.3)
        price_now = ticker_checker.get_intraday_price_at(ticker, current_time, timezone)
        prev_close = ticker_checker.get_previous_close(ticker)
        if price_now is None or prev_close is None:
            continue
        change_pct = ticker_checker.get_change_pct_vs_prev_close(ticker, price_now)
        snapshot.append({
            "ticker": ticker,
            "price_now": price_now,
            "prev_close": prev_close,
            "change_pct": change_pct if change_pct else 0.0,
        })
    state["snapshot"] = snapshot
    print(f"[DEBUG][node_price] snapshot: {snapshot}")
    return state

def node_judge(state:AgentState) -> AgentState:
    threshold = float(state["rules"]["alert_threshold"])
    alerts: List[str] = []
    for row in state.get("snapshot", []):
        change_pct = row.get("change_pct", 0.0)
        if change_pct > threshold:
            alerts.append(f"{row['ticker']} moved {row['change_pct']:.2f}% (now {row['price_now']:.2f})")
    state["alerts"] = alerts
    print(f"[DEBUG][node_judge] alerts: {alerts}")
    return state

def node_news(state:AgentState) -> AgentState:
    tickers = state["rules"]["tickers"]
    news = {}
    for ticker in tickers:
        news[ticker] = news_collector.fetch_news_headlines(ticker, 5)
    state["news_map"] = news
    print(f"[DEBUG][node_news] news count: {len(news)}")
    print(f"[DEBUG][node_news] news: {news}")
    return state

def node_brief(state:AgentState) -> AgentState:
    rules = state["rules"]
    language = rules.get("report_language", "zh")
    threshold = float(rules["alert_threshold"])
    news_map = state["news_map"]
    # build context
    lines = []
    for row in state.get("snapshot", []):
        lines.append(f'{row["ticker"]}: now={row["price_now"]:.2f}, prev_close={row["prev_close"]:.2f}, change={row["change_pct"]:.2f}%')
        news = news_map.get(row["ticker"], [])
        for n in news:
            lines.append(f"  - {n['title']} ({n.get('link', '')})")
    context = "\n".join(lines) if lines else "No price snapshot."

    # prompt
    prompt = f"""Write a concise end-of-day style report for the following tickers.
For each ticker, summarize news briefs with corresponding links
Emphasize any move beyond ±{threshold:.1f}% . Be neutral and factual. Avoid investment advice.

DATA:
{context}

Output in {language}, use clear bullets and a one-line summary at the end.
"""
    brief = llm_interaction.ask_llm(prompt, model="gpt-4o") or ""
    # Fallback
    if not brief.strip():
        bullets = "\n".join([f"- {l}" for l in lines]) or "No price snapshot."
        brief = f"{bullets}\nNo price snapshot provided, cannot generate summary."

    state["brief"] = brief
    print(f"[DEBUG][node_brief] brief (first 120): {brief[:120]}")
    return state

def node_notify(state:AgentState) -> AgentState:
    with open("config.yaml", "r") as f:
        base_cfg = yaml.safe_load(f)
    webhook = (base_cfg.get("discord") or {}).get("webhook_url")
    mention_id = (base_cfg.get("discord") or {}).get("mention_id")

    method = state["rules"].get("notify_method", "console")
    alerts = state.get("alerts", [])
    brief = state.get("brief", "")

    if alerts:
        notifier.notify(method, "[ALERT]\n" + "\n".join(alerts), discord_webhook=webhook, mention_id=mention_id)
    notifier.notify(method, "[DAILY BRIEF]\n" + brief, discord_webhook=webhook, mention_id=mention_id)
    print(f"[DEBUG][node_notify] notified via: {method}, alerts: {len(alerts)}")
    return state

# define supervisor
def node_supervisor(state: AgentState) -> AgentState:
    plan: List[str] = []
    if "rules" not in state:
        plan.append("parse")
    if "snapshot" not in state:
        plan.append("price")
    if "news_map" not in state:
        plan.append("news")
    plan += ["judge", "brief", "notify"]
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
    list_of_nodes = ["parse", "price", "news", "judge", "brief", "notify"]
    g = StateGraph(AgentState)

    # add nodes
    g.add_node("supervisor", node_supervisor)
    g.add_node("parse", node_parse)
    g.add_node("price", node_price)
    g.add_node("judge", node_judge)
    g.add_node("brief", node_brief)
    g.add_node("notify", node_notify)
    g.add_node("news", node_news)

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
        "__END__": END,
    })

    # each node returns to router
    for node in list_of_nodes:
        g.add_conditional_edges(node, route, {
            "parse": "parse",
            "price": "price",
            "judge": "judge",
            "brief": "brief",
            "notify": "notify",
            "news": "news",
            "__END__": END,
        })

    return g

if __name__ == "__main__":
    graph = build_graph()
    app = graph.compile(checkpointer=MemorySaver())
    init = {
        "requirement": "Check MSFT and META price and tell me in discord"
    }
    out = app.invoke(
        init,
        config={"configurable": {"thread_id": str(uuid.uuid4())}}
    )
    print(out.keys())