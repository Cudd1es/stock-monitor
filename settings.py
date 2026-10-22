import os
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

HEADER = "# 由程序维护(python watchlist.py)。手动编辑也可以,但注释会在下次写入时丢失。\n"
NOTIFY_METHODS = ("console", "discord")
LANGUAGES = ("en", "zh", "jp")
MAX_INTRADAY_MINUTES = 390  # one regular session


@dataclass(frozen=True)
class TickerDefaults:
    alert_threshold: float = 5.0
    intraday: bool = True
    research: bool = True


@dataclass(frozen=True)
class TickerRule:
    symbol: str
    alert_threshold: float
    intraday: bool
    research: bool


@dataclass(frozen=True)
class Settings:
    timezone: str = "America/Toronto"
    daily_brief: Optional[str] = "16:10"
    intraday_minutes: Optional[int] = None
    notify_method: str = "console"
    report_language: str = "zh"
    defaults: TickerDefaults = TickerDefaults()
    discord_webhook: Optional[str] = None
    discord_mention_id: Optional[str] = None


OVERRIDE_FIELDS = tuple(f.name for f in fields(TickerDefaults))


def normalize_symbol(raw: str) -> str:
    """Uppercase and keep only letters, digits, '.' and '-' (yfinance writes share classes as BRK-B)."""
    symbol = re.sub(r"[^A-Z0-9.\-]", "", str(raw).strip().upper())
    if not symbol:
        raise ValueError(f"invalid ticker symbol: {raw!r}")
    return symbol


def _read_yaml(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _is_time(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))


def _check_field(name: str, value: Any, where: str) -> Any:
    """Strict types: YAML `true` must not pass as a threshold, nor "no" as a switch."""
    if name == "alert_threshold":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{where}: alert_threshold must be a number > 0, got {value!r}")
        return float(value)
    if not isinstance(value, bool):
        raise ValueError(f"{where}: {name} must be true or false, got {value!r}")
    return value


def _check_overrides(raw: Dict[str, Any], where: str, allowed=OVERRIDE_FIELDS) -> Dict[str, Any]:
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {sorted(unknown)}")
    return {k: _check_field(k, v, where) for k, v in raw.items()}


def load_settings(path: str = "config.yaml") -> Settings:
    try:
        cfg = _read_yaml(path) or {}
    except FileNotFoundError:
        raise ValueError(f"{path} not found; copy config_example.yaml to start")
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")

    schedule = cfg.get("schedule") or {}
    daily_brief = schedule.get("daily_brief", Settings.daily_brief)
    intraday = schedule.get("intraday_minutes", Settings.intraday_minutes)
    if daily_brief is not None and not _is_time(daily_brief):
        raise ValueError(f"{path}: schedule.daily_brief must be HH:MM or null, got {daily_brief!r}")
    if intraday is not None and (isinstance(intraday, bool) or not isinstance(intraday, int)
                                 or not 1 <= intraday <= MAX_INTRADAY_MINUTES):
        raise ValueError(f"{path}: schedule.intraday_minutes must be 1..{MAX_INTRADAY_MINUTES} or null")
    if daily_brief is None and intraday is None:
        raise ValueError(f"{path}: both schedule.daily_brief and schedule.intraday_minutes are off")

    notify = cfg.get("notify_method", Settings.notify_method)
    if notify not in NOTIFY_METHODS:
        raise ValueError(f"{path}: notify_method must be one of {NOTIFY_METHODS}, got {notify!r}")
    language = cfg.get("report_language", Settings.report_language)
    if language not in LANGUAGES:
        raise ValueError(f"{path}: report_language must be one of {LANGUAGES}, got {language!r}")

    defaults = TickerDefaults(**_check_overrides(cfg.get("defaults") or {}, f"{path}: defaults"))
    discord = cfg.get("discord") or {}
    return Settings(
        timezone=cfg.get("timezone", Settings.timezone),
        daily_brief=daily_brief,
        intraday_minutes=intraday,
        notify_method=notify,
        report_language=language,
        defaults=defaults,
        discord_webhook=discord.get("webhook_url"),
        discord_mention_id=discord.get("mention_id"),
    )


def parse_rules(entries: Any, defaults: TickerDefaults) -> List[TickerRule]:
    """Validate the `tickers:` list and merge each entry with the defaults."""
    if not isinstance(entries, list):
        raise ValueError("watchlist: `tickers` must be a list")
    rules, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or "symbol" not in entry:
            raise ValueError(f"watchlist: each ticker needs a `symbol`, got {entry!r}")
        symbol = normalize_symbol(entry["symbol"])
        if symbol in seen:
            raise ValueError(f"watchlist: {symbol} is listed twice")
        seen.add(symbol)
        overrides = _check_overrides({k: v for k, v in entry.items() if k != "symbol"}, f"watchlist: {symbol}")
        merged = {name: overrides.get(name, getattr(defaults, name)) for name in OVERRIDE_FIELDS}
        rules.append(TickerRule(symbol=symbol, **merged))
    return rules


def load_watchlist(path: str, defaults: TickerDefaults) -> List[TickerRule]:
    try:
        data = _read_yaml(path) or {}
    except FileNotFoundError:
        raise ValueError(f'{path} not found; create it with: python watchlist.py "<what to watch>"')
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping with a `tickers` list")
    return parse_rules(data.get("tickers", []), defaults)


def dump_watchlist(rules: List[TickerRule], path: str, defaults: TickerDefaults) -> None:
    """Write only what differs from the defaults, atomically so a running monitor never reads half a file."""
    entries = []
    for rule in rules:
        entry = {"symbol": rule.symbol}
        entry.update({name: getattr(rule, name) for name in OVERRIDE_FIELDS
                      if getattr(rule, name) != getattr(defaults, name)})
        entries.append(entry)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(HEADER + yaml.safe_dump({"tickers": entries}, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    os.replace(tmp, target)
