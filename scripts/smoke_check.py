"""Exit non-zero unless the most recent pipeline run succeeded and produced sane data.
Used by the daily GitHub Actions smoke test:  python pipeline.py --backfill && python scripts/smoke_check.py
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "air_quality.db"
MIN_DAILY_ROWS = 5 * 30      # 5 cities x at least 30 days after a backfill


def main():
    if not DB.exists():
        sys.exit(f"FAIL: {DB} was not created")
    con = sqlite3.connect(DB)
    run = con.execute("SELECT status, message, rows_collected FROM pipeline_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    daily = con.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
    cities = con.execute("SELECT COUNT(DISTINCT city) FROM daily_summary").fetchone()[0]
    print(f"last run: {run} | daily rows: {daily} | cities: {cities}")
    if run is None or run[0] != "success":
        sys.exit(f"FAIL: last run was {run}")
    if cities != 5 or daily < MIN_DAILY_ROWS:
        sys.exit(f"FAIL: expected 5 cities and >= {MIN_DAILY_ROWS} daily rows")
    print("OK")


if __name__ == "__main__":
    main()
