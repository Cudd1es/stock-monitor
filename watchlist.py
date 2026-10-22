import argparse
import os
import sys
from typing import Any, Callable, Dict, List, Optional
import parser
import settings
import ticker_checker
from settings import OVERRIDE_FIELDS, TickerDefaults, TickerRule

OPS = ("add", "update", "remove")


def apply_ops(rules: List[TickerRule], ops: List[Dict[str, Any]], defaults: TickerDefaults) -> List[TickerRule]:
    """
    Apply add/update/remove operations and return a new, validated watchlist (`rules` is not modified).
    Shared by the CLI and the Discord bot; fields are checked exactly like when the file is loaded.
    """
    entries = {rule.symbol: {name: getattr(rule, name) for name in OVERRIDE_FIELDS} for rule in rules}
    for op in ops:
        kind = op.get("op")
        symbol = settings.normalize_symbol(op.get("symbol", ""))
        fields = {k: v for k, v in op.items() if k not in ("op", "symbol")}
        if kind not in OPS:
            raise ValueError(f"unknown operation {kind!r}; expected one of {OPS}")
        if kind == "add":
            if symbol in entries:
                raise ValueError(f"{symbol} is already in the watchlist")
            entries[symbol] = fields
        elif symbol not in entries:
            raise ValueError(f"{symbol} is not in the watchlist")
        elif kind == "update":
            entries[symbol].update(fields)
        else:
            del entries[symbol]
    return settings.parse_rules([{"symbol": s, **f} for s, f in entries.items()], defaults)


def _describe(rule: TickerRule) -> str:
    return f"{rule.symbol} threshold={rule.alert_threshold} intraday={rule.intraday} research={rule.research}"


def diff_lines(before: List[TickerRule], after: List[TickerRule]) -> List[str]:
    old = {r.symbol: r for r in before}
    new = {r.symbol: r for r in after}
    lines = [f"- {s}" for s in old if s not in new]
    for symbol, rule in new.items():
        if symbol not in old:
            lines.append(f"+ {_describe(rule)}")
            continue
        for name in OVERRIDE_FIELDS:
            was, now = getattr(old[symbol], name), getattr(rule, name)
            if was != now:
                lines.append(f"~ {symbol} {name}: {was} → {now}")
    return lines


def main(argv: Optional[List[str]] = None, input_fn: Callable[[str], str] = input) -> int:
    ap = argparse.ArgumentParser(description="Show or edit the watchlist in natural language")
    ap.add_argument("request", help='"list", or a change such as "add NVDA, alert me at 3%%"')
    ap.add_argument("--yes", action="store_true", help="write without asking for confirmation")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--watchlist", default="watchlist.yaml")
    args = ap.parse_args(argv)

    try:
        defaults = settings.load_settings(args.config).defaults
        exists = os.path.exists(args.watchlist)
        current = settings.load_watchlist(args.watchlist, defaults) if exists else []

        if args.request.strip().lower() == "list":
            if not exists:
                print(f'{args.watchlist} does not exist yet; create it with: python watchlist.py "<what to watch>"')
            for rule in current:
                print(_describe(rule))
            return 0

        updated = apply_ops(current, parser.parse_watchlist_edit(args.request, current), defaults)
        known = {r.symbol for r in current}
        for rule in updated:
            if rule.symbol not in known and ticker_checker.get_previous_close(rule.symbol) is None:
                raise ValueError(f"no price data for {rule.symbol}; is the symbol right?")

        changes = diff_lines(current, updated)
        if not changes:
            print("no change")
            return 0
        print("\n".join(changes))
        if not args.yes and input_fn("Write these changes? [y/N] ").strip().lower() not in ("y", "yes"):
            print("not written")
            return 0
        settings.dump_watchlist(updated, args.watchlist, defaults)
        print(f"written to {args.watchlist}")
        return 0
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
