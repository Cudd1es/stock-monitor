import json
import re
from typing import Any, Dict, List, Optional
import yaml
from llm_interaction import ask_llm
from settings import TickerRule, normalize_symbol

EDIT_PROMPT = """You edit the watchlist of a stock-monitoring agent.
The user describes a change in natural language (possibly Chinese/English mixed).
You MUST return ONLY a JSON array of operations (no extra text, no code fences):

[
  {{"op": "add", "symbol": "NVDA", "alert_threshold": 3}},
  {{"op": "update", "symbol": "SPY", "intraday": false}},
  {{"op": "remove", "symbol": "MSFT"}}
]

Operations:
- "add": start watching a ticker that is not in the current watchlist
- "update": change settings of a ticker already in the watchlist
- "remove": stop watching a ticker

Optional fields on "add" / "update" (include ONLY the ones the user asked to set):
- "alert_threshold": number, percent move vs previous close that triggers an alert; "3%" -> 3
- "intraday": boolean, whether to check the ticker for moves during market hours;
  "no need to watch intraday" / "不用盯盘" -> false
- "research": boolean, whether to fetch news, SEC filings and analyst views;
  "no news" / "不要新闻" -> false

Rules:
- Use the ticker symbol as listed on Yahoo Finance, uppercase (e.g. "Nvidia" -> "NVDA", Berkshire B -> "BRK-B").
- Only use "update" / "remove" for tickers in the current watchlist, and "add" for tickers not in it.
- If the request changes nothing, return [].

Current watchlist (defaults already applied):
{watchlist}

User request:
<<<
{request}
>>>"""


def _extract_json_array(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("["):
        match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
        if match:
            raw = match.group(0)
    return raw


def parse_watchlist_edit(text: str, current: List[TickerRule], model: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Use the LLM to turn a natural-language request into watchlist edit operations.
    Only the shape is checked here; apply_ops validates the fields.
    """
    watchlist = yaml.safe_dump([vars(rule) for rule in current], sort_keys=False) if current else "(empty)"
    raw = ask_llm(EDIT_PROMPT.format(watchlist=watchlist, request=text), model=model) or ""
    try:
        ops = json.loads(_extract_json_array(raw))
    except ValueError as e:
        raise ValueError(f"LLM did not return a JSON array: {e}\nLLM raw output: {raw[:300]}")
    if not isinstance(ops, list):
        raise ValueError(f"LLM output is not a JSON array: {raw[:300]}")
    for op in ops:
        if not isinstance(op, dict) or "op" not in op or "symbol" not in op:
            raise ValueError(f"each operation needs `op` and `symbol`, got {op!r}")
        op["symbol"] = normalize_symbol(op["symbol"])
    return ops
