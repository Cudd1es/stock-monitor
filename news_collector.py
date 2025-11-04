from typing import Dict, List, Optional
import yfinance as yf

def fetch_news_headlines(ticker: str, topk: int = 3) -> List[Dict[str, str]]:
    """
    Robust extractor for yfinance Ticker(...).news
    Returns a list of {title, link}, best-effort across schema variants.
    """
    out: List[Dict[str, str]] = []
    try:
        t = yf.Ticker(ticker)
        items = t.news or []
    except Exception:
        return out

    for it in items:
        title = None
        link = None

        # Newer schema (nested under "content")
        content = it.get("content") if isinstance(it, dict) else None
        if isinstance(content, dict):
            # title
            title = content.get("title") or content.get("summary") or content.get("description")
            # link (prefer canonical URL, then clickThrough)
            canon = content.get("canonicalUrl") or {}
            click = content.get("clickThroughUrl") or {}
            if isinstance(canon, dict):
                link = canon.get("url") or link
            if isinstance(click, dict):
                link = link or click.get("url")

        # Legacy/flat schema fallback
        if not title:
            title = it.get("title") or it.get("summary") or it.get("description")
        if not link:
            link = it.get("link")  # sometimes present in old schema

        # Final guard: must have a title
        if title:
            out.append({"title": str(title), "link": str(link or "")})

        if len(out) >= topk:
            break

    # De-duplicate by title (simple heuristic)
    seen = set()
    deduped = []
    for x in out:
        key = x["title"].strip()
        if key not in seen:
            seen.add(key)
            deduped.append(x)

    return deduped[:topk]