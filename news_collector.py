import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
import requests
import yfinance as yf

SEARCH_COUNT = 20       # candidates pulled before relevance filtering
MAX_AGE_HOURS = 72      # older stories rarely explain today's move
MAX_PAGE_FETCHES = 8    # article pages fetched per ticker
SUMMARY_CHARS = 600
PAGE_TIMEOUT = 8
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

LEGAL_SUFFIX = re.compile(
    r"(,?\s+(inc\.?|incorporated|corporation|corp\.?|company|co\.?|ltd\.?|limited|plc|holdings?|group|"
    r"n\.v\.|s\.a\.|ag|se|class [a-c]))+$|\.com$",
    flags=re.IGNORECASE,
)
# first words too common to identify a company on their own
GENERIC_FIRST_WORDS = {
    "advanced", "american", "applied", "bank", "first", "general", "global", "international",
    "national", "new", "taiwan", "the", "united", "china", "japan", "royal", "southern", "western",
}


@lru_cache(maxsize=256)
def _company_name(ticker: str) -> str:
    # not wrapped in try: lru_cache doesn't cache exceptions, so a failed lookup is retried next time
    info = yf.Ticker(ticker).info or {}
    return info.get("shortName") or info.get("longName") or ""


def company_aliases(ticker: str) -> List[str]:
    """Names a headline may use for the company, e.g. "Meta Platforms, Inc." -> ["Meta Platforms", "Meta"]."""
    try:
        name = _company_name(ticker)
    except Exception:
        return []
    while True:
        stripped = LEGAL_SUFFIX.sub("", name).strip(" ,")
        if stripped == name:
            break
        name = stripped
    if not name:
        return []
    aliases = [name]
    first = name.split()[0].strip(",.")
    if first != name and len(first) >= 3 and first.lower() not in GENERIC_FIRST_WORDS:
        aliases.append(first)
    return aliases


def mentions(text: str, ticker: str, aliases: List[str]) -> bool:
    """Whole-word match on the ticker (case-sensitive) or any alias (case-insensitive)."""
    if not text:
        return False
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", text):
        return True
    return any(re.search(rf"\b{re.escape(a)}\b", text, flags=re.IGNORECASE) for a in aliases)


def extract_article_text(page: str) -> Tuple[str, str]:
    """(meta description, first two real paragraphs) from an article page."""
    description = ""
    m = re.search(r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]*)"', page)
    if m:
        description = html.unescape(m.group(1)).strip()

    paragraphs = []
    for raw in re.findall(r"<p[^>]*>(.*?)</p>", page, flags=re.DOTALL):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
        text = re.sub(r"\s+([,.:;!?)])", r"\1", text)
        text = re.sub(r"\(\s+", "(", text)
        if not 80 <= len(text) <= 2000 or "Skip to" in text:
            continue
        # scrambled/obfuscated blobs are mostly punctuation
        if sum(c.isalnum() or c.isspace() for c in text) / len(text) < 0.9:
            continue
        paragraphs.append(text)
        if len(paragraphs) == 2:
            break
    return description, " ".join(paragraphs)


def fetch_article_text(url: str) -> Tuple[str, str]:
    if not url:
        return "", ""
    try:
        resp = requests.get(url, timeout=PAGE_TIMEOUT, headers={"User-Agent": USER_AGENT})
        if not resp.ok:
            return "", ""
        return extract_article_text(resp.text)
    except Exception:
        return "", ""


def _search(query: str) -> list:
    try:
        return yf.Search(query, news_count=SEARCH_COUNT).news or []
    except Exception:
        return []


def _fetch_raw_items(ticker: str) -> list:
    """
    Raw news items from Ticker(...).news, falling back to Search(...).news when empty.
    """
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        items = []
    if items:
        return items
    # Yahoo's ticker news endpoint (/xhr/ncp) returns 404 as of 2026-10; fall back to search
    items = _search(ticker)
    if not items:
        print(f"[WARN][news] no news from Ticker.news or Search for {ticker}")
    return items


def _parse_time(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _normalize(it: Any) -> Optional[Dict[str, Any]]:
    """
    One news item across schema variants: the nested "content" schema (Ticker.news)
    and the flat schema (Search.news).
    """
    if not isinstance(it, dict):
        return None
    content = it.get("content") if isinstance(it.get("content"), dict) else {}
    title = content.get("title") or it.get("title")
    if not title:
        return None
    link = None
    for key in ("canonicalUrl", "clickThroughUrl"):
        if isinstance(content.get(key), dict):
            link = link or content[key].get("url")
    provider = content.get("provider") if isinstance(content.get("provider"), dict) else {}
    return {
        "title": str(title).strip(),
        "link": str(link or it.get("link") or ""),
        "publisher": provider.get("displayName") or it.get("publisher") or "",
        "published": _parse_time(content.get("pubDate") or it.get("providerPublishTime")),
        "summary": str(content.get("summary") or content.get("description") or "").strip(),
        "related": it.get("relatedTickers") or [],
    }


def _truncate(text: str, limit: int = SUMMARY_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def fetch_news_headlines(ticker: str, topk: int = 3, now: Optional[float] = None) -> List[Dict[str, str]]:
    """
    Recent news that is actually about `ticker`, each as {title, link, publisher, summary}.

    Yahoo tags a ticker on many unrelated stories (e.g. through ads embedded in the article),
    so a story is kept only when its headline names the ticker/company, or when the ticker is
    the story's primary ticker and the article's own description names the company.
    """
    now = time.time() if now is None else now
    aliases = company_aliases(ticker)

    seen = set()
    candidates = []

    def add_candidates(raw_items: list) -> None:
        for raw in raw_items:
            item = _normalize(raw)
            if not item or item["title"] in seen:
                continue
            seen.add(item["title"])
            if item["published"] is not None and now - item["published"] > MAX_AGE_HOURS * 3600:
                continue
            candidates.append(item)

    def named_in_title() -> List[Dict[str, Any]]:
        return [it for it in candidates if mentions(it["title"], ticker, aliases)]

    add_candidates(_fetch_raw_items(ticker))
    if len(named_in_title()) < topk and aliases:
        # the ticker search can be swamped by loosely tagged stories; a name search is more on-topic
        add_candidates(_search(f"{aliases[-1]} stock"))

    strong = sorted(named_in_title(), key=lambda it: it["published"] or 0, reverse=True)[:topk]
    weak = [it for it in candidates
            if it not in strong and it["related"][:1] == [ticker]][:max(0, MAX_PAGE_FETCHES - len(strong))]

    # fetch article text only where the feed gave no summary, or where relevance must be confirmed
    to_fetch = [it for it in strong if not it["summary"]] + weak
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = dict(zip((id(it) for it in to_fetch), pool.map(lambda it: fetch_article_text(it["link"]), to_fetch)))

    kept = []
    for it in strong + weak:
        description, lead = pages.get(id(it), ("", ""))
        if it in weak and not mentions(description, ticker, aliases):
            continue
        if not it["summary"]:
            # many articles open with the same sentence as their meta description
            if description and description[:60] in lead:
                description = ""
            it["summary"] = " ".join(p for p in (description, lead) if p)
        kept.append({
            "title": it["title"],
            "link": it["link"],
            "publisher": it["publisher"],
            "summary": _truncate(it["summary"]),
        })
        if len(kept) >= topk:
            break
    return kept
