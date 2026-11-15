from langchain_core.tools import tool
from langchain.agents import create_agent
import core
from typing import List, Dict, Any, Optional
from threading import Lock
import notifier
import news_collector
import ticker_checker
import analyst
import filings
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
    Use yfinance API to get recent news about the ticker (unrelated stories filtered out)
    :param ticker: ticker symbol
    :return: list of ticker news with title, link, publisher and article summary
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

@tool("ticker_research")
def ticker_research(ticker: str, timezone: str = "America/Toronto") -> Dict[str, Any]:
    """
    Get analyst views (consensus rating, price targets, recent rating changes, next earnings date
    and estimates) from yfinance, recent SEC 8-K filings with their press releases, and today's
    volume relative to the usual volume by this time of day
    :param ticker: ticker symbol
    :param timezone: market timezone, e.g.: "America/Toronto"
    :return: dictionary with "analyst", "filings" and "volume"
    """
    with yf_lock:
        view = analyst.fetch_analyst_view(ticker)
        volume = ticker_checker.get_relative_volume(ticker, timezone)
    research = {"analyst": view, "filings": filings.fetch_recent_filings(ticker), "volume": volume}
    logger.info(f"[ticker_research] {ticker}: {len(research['filings'])} filings")
    return research


@tool("generate_report")
def generate_report(ticker: str, snapshot: Dict[str, Any], news: List[Dict[str, Any]],
                    research: Optional[Dict[str, Any]] = None, language: str = "zh") -> str:
    """
    Generate report from ticker snapshot data, news and research
    :param ticker: ticker symbol for the report
    :param snapshot: ticker data returned by ticker_price
    :param news: news related to the ticker, returned by ticker_news
    :param research: analyst views and SEC filings returned by ticker_research
    :param language: language of the report, e.g.: "zh", "en", "jp"
    :return: report text
    """
    # the LLM builds these arguments itself, so don't trust snapshot to carry the ticker key
    row = {**snapshot, "ticker": ticker}
    with yf_lock:
        market = core.fetch_market()
    context = core.build_context([row], {ticker: news}, market, {ticker: research} if research else None)
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

    tools = [ticker_price, ticker_news, ticker_research, send_notification, generate_report]
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