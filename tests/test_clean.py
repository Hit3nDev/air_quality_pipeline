import numpy as np
import pandas as pd

import pipeline
from conftest import NOW, hourly_times, insert_raw


def clean_df(con):
    return pd.read_sql("SELECT * FROM clean_readings ORDER BY city, time", con)


def ramp(n=48):
    return [10.0 + h for h in range(n)]          # 10, 11, 12 ... so interpolation is exactly checkable


def test_gap_of_three_hours_is_interpolated_and_flagged(con):
    vals = ramp()
    for h in (10, 11, 12):
        vals[h] = None
    insert_raw(con, "Delhi", "2026-01-10", vals)
    pipeline.clean(con, now=NOW)
    df = clean_df(con).set_index("time")
    assert len(df) == 48
    filled = df.loc[hourly_times("2026-01-10", 48)[10:13]]
    assert filled.pm2_5.tolist() == [20.0, 21.0, 22.0]      # exact linear fill
    assert filled.was_imputed.tolist() == [1, 1, 1]
    assert df.was_imputed.sum() == 3                         # nothing else flagged


def test_gap_longer_than_three_hours_is_dropped_entirely(con):
    vals = ramp()
    for h in (10, 11, 12, 13):                               # 4-hour gap
        vals[h] = None
    insert_raw(con, "Delhi", "2026-01-10", vals)
    pipeline.clean(con, now=NOW)
    times = set(clean_df(con).time)
    assert len(times) == 44
    # regression: pandas interpolate(limit=3) used to fill the first 3 hours of such a gap
    assert not times & set(hourly_times("2026-01-10", 48)[10:14])


def test_negative_values_are_nulled_then_filled_from_neighbours(con):
    vals = ramp()
    vals[5] = -8.0
    insert_raw(con, "Delhi", "2026-01-10", vals)
    pipeline.clean(con, now=NOW)
    df = clean_df(con)
    assert (df.pm2_5 >= 0).all()
    row = df[df.time == "2026-01-10T05:00"].iloc[0]
    assert row.pm2_5 == 15.0 and row.was_imputed == 1


def test_trailing_negative_has_no_neighbour_and_is_dropped(con):
    vals = ramp()
    vals[-1] = -1.0
    insert_raw(con, "Delhi", "2026-01-10", vals)
    pipeline.clean(con, now=NOW)
    assert len(clean_df(con)) == 47


def test_forecast_hours_after_now_are_dropped(con):
    insert_raw(con, "Delhi", "2026-01-19", ramp(48))         # 2026-01-19 00:00 .. 2026-01-20 23:00
    pipeline.clean(con, now=NOW)                             # NOW = 2026-01-20 00:00
    df = clean_df(con)
    assert df.time.max() == "2026-01-20T00:00"
    assert len(df) == 25


def test_three_sigma_outlier_is_flagged_and_normal_data_is_not(con):
    hours = 240
    vals = [30 + 5 * np.sin(2 * np.pi * h / 24) for h in range(hours)]
    vals[120] = 400.0                                        # one absurd spike
    insert_raw(con, "Delhi", "2026-01-10", vals)
    pipeline.clean(con, now=NOW)
    df = clean_df(con)
    assert df.is_outlier.sum() == 1
    assert df[df.is_outlier == 1].time.iloc[0] == hourly_times("2026-01-10", hours)[120]


def test_cities_are_cleaned_independently(con):
    insert_raw(con, "Delhi", "2026-01-10", ramp(24))
    insert_raw(con, "Mumbai", "2026-01-10", [None, None] + ramp(22))   # leading gap: nothing to interpolate from
    pipeline.clean(con, now=NOW)
    df = clean_df(con)
    assert (df.city == "Delhi").sum() == 24                  # Delhi's last value is not leaked into Mumbai
    assert (df.city == "Mumbai").sum() == 22


def test_clean_is_repeatable(con):
    insert_raw(con, "Delhi", "2026-01-10", ramp(48))
    pipeline.clean(con, now=NOW)
    first = clean_df(con)
    pipeline.clean(con, now=NOW)
    pd.testing.assert_frame_equal(first, clean_df(con))
