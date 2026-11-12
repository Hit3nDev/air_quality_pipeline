import os
from datetime import timedelta

import pandas as pd

import pipeline
from conftest import insert_raw


def insert_clean(con, rows):
    con.executemany("INSERT INTO clean_readings VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()


def daily(con):
    return pd.read_sql("SELECT * FROM daily_summary ORDER BY city, date", con)


# ---------- transform ----------
def test_daily_summary_aggregates_and_excludes_outliers(con):
    insert_clean(con, [
        ("Delhi", "2026-01-10T00:00", 10.0, 20.0, 5.0, 40.0, 0, 0),
        ("Delhi", "2026-01-10T01:00", 20.0, 30.0, 7.0, 60.0, 0, 0),
        ("Delhi", "2026-01-10T02:00", 30.0, 40.0, 9.0, 80.0, 0, 0),
        ("Delhi", "2026-01-10T03:00", 500.0, 600.0, 90.0, 400.0, 1, 0),   # outlier: must be ignored
    ])
    pipeline.transform(con)
    row = daily(con).iloc[0]
    assert (row.avg_pm2_5, row.max_pm2_5, row.n_hours) == (20.0, 30.0, 3)
    assert row.avg_aqi == 60.0 and row.aqi_category == "Moderate"


def test_transform_is_an_idempotent_upsert(con):
    insert_clean(con, [("Delhi", "2026-01-10T00:00", 10.0, 20.0, 5.0, 40.0, 0, 0)])
    pipeline.transform(con)
    pipeline.transform(con)
    assert len(daily(con)) == 1
    insert_clean(con, [("Delhi", "2026-01-10T01:00", 30.0, 20.0, 5.0, 40.0, 0, 0)])
    pipeline.transform(con)                                  # a later run updates the same city-day
    out = daily(con)
    assert len(out) == 1 and out.iloc[0].avg_pm2_5 == 20.0 and out.iloc[0].n_hours == 2


# ---------- archive / retention ----------
NOW = pd.Timestamp("2026-03-01T12:00").to_pydatetime()      # cutoff = NOW - 60 days = 2025-12-31 12:00


def counts(con):
    q = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return q("raw_readings"), q("archive_readings")


def test_old_rows_move_to_archive_and_recent_rows_stay(con, tmp_path):
    insert_raw(con, "Delhi", "2025-12-01", [10.0] * 10)      # ~90 days old
    insert_raw(con, "Delhi", "2026-02-20", [20.0] * 10)      # ~9 days old
    moved = pipeline.archive(con, now=NOW, raw_dir=tmp_path)
    assert moved == 10 and counts(con) == (10, 10)
    assert con.execute("SELECT MAX(time) FROM archive_readings").fetchone()[0] < "2025-12-31T12:00"
    assert con.execute("SELECT MIN(time) FROM raw_readings").fetchone()[0] >= "2025-12-31T12:00"


def test_archive_twice_moves_nothing_more_and_loses_nothing(con, tmp_path):
    insert_raw(con, "Delhi", "2025-12-01", [10.0] * 10)
    insert_raw(con, "Delhi", "2026-02-20", [20.0] * 10)
    pipeline.archive(con, now=NOW, raw_dir=tmp_path)
    assert pipeline.archive(con, now=NOW, raw_dir=tmp_path) == 0
    assert counts(con) == (10, 10) and sum(counts(con)) == 20


def test_raw_json_older_than_30_days_is_deleted(con, tmp_path):
    old, new = tmp_path / "Delhi_old.json", tmp_path / "Delhi_new.json"
    for f, age in ((old, 45), (new, 5)):
        f.write_text("{}")
        ts = (NOW - timedelta(days=age)).timestamp()
        os.utime(f, (ts, ts))
    pipeline.archive(con, now=NOW, raw_dir=tmp_path)
    assert not old.exists() and new.exists()


def test_archive_never_touches_daily_summary(con, tmp_path):
    insert_raw(con, "Delhi", "2025-12-01", [10.0] * 10)
    con.execute("INSERT INTO daily_summary VALUES ('Delhi','2025-12-01',10,10,20,10,40,'Good',10)")
    con.commit()
    pipeline.archive(con, now=NOW, raw_dir=tmp_path)
    assert con.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0] == 1
