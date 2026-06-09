"""Prophet signal generation with rolling 3-year window (Points 2 & 4).

- Training window: last 3 years of *filtered* data, ending at `as_of`.
- Forecast horizon: 30 calendar days.
- Concentration diagnostic: flags when >70% of tickers share the same direction.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .data_loader import LoadResult

log = logging.getLogger(__name__)

TRAINING_YEARS = 3
HORIZON_DAYS = 30
CONCENTRATION_THRESHOLD = 0.70
SIGNAL_THRESHOLDS = {  # target/last - 1
    "ACHAT FORT": 0.10,
    "ACHAT": 0.03,
    "NEUTRE": -0.03,
    "VENTE": -0.10,
}


@dataclass
class TickerSignal:
    ticker: str
    last_price: float
    last_date: str
    signal: str
    confidence: str
    target_30d: float
    trend: str
    yhat: float
    yhat_lower: float
    yhat_upper: float
    train_start: str
    train_end: str
    train_rows: int
    train_annualized_return: float


def _classify(target: float, last: float) -> str:
    if last <= 0:
        return "NEUTRE"
    chg = target / last - 1.0
    if chg >= SIGNAL_THRESHOLDS["ACHAT FORT"]:
        return "ACHAT FORT"
    if chg >= SIGNAL_THRESHOLDS["ACHAT"]:
        return "ACHAT"
    if chg >= SIGNAL_THRESHOLDS["NEUTRE"]:
        return "NEUTRE"
    if chg >= SIGNAL_THRESHOLDS["VENTE"]:
        return "VENTE"
    return "VENTE FORTE"


def _confidence(yhat_lower: float, yhat_upper: float, yhat: float) -> str:
    if yhat <= 0:
        return "Basse"
    rel_width = (yhat_upper - yhat_lower) / yhat
    if rel_width < 0.15:
        return "Haute"
    if rel_width < 0.30:
        return "Moyenne"
    return "Basse"


def _annualized_return(series: pd.Series) -> float:
    if len(series) < 2 or series.iloc[0] <= 0:
        return 0.0
    days = (series.index[-1] - series.index[0]).days
    if days <= 0:
        return 0.0
    total = series.iloc[-1] / series.iloc[0]
    if total <= 0:
        return 0.0
    return float(total ** (365.0 / days) - 1.0)


def _train_window(df: pd.DataFrame, as_of: pd.Timestamp, years: int = TRAINING_YEARS) -> pd.DataFrame:
    cutoff_high = as_of
    cutoff_low = as_of - pd.DateOffset(years=years)
    window = df[(df["ds"] >= cutoff_low) & (df["ds"] <= cutoff_high)]
    return window.reset_index(drop=True)


def forecast_ticker(
    result: LoadResult,
    as_of: pd.Timestamp,
    horizon_days: int = HORIZON_DAYS,
) -> TickerSignal | None:
    """Fit Prophet on the rolling window ending at `as_of` and return a signal.

    Returns None if the ticker was excluded by the liquidity filter or has
    insufficient training data.
    """
    from prophet import Prophet  # lazy import: heavy dependency

    if result.excluded:
        log.warning("ticker=%s excluded (illiquid_ratio=%.2f)", result.ticker, result.illiquid_ratio)
        return None

    train = _train_window(result.df, as_of)
    if len(train) < 60:  # need enough points for Prophet
        log.warning("ticker=%s insufficient training rows=%d", result.ticker, len(train))
        return None

    model = Prophet(
        daily_seasonality=False,
        weekly_seasonality=False,
        yearly_seasonality=True,
        interval_width=0.80,
    )
    model.fit(train[["ds", "y"]])

    future = model.make_future_dataframe(periods=horizon_days, freq="D", include_history=False)
    fcst = model.predict(future)
    last_row = fcst.iloc[-1]

    last_price = float(train["y"].iloc[-1])
    last_date = train["ds"].iloc[-1].date().isoformat()
    yhat = float(last_row["yhat"])
    yhat_lower = float(last_row["yhat_lower"])
    yhat_upper = float(last_row["yhat_upper"])
    target_30d = yhat
    trend = "UP" if yhat > last_price else "DOWN"
    signal = _classify(target_30d, last_price)
    confidence = _confidence(yhat_lower, yhat_upper, yhat)

    train_indexed = train.set_index("ds")["y"]
    ann_ret = _annualized_return(train_indexed)

    return TickerSignal(
        ticker=result.ticker,
        last_price=round(last_price, 2),
        last_date=last_date,
        signal=signal,
        confidence=confidence,
        target_30d=round(target_30d, 2),
        trend=trend,
        yhat=round(yhat, 2),
        yhat_lower=round(yhat_lower, 2),
        yhat_upper=round(yhat_upper, 2),
        train_start=train["ds"].iloc[0].date().isoformat(),
        train_end=train["ds"].iloc[-1].date().isoformat(),
        train_rows=len(train),
        train_annualized_return=round(ann_ret, 4),
    )


def detect_concentration(signals: Iterable[TickerSignal]) -> dict:
    """Return a diagnostic dict; logs a warning if concentration exceeds threshold."""
    sigs = list(signals)
    if not sigs:
        return {"concentrated": False, "dominant_signal": None, "ratio": 0.0, "n": 0}
    counts = pd.Series([s.signal for s in sigs]).value_counts(normalize=True)
    dominant = counts.idxmax()
    ratio = float(counts.iloc[0])
    concentrated = ratio > CONCENTRATION_THRESHOLD
    if concentrated:
        log.warning(
            "Concentration alert: %.1f%% of tickers share signal=%s "
            "(possible training-period bias)", ratio * 100, dominant,
        )
    return {
        "concentrated": concentrated,
        "dominant_signal": dominant,
        "ratio": round(ratio, 4),
        "n": len(sigs),
        "threshold": CONCENTRATION_THRESHOLD,
    }


def signals_to_dashboard_dict(signals: Iterable[TickerSignal]) -> dict:
    """Match the SIGNALS_DATA schema consumed by brvm_dashboard_enriched.html."""
    return {s.ticker: asdict(s) for s in signals}


def signal_to_history_record(sig: TickerSignal, as_of: pd.Timestamp) -> dict:
    """Compact per-date record stored in the signal history time-series."""
    chg = (sig.target_30d / sig.last_price - 1.0) * 100.0 if sig.last_price > 0 else 0.0
    return {
        "as_of": as_of.date().isoformat(),
        "signal": sig.signal,
        "last_price": sig.last_price,
        "target_30d": sig.target_30d,
        "change_pct": round(chg, 2),
        "confidence": sig.confidence,
        "trend": sig.trend,
    }


def load_signal_history(path: Path) -> dict:
    """Load the history JSON, returning an empty skeleton when absent/corrupt."""
    if not path.exists():
        return {"generated_at": None, "tickers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("signal history unreadable (%s); starting fresh", exc)
        return {"generated_at": None, "tickers": {}}
    data.setdefault("tickers", {})
    return data


def upsert_signal_history(
    history: dict,
    signals: Iterable[TickerSignal],
    as_of: pd.Timestamp,
) -> dict:
    """Insert/replace one snapshot per ticker for the given `as_of` date.

    Records are deduped by `as_of` (latest write wins) and kept sorted ascending.
    Mutates and returns `history`.
    """
    tickers = history.setdefault("tickers", {})
    as_of_iso = as_of.date().isoformat()
    for sig in signals:
        series = tickers.setdefault(sig.ticker, [])
        series[:] = [r for r in series if r.get("as_of") != as_of_iso]
        series.append(signal_to_history_record(sig, as_of))
        series.sort(key=lambda r: r["as_of"])
    history["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    return history


def save_signal_history(path: Path, history: dict) -> None:
    """Write the history JSON to disk (pretty-printed, UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
