from langchain_core.tools import tool
from langchain.agents import create_agent
import core
from typing import List, Dict, Any
from threading import Lock
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
logger = logging.getLogger(__name__)

# define state
"""class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next: str"""

# define tools
yf_lock = Lock()

@tool("ticker_price")
def ticker_price(ticker: str, timezone: str = "America/Toronto") -> Dict[str, Any]:
    """
    Use yfinance API to get ticker data (ticker: stock ticker symbol, price_now: current price,
    prev_close: previous close price, change_pct: change percent; None when unavailable,
    e.g. outside trading hours)
    :param ticker: ticker symbol
    :param timezone: market timezone, e.g.: "America/Toronto"
    :return: dictionary of ticker data
    """
    with yf_lock:
        snapshot = core.fetch_snapshot(ticker, timezone)
    logger.info(f"[ticker_price] snapshot: {snapshot}")
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
    webhook, mention_id = core.load_discord_config()
    #print(f"[DEBUG] discord message: {content}")
    notifier.notify(method, "[DAILY BRIEF]\n" + content, discord_webhook=webhook, mention_id=mention_id)
    #print(f"[DEBUG][send_notification] notified via: {method}")
    logger.info(f"[send_notification] notified via: {method}")
    return

@tool("generate_report")
def generate_report(ticker: str, snapshot: Dict[str, Any], news: List[Dict[str, Any]], language: str = "zh") -> str:
    """
    Generate report from ticker snapshot data and news
    :param ticker: ticker symbol for the report
    :param snapshot: ticker data returned by ticker_price
    :param news: news related to the ticker, returned by ticker_news
    :param language: language of the report, e.g.: "zh", "en", "jp"
    :return: report text
    """
    # the LLM builds these arguments itself, so don't trust snapshot to carry the ticker key
    row = {**snapshot, "ticker": ticker}
    context = core.build_context([row], {ticker: news})
    brief = core.generate_brief([ticker], context, language)
    logger.info(f"[generate_report] brief: {brief[:120]}")
    return brief


# create agent
def main():
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        filename="./logs/agent.log",
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    tools = [ticker_price, ticker_news, send_notification, generate_report]
    system_prompt = PromptManager().render_prompt(name="langgraph_agent")["system"]
    logger.info(f"[system_prompt] generated system_prompt: {system_prompt}")

    llm = ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    user_content = input("I am stock ticker monitor agent, how can I help you?\n")
    result = agent.invoke({"messages": [{"role": "user", "content": user_content}]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()