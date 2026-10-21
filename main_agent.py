from langchain_core.tools import tool
from langchain.agents import create_agent

from typing import List, Dict, Any
from threading import Lock
import yaml
from datetime import datetime
import ticker_checker
import llm_interaction
import notifier
import news_collector
from prompt_manager import PromptManager
from dotenv import load_dotenv
import os
import logging
from langchain_openai import ChatOpenAI
from llm_interaction import LLM_BASE_URL, LLM_MODEL

# load llm api key in .env
load_dotenv()

# log
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="./logs/agent.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# define state
"""class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next: str"""

# define tools
yf_lock = Lock()

@tool("ticker_price")
def ticker_price(ticker:str, timezone:str="America/Toronto") -> list[Dict[str, Any]]:
    """
    Use yfinance API to get ticker data (ticker: stock ticker symbol, price_now: current price,
    prev_close: previous close price, change_pct: change percent)
    :param ticker: ticker symbol
    :param timezone: user location timezone, e.g.: "America/Toronto"
    :return: dictionary of ticker data
    """
    with yf_lock:
        snapshot = []
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        timezone = timezone
        price_now = ticker_checker.get_intraday_price_at(ticker, current_time, timezone)
        prev_close = ticker_checker.get_previous_close(ticker)
        if price_now is None or prev_close is None:
            change_pct = 0.0
        else:
            change_pct = ticker_checker.get_change_pct_vs_prev_close(ticker, price_now)
        snapshot.append({
            "ticker": ticker,
            "price_now": price_now,
            "prev_close": prev_close,
            "change_pct": change_pct if change_pct else 0.0,
        })
        #print(f"[DEBUG][ticker_price] snapshot: {snapshot}")
        logger.info(f"[ticker_price] ticker price: {ticker}")
        return snapshot

@tool("ticker_news")
def ticker_news(ticker:str) -> List[Dict[str, Any]]:
    """
    Use yfinance API to get ticker news
    :param ticker: ticker symbol
    :return: list of ticker news with reference link
    """
    news = news_collector.fetch_news_headlines(ticker, 5)
    #print(f"[DEBUG][ticker_news] news count: {len(news)}")
    #print(f"[DEBUG][ticker_news] news: {news}")
    logger.info(f"[ticker_news] news count: {len(news)}")
    logger.info(f"[ticker_news] news: {news}")
    return news

@tool("send_notification")
def send_notification(method:str, content:str):
    """
    notify user about the ticker report on method (console or discord)
    :param method: notification method (console or discord)
    :param content: notification content
    :return:
    """
    with open("config.yaml", "r") as f:
        base_cfg = yaml.safe_load(f)
    webhook = (base_cfg.get("discord") or {}).get("webhook_url")
    mention_id = (base_cfg.get("discord") or {}).get("mention_id")
    #print(f"[DEBUG] discord message: {content}")
    notifier.notify(method, "[DAILY BRIEF]\n" + content, discord_webhook=webhook, mention_id=mention_id)
    #print(f"[DEBUG][send_notification] notified via: {method}")
    logger.info(f"[send_notification] notified via: {method}")
    return

@tool("generate_report")
def generate_report(ticker:str, snapshot:Dict[str, Any], news:List[Dict[str, Any]], language: str="zh") -> str:
    """
    Generate report from ticker snapshot data and news
    :param ticker: ticker symbol for the report
    :param snapshot: ticker data
    :param news: news related to the ticker
    :param language: language of the report, e.g.: "zh", "en", "jp"
    :return: report text
    """
    # price may be None outside trading hours
    def fmt(value) -> str:
        return f"{value:.2f}" if isinstance(value, (int, float)) else "N/A"

    # build context
    lines = [
        f'{ticker}: now={fmt(snapshot.get("price_now"))}, prev_close={fmt(snapshot.get("prev_close"))}, change={fmt(snapshot.get("change_pct"))}%']
    for n in news:
        lines.append(f"  - {n['title']} ({n.get('link', '')})")
    context = "\n".join(lines) if lines else "No price snapshot."
    prompt_manager = PromptManager()
    prompt = prompt_manager.construct_prompt(
        name= "report",
        ticker= ticker,
        language= language,
        context= context,
    )
    logger.info(f"[generate_report] generated generate_report prompt: {prompt}")
    brief = llm_interaction.ask_llm(prompt) or ""
    # Fallback
    if not brief.strip():
        bullets = "\n".join([f"- {l}" for l in lines]) or "No price snapshot."
        brief = f"{bullets}\nNo price snapshot provided, cannot generate summary."
    #print(f"[DEBUG][generate_report] brief (first 120): {brief[:120]}")
    logger.info(f"[generate_report] brief: {brief[:120]}")
    return brief

# create agent
tools = [ticker_price, ticker_news, send_notification, generate_report]
prompt_manager = PromptManager()
system_prompt = prompt_manager.render_prompt(name="langgraph_agent")["system"]
logger.info(f"[system_prompt] generated system_prompt: {system_prompt}")

llm = ChatOpenAI(
    model=LLM_MODEL,
    base_url=LLM_BASE_URL,
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=system_prompt,
)
#"分析 MSFT, NVDA 和 META, 并在discord给我发简报"
user_content = input("I am stock ticker monitor agent, how can I help you?\n")

result = agent.invoke({
    "messages": [{"role": "user", "content":user_content}]
})
