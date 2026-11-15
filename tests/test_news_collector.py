from types import SimpleNamespace
import news_collector

NOW = 1_792_600_000  # fixed "now" (epoch seconds)
HOUR = 3600


def fake_search(news):
    return lambda ticker, news_count: SimpleNamespace(news=news)


def item(title, related=("MSFT",), age_hours=1, link=None):
    return {
        "title": title,
        "link": link or f"https://x/{abs(hash(title))}",
        "publisher": "Wire",
        "providerPublishTime": NOW - age_hours * HOUR,
        "relatedTickers": list(related),
    }


def setup(monkeypatch, search_news, ticker_news=(), summaries=None):
    monkeypatch.setattr(news_collector.yf, "Ticker", lambda t: SimpleNamespace(news=list(ticker_news)))
    monkeypatch.setattr(news_collector.yf, "Search", fake_search(search_news))
    monkeypatch.setattr(news_collector, "company_aliases", lambda t: ["Microsoft"])
    summaries = summaries or {}
    fetched = []

    def fake_summary(url):
        fetched.append(url)
        return summaries.get(url, ("", ""))

    monkeypatch.setattr(news_collector, "fetch_article_text", fake_summary)
    return fetched


def titles(news):
    return [n["title"] for n in news]


# --- relevance ---

def test_keeps_headlines_naming_the_company_or_ticker(monkeypatch):
    setup(monkeypatch, [
        item("Microsoft Must Say This on Oct. 28"),
        item("Is MSFT a buy?"),
        item("Alphabet Has Lost $860 Billion Since Its Peak", related=("GOOG", "MSFT")),
    ])
    out = news_collector.fetch_news_headlines("MSFT", 5, now=NOW)
    assert titles(out) == ["Microsoft Must Say This on Oct. 28", "Is MSFT a buy?"]


def test_ticker_match_is_a_whole_word():
    assert not news_collector.mentions("MSFTX rallies", "MSFT", [])
    assert news_collector.mentions("Why Meta stock fell", "META", ["Meta"])


def test_untitled_match_kept_when_ticker_is_primary_and_article_describes_company(monkeypatch):
    primary = item("Cloud Giant Signs Huge AI Deal", related=("MSFT", "^GSPC"), link="https://p")
    setup(monkeypatch, [primary], summaries={"https://p": ("Microsoft signed a deal with OpenAI.", "")})
    assert titles(news_collector.fetch_news_headlines("MSFT", 5, now=NOW)) == ["Cloud Giant Signs Huge AI Deal"]


def test_untitled_match_dropped_when_article_is_about_something_else(monkeypatch):
    # Yahoo tags NVDA on unrelated stories because of ads embedded in the page
    noise = item("Social Security COLA in 2027", related=("MSFT",), link="https://n")
    setup(monkeypatch, [noise], summaries={"https://n": ("Retirees get a raise next year.", "")})
    assert news_collector.fetch_news_headlines("MSFT", 5, now=NOW) == []


def test_drops_stories_where_ticker_is_only_a_side_mention(monkeypatch):
    fetched = setup(monkeypatch, [item("Amazon Stock Could Climb Higher", related=("AMZN", "GOOG", "MSFT"))])
    assert news_collector.fetch_news_headlines("MSFT", 5, now=NOW) == []
    assert fetched == []  # not worth a page fetch


def test_drops_stale_news(monkeypatch):
    setup(monkeypatch, [item("Microsoft old story", age_hours=100), item("Microsoft fresh story")])
    assert titles(news_collector.fetch_news_headlines("MSFT", 5, now=NOW)) == ["Microsoft fresh story"]


def test_limits_to_topk(monkeypatch):
    setup(monkeypatch, [item(f"Microsoft story {i}") for i in range(6)])
    assert len(news_collector.fetch_news_headlines("MSFT", 3, now=NOW)) == 3


def test_dedups_by_title(monkeypatch):
    setup(monkeypatch, [item("Microsoft story"), item("Microsoft story", link="https://other")])
    assert len(news_collector.fetch_news_headlines("MSFT", 5, now=NOW)) == 1


# --- summaries ---

def test_attaches_article_summary(monkeypatch):
    setup(monkeypatch, [item("Microsoft beats estimates", link="https://a")],
          summaries={"https://a": ("Azure grew 40%.", "Revenue rose 15% year over year.")})
    out = news_collector.fetch_news_headlines("MSFT", 5, now=NOW)
    assert out[0]["summary"] == "Azure grew 40%. Revenue rose 15% year over year."
    assert out[0]["link"] == "https://a"
    assert out[0]["publisher"] == "Wire"


def test_summary_failure_still_returns_headline(monkeypatch):
    setup(monkeypatch, [item("Microsoft beats estimates")])
    assert news_collector.fetch_news_headlines("MSFT", 5, now=NOW)[0]["summary"] == ""


def test_long_summary_is_truncated(monkeypatch):
    setup(monkeypatch, [item("Microsoft beats estimates", link="https://a")],
          summaries={"https://a": ("d", "x" * 5000)})
    assert len(news_collector.fetch_news_headlines("MSFT", 5, now=NOW)[0]["summary"]) <= news_collector.SUMMARY_CHARS + 1


def test_extract_article_text_skips_navigation_and_garbage():
    page = """
    <meta property="og:description" content="Microsoft has been losing ground &amp; more.">
    <p>oHNrc0)e'ozMZ!ohLuGM))~hr'p5)']rB){Ieocvesno>=AtBc0LoGIei}5'oNwpD x[)=rd1s(er:)+Y&amp;Geym5t_( t([nlAeo</p>
    <p>Skip to navigation   Skip to main content   Skip to right column   Home   News   Finance   Sports</p>
    <p>short</p>
    <p>Microsoft (NASDAQ: MSFT) has a major event coming up on Oct. 28: its <a href="#">earnings report</a> for fiscal 2027.</p>
    <p>The question is whether Microsoft can finish the year strong or if there's another sell-off waiting.</p>
    <p>A third paragraph that should not be included because two are enough for a summary here.</p>
    """
    description, lead = news_collector.extract_article_text(page)
    assert description == "Microsoft has been losing ground & more."
    assert lead.startswith("Microsoft (NASDAQ: MSFT) has a major event coming up on Oct. 28: its earnings report")
    assert "sell-off waiting." in lead
    assert "third paragraph" not in lead


# --- sources ---

def test_prefers_ticker_news_when_available(monkeypatch):
    ticker_news = [{"content": {"title": "Microsoft from ticker", "canonicalUrl": {"url": "https://t"},
                                "summary": "Feed summary.", "provider": {"displayName": "Reuters"}}}]
    fetched = setup(monkeypatch, [item("Microsoft from search")], ticker_news=ticker_news)
    out = news_collector.fetch_news_headlines("MSFT", 1, now=NOW)
    assert titles(out) == ["Microsoft from ticker"]
    assert out[0]["summary"] == "Feed summary."
    assert fetched == []  # feed already had a summary


def test_warns_when_both_sources_are_empty(monkeypatch, capsys):
    setup(monkeypatch, [])
    assert news_collector.fetch_news_headlines("MSFT", now=NOW) == []
    assert "no news" in capsys.readouterr().out.lower()


def test_company_aliases_strip_legal_suffixes(monkeypatch):
    news_collector._company_name.cache_clear()
    monkeypatch.setattr(news_collector.yf, "Ticker",
                        lambda t: SimpleNamespace(info={"shortName": "Microsoft Corporation"}))
    assert news_collector.company_aliases("MSFT") == ["Microsoft"]


def test_company_aliases_add_distinctive_first_word(monkeypatch):
    news_collector._company_name.cache_clear()
    monkeypatch.setattr(news_collector.yf, "Ticker",
                        lambda t: SimpleNamespace(info={"shortName": "Meta Platforms, Inc."}))
    assert news_collector.company_aliases("META") == ["Meta Platforms", "Meta"]


def test_company_aliases_skip_generic_first_word(monkeypatch):
    news_collector._company_name.cache_clear()
    monkeypatch.setattr(news_collector.yf, "Ticker",
                        lambda t: SimpleNamespace(info={"shortName": "Advanced Micro Devices, Inc."}))
    assert news_collector.company_aliases("AMD") == ["Advanced Micro Devices"]


def test_company_aliases_survive_lookup_failure(monkeypatch):
    news_collector._company_name.cache_clear()

    def boom(t):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(news_collector.yf, "Ticker", boom)
    assert news_collector.company_aliases("MSFT") == []


def test_searches_by_company_name_when_ticker_search_is_noisy(monkeypatch):
    by_query = {
        "MSFT": [item("Social Security COLA in 2027", related=("GOOG", "MSFT"))],
        "Microsoft stock": [item("Microsoft stock jumps on Azure deal", age_hours=2),
                            item("Microsoft beats estimates", age_hours=1)],
    }
    setup(monkeypatch, [])
    monkeypatch.setattr(news_collector.yf, "Search",
                        lambda q, news_count: SimpleNamespace(news=by_query.get(q, [])))
    out = news_collector.fetch_news_headlines("MSFT", 5, now=NOW)
    assert titles(out) == ["Microsoft beats estimates", "Microsoft stock jumps on Azure deal"]  # newest first


def test_summary_does_not_repeat_description(monkeypatch):
    setup(monkeypatch, [item("Microsoft beats estimates", link="https://a")],
          summaries={"https://a": ("Azure grew 40%.", "Azure grew 40%. Revenue rose 15%.")})
    assert news_collector.fetch_news_headlines("MSFT", 5, now=NOW)[0]["summary"] == "Azure grew 40%. Revenue rose 15%."


def test_extract_article_text_tidies_spacing_around_tags():
    page = "<p>Meta Platforms ( <a>META</a> ) will kick off Big Tech's earnings season when it reports results after the bell.</p>"
    assert news_collector.extract_article_text(page)[1].startswith("Meta Platforms (META) will kick off")
