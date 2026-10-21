from datetime import date
from types import SimpleNamespace
import filings

TODAY = date(2026, 10, 21)
UA = "Sample Company Name AdminContact@sample.com"

SUBMISSIONS = {
    "filings": {"recent": {
        "form":            ["8-K",                  "10-Q",                 "8-K",                  "8-K"],
        "filingDate":      ["2026-10-20",           "2026-10-19",           "2026-10-16",           "2026-09-02"],
        "accessionNumber": ["0001193125-26-000001", "0001193125-26-000002", "0001193125-26-000003", "0001193125-26-000004"],
        "primaryDocument": ["d1.htm",               "q.htm",                "d3.htm",               "d4.htm"],
        "items":           ["5.02,9.01",            "",                     "2.02,7.01,9.01",       "7.01"],
    }}
}


def fake_get(responses):
    calls = []

    def get(url, headers, timeout):
        calls.append((url, headers["User-Agent"]))
        status, body = responses.get(url, (404, {}))
        return SimpleNamespace(ok=status == 200, status_code=status, json=lambda: body,
                               text=body if isinstance(body, str) else "")

    return get, calls


def setup(monkeypatch, responses, ua=UA):
    filings.clear_cache()
    monkeypatch.setattr(filings, "user_agent", lambda: ua)
    get, calls = fake_get(responses)
    monkeypatch.setattr(filings.requests, "get", get)
    return calls


RESPONSES = {
    filings.TICKER_MAP_URL: (200, {"0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}}),
    filings.SUBMISSIONS_URL.format(cik=789019): (200, SUBMISSIONS),
}


def test_recent_8k_filings_with_item_descriptions(monkeypatch):
    calls = setup(monkeypatch, RESPONSES)
    out = filings.fetch_recent_filings("MSFT", today=TODAY)
    assert out == [
        {"form": "8-K", "date": "2026-10-20", "items": ["5.02"],
         "url": "https://www.sec.gov/Archives/edgar/data/789019/000119312526000001/d1.htm", "summary": ""},
        {"form": "8-K", "date": "2026-10-16", "items": ["2.02", "7.01"],
         "url": "https://www.sec.gov/Archives/edgar/data/789019/000119312526000003/d3.htm", "summary": ""},
    ]
    assert all(ua == UA for _, ua in calls)


def test_no_user_agent_skips_sec_entirely(monkeypatch):
    calls = setup(monkeypatch, RESPONSES, ua=None)
    assert filings.fetch_recent_filings("MSFT", today=TODAY) == []
    assert calls == []


def test_unknown_ticker_returns_nothing(monkeypatch):
    setup(monkeypatch, RESPONSES)
    assert filings.fetch_recent_filings("ZZZZ", today=TODAY) == []


def test_sec_errors_return_nothing(monkeypatch):
    setup(monkeypatch, {filings.TICKER_MAP_URL: (403, {})})
    assert filings.fetch_recent_filings("MSFT", today=TODAY) == []


def test_ticker_map_is_fetched_once(monkeypatch):
    calls = setup(monkeypatch, RESPONSES)
    filings.fetch_recent_filings("MSFT", today=TODAY)
    filings.fetch_recent_filings("MSFT", today=TODAY)
    assert [url for url, _ in calls].count(filings.TICKER_MAP_URL) == 1


def test_class_share_tickers_match_sec_format(monkeypatch):
    setup(monkeypatch, {filings.TICKER_MAP_URL: (200, {"0": {"cik_str": 1067983, "ticker": "BRK-B"}})})
    assert filings.ticker_to_cik("BRK.B") == 1067983


def test_fact_lines():
    lines = filings.fact_lines([{"form": "8-K", "date": "2026-10-16", "items": ["2.02", "7.01"], "url": "https://u"}])
    assert lines == ["SEC 8-K filed 2026-10-16: Item 2.02 Results of Operations and Financial Condition; "
                     "Item 7.01 Regulation FD Disclosure (https://u)"]


def test_fact_lines_unknown_item_code():
    assert filings.fact_lines([{"form": "8-K", "date": "d", "items": ["6.05"], "url": "u"}]) == ["SEC 8-K filed d: Item 6.05 (u)"]


def test_user_agent_from_env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert filings.user_agent() == UA


def test_user_agent_from_config(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f'sec:\n  user_agent: "{UA}"\n')
    assert filings.user_agent(str(cfg)) == UA


def test_user_agent_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert filings.user_agent(str(tmp_path / "nope.yaml")) is None


# --- exhibit 99.1 press releases ---

INDEX_PAGE = """
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/789019/000119312526000003/d3.htm">d3.htm</a></td><td>8-K</td><td>63346</td></tr>
<tr><td>2</td><td>EX-99.1</td><td><a href="/Archives/edgar/data/789019/000119312526000003/msft-ex99_1.htm">msft-ex99_1.htm</a></td><td>EX-99.1</td><td>901600</td></tr>
</table>
"""

PRESS_RELEASE = """
<DOCUMENT><TYPE>EX-99.1<SEQUENCE>2<FILENAME>msft-ex99_1.htm<TEXT>
<html><head><title>msft-ex99_1</title><style>p {margin:0}</style></head><body>
<p>EX-99.1</p>
<p style="text-align:center"><b>Microsoft Cloud and AI Strength Fuels Fourth Quarter Results</b></p>
<p>REDMOND, Wash. &#8212; July 29, 2026 &#8212; Microsoft Corp. today announced the following results for the quarter:</p>
<div>&#8226; Revenue was $90.0 billion and increased 18% (up 17% in constant currency)</div>
<div>&#8226; Diluted earnings per share was $4.81 and increased 32% on a GAAP basis</div>
<table><tr><td>($ in millions)</td><td>2026</td></tr></table>
</body></html>
"""

INDEX_URL = "https://www.sec.gov/Archives/edgar/data/789019/000119312526000003/0001193125-26-000003-index.htm"
EXHIBIT_URL = "https://www.sec.gov/Archives/edgar/data/789019/000119312526000003/msft-ex99_1.htm"


def test_find_exhibit_url_from_filing_index():
    assert filings.find_exhibit_url(INDEX_PAGE) == EXHIBIT_URL


def test_find_exhibit_url_missing():
    assert filings.find_exhibit_url("<table><tr><td>8-K</td></tr></table>") is None


def test_extract_press_release_keeps_headline_and_highlights():
    text = filings.extract_press_release(PRESS_RELEASE)
    assert text.startswith("Microsoft Cloud and AI Strength Fuels Fourth Quarter Results REDMOND, Wash. — July 29")
    assert "Revenue was $90.0 billion and increased 18%" in text
    assert "EX-99.1" not in text
    assert "margin" not in text
    assert "msft-ex99_1" not in text


def test_extract_press_release_is_capped():
    page = "<p>" + "Revenue grew strongly this quarter. " * 200 + "</p>"
    assert len(filings.extract_press_release(page)) <= filings.PRESS_RELEASE_CHARS + 1


def test_recent_filings_attach_press_release(monkeypatch):
    setup(monkeypatch, {**RESPONSES, INDEX_URL: (200, INDEX_PAGE), EXHIBIT_URL: (200, PRESS_RELEASE)})
    out = filings.fetch_recent_filings("MSFT", today=TODAY)
    assert out[0]["summary"] == ""  # 5.02 filing without an exhibit index here
    assert out[1]["summary"].startswith("Microsoft Cloud and AI Strength")


def test_fact_lines_include_press_release():
    lines = filings.fact_lines([{"form": "8-K", "date": "2026-10-16", "items": ["2.02"], "url": "https://u",
                                 "summary": "Revenue was $90.0 billion."}])
    assert lines == ["SEC 8-K filed 2026-10-16: Item 2.02 Results of Operations and Financial Condition (https://u)\n"
                     "    Press release (EX-99.1): Revenue was $90.0 billion."]
