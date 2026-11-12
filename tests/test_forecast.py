import numpy as np
import pandas as pd
import pytest

import forecast
import pipeline


def daily_series(values, start="2026-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"), dtype=float)


def test_features_use_only_the_past_and_target_is_the_next_day():
    s = daily_series(range(1, 21))                       # day k has value k, so every feature is checkable by hand
    X = forecast.build_features(s)
    row = X.loc["2026-01-12"]                            # predicting day 12 (value 12)
    assert row.target == 12
    assert row.today == 11 and row.lag1 == 10 and row.lag2 == 9 and row.lag6 == 5
    assert row.mean7 == np.mean(range(5, 12))            # days 5..11: strictly before the target day
    assert X.index.min() == pd.Timestamp("2026-01-08")   # needs 7 days of history before the first target


def test_missing_day_is_not_silently_skipped():
    s = daily_series(range(1, 31)).drop(pd.Timestamp("2026-01-15"))
    X = forecast.build_features(s)
    # every target day whose target, "today", lag1, lag2, lag6 or 7-day mean touches Jan 15 (i.e. Jan 15-22) is dropped,
    # rather than being computed from a series with the gap closed up
    gap_affected = pd.date_range("2026-01-15", "2026-01-22")
    assert not X.index.isin(gap_affected).any()
    assert pd.Timestamp("2026-01-23") in X.index and pd.Timestamp("2026-01-14") in X.index
    assert not X.isna().any().any()


def test_time_split_never_puts_the_future_in_training():
    X = forecast.build_features(daily_series(range(1, 101)))
    train, test = forecast.split_by_time(X, holdout_days=14)
    assert len(test) == 14
    assert train.index.max() < test.index.min()
    assert len(train) + len(test) == len(X)


def test_persistence_mae_matches_hand_calculation():
    # steadily rising by 1/day: "tomorrow = today" is always wrong by exactly 1
    res = forecast.evaluate_city(daily_series(np.arange(100.0, 220.0)), holdout_days=14)
    assert res["mae_persistence"] == pytest.approx(1.0)


def test_ridge_learns_a_simple_trend_and_beats_persistence():
    res = forecast.evaluate_city(daily_series(np.arange(100.0, 220.0)), holdout_days=14)
    assert res["mae_ridge"] < res["mae_persistence"]


def test_ridge_is_fitted_on_training_rows_only():
    # flat for 90 days then a jump in the holdout: a model that saw the jump would not predict ~flat
    s = daily_series([50.0] * 90 + [150.0] * 14)
    X = forecast.build_features(s)
    train, test = forecast.split_by_time(X, 14)
    assert train.target.max() <= 150 and (train.index < test.index.min()).all()
    res = forecast.evaluate_city(s, 14)
    assert res["mae_ridge"] > 50                           # it cannot have known about the jump


def test_short_history_is_skipped_and_nothing_to_evaluate_raises():
    assert forecast.evaluate_city(daily_series(range(20)), 14) is None
    with pytest.raises(pipeline.PipelineError):
        forecast.evaluate(pd.DataFrame({"Delhi": daily_series(range(20))}), 14)


def test_load_daily_drops_incomplete_days_and_today(con):
    con.executemany("INSERT INTO daily_summary VALUES (?,?,?,?,?,?,?,?,?)", [
        ("Delhi", "2026-01-08", 50, 60, 1, 1, 1, "Good", 24),
        ("Delhi", "2026-01-09", 70, 80, 1, 1, 1, "Good", 12),    # too few valid hours
        ("Delhi", "2026-01-10", 90, 99, 1, 1, 1, "Good", 22),    # "today": still in progress
        ("Mumbai", "2026-01-08", 30, 40, 1, 1, 1, "Good", 20),
    ])
    wide = forecast.load_daily(con, today="2026-01-10")
    assert list(wide.index) == [pd.Timestamp("2026-01-08")]
    assert wide.loc["2026-01-08", "Delhi"] == 50 and wide.loc["2026-01-08", "Mumbai"] == 30


def test_overall_mae_is_pooled_over_all_test_predictions():
    daily = pd.DataFrame({"A": daily_series(np.arange(100.0, 220.0)), "B": daily_series(np.arange(300.0, 420.0))})
    res = forecast.evaluate(daily, 14)
    assert res["n_test_predictions"] == 28
    assert res["overall"]["mae_persistence"] == pytest.approx(1.0)
    assert "ALL" in forecast.format_report(res)
