from types import SimpleNamespace
import news_collector


def fake_search(news):
    return lambda ticker, news_count: SimpleNamespace(news=news)


def test_falls_back_to_search_when_ticker_news_is_empty(monkeypatch):
    monkeypatch.setattr(news_collector.yf, "Ticker", lambda t: SimpleNamespace(news=[]))
    monkeypatch.setattr(news_collector.yf, "Search", fake_search([{"title": "MSFT beats estimates", "link": "https://x"}]))
    assert news_collector.fetch_news_headlines("MSFT") == [{"title": "MSFT beats estimates", "link": "https://x"}]


def test_prefers_ticker_news_when_available(monkeypatch):
    ticker_news = [{"content": {"title": "From ticker", "canonicalUrl": {"url": "https://t"}}}]
    monkeypatch.setattr(news_collector.yf, "Ticker", lambda t: SimpleNamespace(news=ticker_news))
    monkeypatch.setattr(news_collector.yf, "Search", fake_search([{"title": "From search", "link": "https://s"}]))
    assert news_collector.fetch_news_headlines("MSFT") == [{"title": "From ticker", "link": "https://t"}]


def test_warns_when_both_sources_are_empty(monkeypatch, capsys):
    monkeypatch.setattr(news_collector.yf, "Ticker", lambda t: SimpleNamespace(news=[]))
    monkeypatch.setattr(news_collector.yf, "Search", fake_search([]))
    assert news_collector.fetch_news_headlines("MSFT") == []
    assert "no news" in capsys.readouterr().out.lower()
