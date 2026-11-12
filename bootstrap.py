"""Makes sure the SQLite database exists and is fresh. Used by the dashboard because hosts such as
Streamlit Community Cloud have no persistent disk: the database is rebuilt from Open-Meteo when missing/stale.

Kept free of Streamlit imports so it can be unit-tested.
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pipeline

BOOTSTRAP_DAYS = 90     # history fetched when building from scratch (Open-Meteo allows up to 92)
MAX_AGE_HOURS = 24      # a database whose last successful run is older than this is rebuilt


def db_path():
    """Database location; override with the AIRLENS_DB environment variable."""
    return Path(os.environ.get("AIRLENS_DB", pipeline.DB))


def last_success(db):
    """Finish time (IST) of the newest fully successful pipeline run, or None."""
    if not Path(db).exists():
        return None
    try:
        with closing(sqlite3.connect(db)) as con:
            row = con.execute("SELECT finished_at FROM pipeline_runs WHERE status='success' "
                              "ORDER BY run_id DESC LIMIT 1").fetchone()
    except sqlite3.Error:            # empty file, wrong schema, corrupt database: treat as unusable
        return None
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def has_data(db):
    """True if the database already holds at least one daily summary row (even if it is out of date)."""
    if not Path(db).exists():
        return False
    try:
        with closing(sqlite3.connect(db)) as con:
            return con.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def is_stale(db, max_age_hours=MAX_AGE_HOURS, now=None):
    t = last_success(db)
    return t is None or (now or pipeline.now_ist()) - t > timedelta(hours=max_age_hours)


def build_database(db, days=BOOTSTRAP_DAYS):
    """Run the whole pipeline against `db`. Raises PipelineError if it failed outright."""
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as con:
        status = pipeline.run(con, past_days=min(days, pipeline.BACKFILL_DAYS))
        message = con.execute("SELECT message FROM pipeline_runs ORDER BY run_id DESC LIMIT 1").fetchone()[0]
    if status == "failed":
        raise pipeline.PipelineError(message)
    return status


def ensure_database(db, max_age_hours=MAX_AGE_HOURS, days=BOOTSTRAP_DAYS, now=None):
    """Returns "fresh" if the database was recent enough, otherwise "built" after (re)fetching `days` of data."""
    if not is_stale(db, max_age_hours, now):
        return "fresh"
    build_database(db, days)
    return "built"
