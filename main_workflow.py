import time
from datetime import datetime
import uuid
import core

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
    _notified: bool                     # whether notified


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

def node_judge(state: AgentState) -> AgentState:
    threshold = float(state["rules"]["alert_threshold"])
    state["alerts"] = core.find_alerts(state.get("snapshot", []), threshold)
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

def node_brief(state: AgentState) -> AgentState:
    rules = state["rules"]
    language = rules.get("report_language", "zh")
    threshold = float(rules["alert_threshold"])
    context = core.build_context(state.get("snapshot", []), state.get("news_map", {}))

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
        bullets = "\n".join(f"- {line}" for line in context.splitlines())
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
    if state.get("rules", {}).get("news_enabled", True) and "news_map" not in state:
        plan.append("news")
    if "alerts" not in state:
        plan.append("judge")
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
    for node in ["parse", "price", "news", "judge", "brief", "notify"]:
        g.add_edge(node, "supervisor")

    return g

if __name__ == "__main__":
    graph = build_graph()
    app = graph.compile(checkpointer=MemorySaver())
    init = {
        "requirement": "Check MSFT and META price and give me English report in discord"
    }
    out = app.invoke(
        init,
        config={"configurable": {"thread_id": str(uuid.uuid4())}}
    )
    print(out.keys())