"""
Next-day PM2.5 forecast per city: persistence baseline vs a lagged-feature Ridge regression.

    python forecast.py                 # evaluate on the last 14 days, write reports/forecast_results.json

Task      : given everything known up to the end of day D, predict the daily mean PM2.5 of day D+1.
Baseline  : "tomorrow = today" (persistence). Second reference: mean of the last 7 days.
Model     : one Ridge regression per city on [today, yesterday, 2 days ago, 6 days ago, 7-day mean].
Evaluation: time-based holdout. The last HOLDOUT_DAYS target days are the test set; the model is fitted only
            on targets before that, so it never sees the future. Metric: MAE in µg/m³ (lower is better).
Data      : daily_summary (outlier-free daily means). Days with fewer than 20 valid hours and the current,
            still incomplete day are excluded.
"""
import argparse
import json
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

import bootstrap
import pipeline

HOLDOUT_DAYS = 14
MIN_HOURS = 20          # a day needs at least this many valid hourly readings to count
MIN_TRAIN_ROWS = 30
RIDGE_ALPHA = 1.0       # fixed in advance, not tuned on the holdout
FEATURES = ["today", "lag1", "lag2", "lag6", "mean7"]


def load_daily(con, today=None):
    """Wide table: index = date, one column per city, values = daily mean PM2.5 of complete days only."""
    today = pd.Timestamp(today if today is not None else pipeline.now_ist()).normalize()
    d = pd.read_sql("SELECT city, date, avg_pm2_5, n_hours FROM daily_summary", con, parse_dates=["date"])
    d = d[(d.n_hours >= MIN_HOURS) & (d.date < today)]
    return d.pivot(index="date", columns="city", values="avg_pm2_5").sort_index()


def build_features(series):
    """One row per target day T. Every feature is known at the end of day T-1; `target` is the value on day T."""
    s = series.asfreq("D")                       # explicit daily grid: missing days become NaN, never silently skipped
    X = pd.DataFrame({"today": s, "lag1": s.shift(1), "lag2": s.shift(2), "lag6": s.shift(6),
                      "mean7": s.rolling(7, min_periods=7).mean()})
    X["target"] = s.shift(-1)
    X.index = X.index + pd.Timedelta(days=1)     # re-index by the day being predicted
    return X.dropna()


def split_by_time(X, holdout_days=HOLDOUT_DAYS):
    first_test = X.index.max() - pd.Timedelta(days=holdout_days - 1)
    return X[X.index < first_test], X[X.index >= first_test]


def mae(y, pred):
    return float(np.abs(np.asarray(y) - np.asarray(pred)).mean())


def evaluate_city(series, holdout_days=HOLDOUT_DAYS):
    X = build_features(series)
    train, test = split_by_time(X, holdout_days)
    if len(train) < MIN_TRAIN_ROWS or test.empty:
        return None
    model = Ridge(alpha=RIDGE_ALPHA).fit(train[FEATURES], train["target"])
    pred = model.predict(test[FEATURES])
    return {"n_train": len(train), "n_test": len(test),
            "test_start": test.index.min().strftime("%Y-%m-%d"), "test_end": test.index.max().strftime("%Y-%m-%d"),
            "mae_persistence": mae(test["target"], test["today"]),
            "mae_mean7": mae(test["target"], test["mean7"]),
            "mae_ridge": mae(test["target"], pred)}


def evaluate(daily, holdout_days=HOLDOUT_DAYS):
    per_city = {c: r for c in daily.columns if (r := evaluate_city(daily[c].dropna(), holdout_days))}
    if not per_city:
        raise pipeline.PipelineError("not enough daily history to evaluate a forecast")
    n = sum(r["n_test"] for r in per_city.values())
    overall = {k: sum(r[k] * r["n_test"] for r in per_city.values()) / n
               for k in ("mae_persistence", "mae_mean7", "mae_ridge")}   # pooled over all test predictions
    return {"holdout_days": holdout_days, "n_test_predictions": n,
            "data_start": daily.index.min().strftime("%Y-%m-%d"), "data_end": daily.index.max().strftime("%Y-%m-%d"),
            "test_start": min(r["test_start"] for r in per_city.values()),
            "test_end": max(r["test_end"] for r in per_city.values()),
            "overall": overall, "per_city": per_city}


def format_report(res):
    o = res["overall"]
    lines = [f"Next-day PM2.5 forecast | data {res['data_start']} to {res['data_end']} | "
             f"holdout {res['test_start']} to {res['test_end']} ({res['n_test_predictions']} predictions)",
             f"{'city':<10}{'train':>6}{'persist':>9}{'mean7':>8}{'ridge':>8}"]
    for c, r in sorted(res["per_city"].items()):
        lines.append(f"{c:<10}{r['n_train']:>6}{r['mae_persistence']:>9.2f}{r['mae_mean7']:>8.2f}{r['mae_ridge']:>8.2f}")
    lines.append(f"{'ALL':<10}{'':>6}{o['mae_persistence']:>9.2f}{o['mae_mean7']:>8.2f}{o['mae_ridge']:>8.2f}  (MAE, ug/m3)")
    gain = 100 * (1 - o["mae_ridge"] / o["mae_persistence"])
    lines.append(f"Ridge vs persistence: {gain:+.1f}% MAE")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    args = ap.parse_args()
    with closing(sqlite3.connect(bootstrap.db_path())) as con:
        res = evaluate(load_daily(con), args.holdout_days)
    res["computed_at_ist"] = pipeline.now_ist().isoformat(timespec="seconds")
    pipeline.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (pipeline.REPORT_DIR / "forecast_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(format_report(res))


if __name__ == "__main__":
    main()
