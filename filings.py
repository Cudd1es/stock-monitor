import html
import os
import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional
import requests
import yaml

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{accession_dashed}-index.htm"
FORMS = {"8-K", "8-K/A", "6-K"}  # 6-K: current reports from foreign issuers (e.g. ADRs)
LOOKBACK_DAYS = 7
TIMEOUT = 10
MAX_PRESS_RELEASES = 2      # exhibits fetched per ticker (they can be ~1 MB each)
PRESS_RELEASE_CHARS = 1500  # headline + highlights; the financial tables come later

# 8-K item codes; 9.01 (exhibits) is left out because it accompanies almost every filing
ITEMS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.05": "Material Cybersecurity Incident",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers; Compensation Arrangements",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}


def user_agent(config_path: str = "config.yaml") -> Optional[str]:
    """
    SEC asks automated clients to identify themselves with a contact, e.g. "Jane Doe jane@example.com",
    and rejects requests without one. Read from SEC_USER_AGENT, then config.yaml (sec.user_agent).
    """
    if os.getenv("SEC_USER_AGENT"):
        return os.getenv("SEC_USER_AGENT")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return None
    sec = cfg.get("sec") if isinstance(cfg, dict) else None
    return (sec or {}).get("user_agent") or None


def _get(url: str, ua: str):
    try:
        resp = requests.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
    except Exception as e:
        print(f"[WARN][sec] request failed: {e}")
        return None
    if not resp.ok:
        print(f"[WARN][sec] {url} -> HTTP {resp.status_code}")
        return None
    return resp


def _get_json(url: str, ua: str) -> Optional[Any]:
    resp = _get(url, ua)
    return resp.json() if resp else None


def find_exhibit_url(index_page: str) -> Optional[str]:
    """URL of the EX-99.1 press release from a filing's index page (falls back to the first EX-99.x)."""
    exhibits = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", index_page, flags=re.DOTALL | re.IGNORECASE):
        cells = [" ".join(re.sub(r"<[^>]+>", " ", c).split())
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL | re.IGNORECASE)]
        link = re.search(r'href="(/Archives/[^"]+)"', row)
        if link and cells and re.fullmatch(r"EX-99(\.\d+)?", cells[-2] if len(cells) >= 2 else ""):
            exhibits.append((cells[-2], "https://www.sec.gov" + link.group(1)))
    for kind, url in exhibits:
        if kind in ("EX-99.1", "EX-99.01"):
            return url
    return exhibits[0][1] if exhibits else None


def extract_press_release(page: str) -> str:
    """Opening text of a press release exhibit: headline, dateline and highlight bullets."""
    body = re.sub(r"(?is)<(head|script|style|table)\b.*?</\1>", " ", page)
    body = re.sub(r"(?is)<(type|sequence|filename|description)>[^<\n]*", " ", body)  # EDGAR SGML header
    out, size = [], 0
    for block in re.split(r"(?i)</(?:p|div|li|h\d)>|<br\s*/?>", body):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", block)).split())
        text = text.lstrip("•·▪- ").strip()
        if len(text) < 30 or re.fullmatch(r"EX-99[\d.]*", text):
            continue
        out.append(text)
        size += len(text) + 1
        if size >= PRESS_RELEASE_CHARS:
            break
    joined = " ".join(out)
    return joined if len(joined) <= PRESS_RELEASE_CHARS else joined[:PRESS_RELEASE_CHARS].rsplit(" ", 1)[0] + "…"


def _press_release(cik: int, accession: str, ua: str) -> str:
    index = _get(INDEX_URL.format(cik=cik, accession=accession.replace("-", ""), accession_dashed=accession), ua)
    url = find_exhibit_url(index.text) if index else None
    exhibit = _get(url, ua) if url else None
    return extract_press_release(exhibit.text) if exhibit else ""


@lru_cache(maxsize=1)
def _ticker_map(ua: str) -> Dict[str, int]:
    data = _get_json(TICKER_MAP_URL, ua)
    if not data:
        raise RuntimeError("SEC ticker map unavailable")  # raise so lru_cache retries next time
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}


def clear_cache() -> None:
    _ticker_map.cache_clear()


def ticker_to_cik(ticker: str) -> Optional[int]:
    ua = user_agent()
    if not ua:
        return None
    try:
        mapping = _ticker_map(ua)
    except RuntimeError:
        return None
    # SEC writes share classes with a dash (BRK-B), Yahoo with a dot (BRK.B)
    return mapping.get(ticker.upper()) or mapping.get(ticker.upper().replace(".", "-"))


def fetch_recent_filings(ticker: str, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Current reports (8-K / 6-K) filed in the last LOOKBACK_DAYS, newest first."""
    ua = user_agent()
    if not ua:
        return []
    cik = ticker_to_cik(ticker)
    if cik is None:
        return []
    data = _get_json(SUBMISSIONS_URL.format(cik=cik), ua)
    if not data:
        return []
    recent = data.get("filings", {}).get("recent", {})
    cutoff = ((today or date.today()) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    out = []
    for i, form in enumerate(recent.get("form", [])):
        filed = recent["filingDate"][i]
        if form not in FORMS or filed < cutoff:
            continue
        items = [code for code in (recent.get("items", [""] * (i + 1))[i] or "").split(",") if code and code != "9.01"]
        out.append({
            "form": form,
            "date": filed,
            "items": items,
            "url": ARCHIVE_URL.format(cik=cik, accession=recent["accessionNumber"][i].replace("-", ""),
                                      document=recent["primaryDocument"][i]),
            "accession": recent["accessionNumber"][i],
        })
    out.sort(key=lambda f: f["date"], reverse=True)
    for n, f in enumerate(out):
        accession = f.pop("accession")
        f["summary"] = _press_release(cik, accession, ua) if n < MAX_PRESS_RELEASES else ""
    return out


def fact_lines(filings: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for f in filings:
        items = "; ".join(f"Item {code} {ITEMS[code]}" if code in ITEMS else f"Item {code}" for code in f["items"])
        line = f"SEC {f['form']} filed {f['date']}: {items or 'current report'} ({f['url']})"
        if f.get("summary"):
            line += f"\n    Press release (EX-99.1): {f['summary']}"
        lines.append(line)
    return lines
