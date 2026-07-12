"""Backfill the per-ticker Prophet signal history time-series.

Recomputes signals at a regular grid of past `as_of` dates (default: monthly
over the last 18 months) for the portfolio tickers, and writes the result to
data/signals/signals_history.json. The dashboard "Historique Signaux" tab reads
this file to visualise how each asset's signal evolved over time.

Prophet is fit once per (ticker, date), so the run can take several minutes.
By default only tickers present in data/algo/portfolio_latest.json are processed;
use --all-tickers to cover the full liquid universe.

Going forward, generate_signals.py appends each fresh snapshot to the same file,
so this backfill only needs to run once (or to widen the look-back window).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quant.data_loader import load_universe
from quant.signals import (
    forecast_ticker,
    load_signal_history,
    save_signal_history,
    upsert_signal_history,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("build_signals_history")

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument(
        "--months", type=int, default=18,
        help="Look-back window in months (default: 18).",
    )
    parser.add_argument(
        "--freq", type=str, default="MS",
        help="Pandas date frequency for the as_of grid (default: MS = month start).",
    )
    parser.add_argument(
        "--all-tickers", action="store_true",
        help="Backfill every liquid ticker, not just the current portfolio.",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Ignore any existing history file and rebuild from scratch.",
    )
    return parser


def _portfolio_tickers(root: Path) -> set[str]:
    """Tickers held in the latest optimised portfolio allocation."""
    path = root / "data" / "algo" / "portfolio_latest.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return set((data.get("allocation") or {}).keys())


def run(args: argparse.Namespace) -> int:
    actions_dir = args.root / "data" / "actions"
    out_history = args.root / "data" / "signals" / "signals_history.json"

    log.info("Loading universe from %s", actions_dir)
    results = load_universe(actions_dir)

    selected = {r.ticker for r in results if not r.excluded}
    if not args.all_tickers:
        wanted = _portfolio_tickers(args.root)
        if wanted:
            selected &= wanted
            log.info("Restricting to %d portfolio tickers", len(selected))
        else:
            log.warning("portfolio_latest.json not found; backfilling all liquid tickers")
    targets = [r for r in results if r.ticker in selected]
    if not targets:
        log.error("No tickers selected for backfill.")
        return EXIT_FAILURE

    today = pd.Timestamp(datetime.now(tz=timezone.utc).date())
    start = today - pd.DateOffset(months=args.months)
    grid = list(pd.date_range(start=start, end=today, freq=args.freq))
    # Always include "today" as the final snapshot.
    if not grid or grid[-1].date() != today.date():
        grid.append(today)
    log.info(
        "Backfilling %d tickers across %d dates (%s -> %s, freq=%s)",
        len(targets), len(grid), grid[0].date(), grid[-1].date(), args.freq,
    )

    history = {"generated_at": None, "tickers": {}} if args.fresh else load_signal_history(out_history)

    total = len(grid)
    for idx, as_of in enumerate(grid, start=1):
        snapshot = []
        for r in targets:
            sig = forecast_ticker(r, as_of=as_of)
            if sig is not None:
                snapshot.append(sig)
        upsert_signal_history(history, snapshot, as_of)
        log.info("[%d/%d] %s: %d/%d signals", idx, total, as_of.date(), len(snapshot), len(targets))
        # Persist incrementally so a long run is resumable / inspectable.
        save_signal_history(out_history, history)

    n_tickers = len(history.get("tickers", {}))
    log.info("Done. Wrote %s (%d tickers tracked)", out_history, n_tickers)
    return EXIT_SUCCESS


def main() -> int:
    """Main entry point with error handling."""
    parser = create_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI
        log.exception("Backfill failed: %s", exc)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
