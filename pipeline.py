"""
Air Quality Data Lifecycle Pipeline
Stages: 1 Collect -> 2 Store -> 3 Clean -> 4 Transform -> 5 Analyze -> 6 Archive

First run : python pipeline.py --backfill   (pulls last 92 days)
Later runs: python pipeline.py              (pulls last 2 days, incremental)
"""
import argparse, json, logging, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- config ----------------
BASE = Path(__file__).parent
RAW_DIR, REPORT_DIR, DB = BASE / "data" / "raw", BASE / "reports", BASE / "data" / "air_quality.db"
API = "https://air-quality-api.open-meteo.com/v1/air-quality"
VARS = ["pm2_5", "pm10", "nitrogen_dioxide", "us_aqi"]
CITIES = {
    "Delhi": (28.61, 77.21), "Mumbai": (19.08, 72.88), "Bengaluru": (12.97, 77.59),
    "Kolkata": (22.57, 88.36), "Chennai": (13.08, 80.27),
}
RETENTION_DAYS = 60        # hourly rows older than this move to archive_readings
RAW_FILE_KEEP_DAYS = 30    # raw JSON snapshots older than this are deleted
NAAQS_PM25 = 60            # India 24-h PM2.5 standard, µg/m³

BACKFILL_DAYS = 92         # Open-Meteo's maximum past_days
MAX_RETRIES = 4            # attempts per API call (waits 2s, 4s, 8s between them)
BACKOFF_SECONDS = 2
RETRY_STATUS = {429, 500, 502, 503, 504}   # transient HTTP errors worth retrying; other 4xx are our fault
MAX_FILL_HOURS = 3         # gaps up to this many hours are interpolated; longer gaps are dropped
IST = timezone(timedelta(hours=5, minutes=30))   # API timestamps are Asia/Kolkata (no DST)
log = logging.getLogger("aq")


class PipelineError(Exception):
    """Expected, explainable pipeline failure (as opposed to a bug)."""


class FetchError(PipelineError):
    """The Open-Meteo API could not give us usable data."""


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(BASE / "pipeline.log"), logging.StreamHandler()])


def now_ist():
    """Current wall-clock time in India as a naive datetime, matching the API's timestamps.
    Using the machine's own clock would be wrong on UTC servers (CI, Streamlit Cloud)."""
    return datetime.now(IST).replace(tzinfo=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_readings (
  city TEXT, time TEXT, pm2_5 REAL, pm10 REAL, nitrogen_dioxide REAL, us_aqi REAL,
  ingested_at TEXT, PRIMARY KEY (city, time));
CREATE TABLE IF NOT EXISTS archive_readings (
  city TEXT, time TEXT, pm2_5 REAL, pm10 REAL, nitrogen_dioxide REAL, us_aqi REAL,
  ingested_at TEXT, PRIMARY KEY (city, time));
CREATE TABLE IF NOT EXISTS clean_readings (
  city TEXT, time TEXT, pm2_5 REAL, pm10 REAL, nitrogen_dioxide REAL, us_aqi REAL,
  is_outlier INTEGER, was_imputed INTEGER, PRIMARY KEY (city, time));
CREATE TABLE IF NOT EXISTS daily_summary (
  city TEXT, date TEXT, avg_pm2_5 REAL, max_pm2_5 REAL, avg_pm10 REAL, avg_no2 REAL,
  avg_aqi REAL, aqi_category TEXT, n_hours INTEGER, PRIMARY KEY (city, date));
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  rows_collected INTEGER, rows_cleaned INTEGER, rows_archived INTEGER, status TEXT, message TEXT);
"""


def aqi_category(aqi):
    if pd.isna(aqi): return "Unknown"
    for limit, name in [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for Sensitive"),
                        (200, "Unhealthy"), (300, "Very Unhealthy")]:
        if aqi <= limit: return name
    return "Hazardous"


# ---------------- stage 1 + 2: collect & store ----------------
def _validate(city, payload):
    h = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(h, dict) or "time" not in h or any(v not in h for v in VARS):
        raise FetchError(f"{city}: response has no usable 'hourly' block (keys: {list(payload)[:5] if isinstance(payload, dict) else type(payload).__name__})")
    if any(len(h[v]) != len(h["time"]) for v in VARS):
        raise FetchError(f"{city}: hourly arrays have different lengths")


def fetch_city(city, lat, lon, past_days, retries=MAX_RETRIES, backoff=BACKOFF_SECONDS, sleep=None):
    """GET one city from Open-Meteo, retrying transient failures with exponential backoff."""
    params = {"latitude": lat, "longitude": lon, "hourly": ",".join(VARS),
              "past_days": past_days, "forecast_days": 1, "timezone": "Asia/Kolkata"}
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(API, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json()
            _validate(city, payload)          # a schema problem is not transient: raises FetchError, no retry
            return payload
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code not in RETRY_STATUS:
                raise FetchError(f"{city}: HTTP {code}, not retryable") from e
            last = e
        except ValueError as e:               # body was not valid JSON (e.g. a proxy error page)
            last = e
        if attempt < retries:
            wait = backoff * 2 ** (attempt - 1)
            log.warning("%s: attempt %d/%d failed (%s: %s); retrying in %ss",
                        city, attempt, retries, type(last).__name__, last, wait)
            (sleep or time.sleep)(wait)
    raise FetchError(f"{city}: gave up after {retries} attempts ({type(last).__name__}: {last})") from last


def collect(con, past_days, raw_dir=RAW_DIR):
    """Fetch every city. Returns (rows_stored, {city: error}). One failing city does not stop the others;
    FetchError is raised only if every city failed."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    total, failed, now = 0, {}, datetime.now(timezone.utc).isoformat(timespec="seconds")
    for city, (lat, lon) in CITIES.items():
        try:
            payload = fetch_city(city, lat, lon, past_days)
        except FetchError as e:
            log.error("collect failed: %s", e)
            failed[city] = str(e)
            continue
        # keep an immutable raw snapshot (data lake style)
        (raw_dir / f"{city}_{now[:19].replace(':', '-')}.json").write_text(json.dumps(payload))
        h = payload["hourly"]
        rows = [(city, t, *(h[v][i] for v in VARS), now) for i, t in enumerate(h["time"])]
        con.executemany("INSERT OR REPLACE INTO raw_readings VALUES (?,?,?,?,?,?,?)", rows)
        total += len(rows)
        log.info("collected %d rows for %s", len(rows), city)
    con.commit()
    if len(failed) == len(CITIES):
        raise FetchError("every city failed: " + "; ".join(failed.values()))
    return total, failed


# ---------------- stage 3: clean ----------------
def fill_short_gaps(s, limit=MAX_FILL_HOURS):
    """Linearly interpolate runs of missing values that are at most `limit` long.
    Longer runs are left entirely missing (pandas' own `limit=` would fill their first `limit` hours)."""
    isna = s.isna()
    run_len = isna.groupby((~isna).cumsum()).transform("sum")   # length of the NaN run each NaN sits in
    return s.where(~(isna & (run_len <= limit)), s.interpolate(limit_area="inside"))


def clean(con, now=None):
    now = pd.Timestamp(now if now is not None else now_ist())
    df = pd.read_sql("SELECT city,time,pm2_5,pm10,nitrogen_dioxide,us_aqi FROM raw_readings",
                     con, parse_dates=["time"])
    before = len(df)
    df = df[df["time"] <= now]                       # drop forecast hours
    df = df.drop_duplicates(["city", "time"]).sort_values(["city", "time"]).reset_index(drop=True)
    df[VARS] = df[VARS].mask(df[VARS] < 0)                          # negatives are sensor errors
    df["was_imputed"] = df[VARS].isna().any(axis=1).astype(int)
    df[VARS] = df.groupby("city")[VARS].transform(fill_short_gaps)  # fill gaps <= 3h
    df = df.dropna(subset=VARS)                                     # longer gaps stay dropped

    def zflag(s):  # > 3 sigma from a centred 7-day rolling mean
        roll = s.rolling(168, min_periods=24, center=True)
        return ((s - roll.mean()) / roll.std()).abs() > 3
    df["is_outlier"] = df.groupby("city")["pm2_5"].transform(zflag).fillna(False).astype(int)

    df["time"] = df["time"].dt.strftime("%Y-%m-%dT%H:%M")
    con.execute("DELETE FROM clean_readings")
    df.to_sql("clean_readings", con, if_exists="append", index=False)
    con.commit()
    log.info("cleaned: %d raw -> %d clean rows, %d imputed, %d outliers",
             before, len(df), df.was_imputed.sum(), df.is_outlier.sum())
    return len(df)


# ---------------- stage 4: transform ----------------
def transform(con):
    df = pd.read_sql("SELECT * FROM clean_readings WHERE is_outlier=0", con, parse_dates=["time"])
    if df.empty: return
    df["date"] = df["time"].dt.strftime("%Y-%m-%d")
    daily = (df.groupby(["city", "date"])
               .agg(avg_pm2_5=("pm2_5", "mean"), max_pm2_5=("pm2_5", "max"),
                    avg_pm10=("pm10", "mean"), avg_no2=("nitrogen_dioxide", "mean"),
                    avg_aqi=("us_aqi", "mean"), n_hours=("pm2_5", "size"))
               .reset_index().round(1))
    daily["aqi_category"] = daily["avg_aqi"].apply(aqi_category)
    daily.to_sql("_daily_tmp", con, if_exists="replace", index=False)
    con.execute("""INSERT OR REPLACE INTO daily_summary
                   SELECT city,date,avg_pm2_5,max_pm2_5,avg_pm10,avg_no2,avg_aqi,aqi_category,n_hours
                   FROM _daily_tmp""")
    con.execute("DROP TABLE _daily_tmp")
    con.commit()
    log.info("daily_summary upserted %d rows", len(daily))


# ---------------- stage 5: analyze ----------------
def analyze(con, report_dir=REPORT_DIR):
    report_dir.mkdir(parents=True, exist_ok=True)
    daily = pd.read_sql("SELECT * FROM daily_summary", con, parse_dates=["date"])
    hourly = pd.read_sql("SELECT city,time,pm2_5 FROM clean_readings WHERE is_outlier=0",
                         con, parse_dates=["time"])
    if daily.empty: return
    L = [f"# Air Quality Report\nGenerated {now_ist():%Y-%m-%d %H:%M} IST\n"]

    rank = daily.groupby("city")["avg_pm2_5"].mean().sort_values(ascending=False).round(1)
    L += ["## Average PM2.5 by city (µg/m³)", rank.to_string(), ""]

    worst = daily.nlargest(5, "avg_pm2_5")[["city", "date", "avg_pm2_5", "aqi_category"]]
    L += ["## 5 worst days", worst.to_string(index=False), ""]

    exceed = (daily.assign(bad=daily.avg_pm2_5 > NAAQS_PM25).groupby("city")["bad"].mean() * 100).round(0)
    L += [f"## % of days above India NAAQS limit ({NAAQS_PM25} µg/m³)", exceed.to_string(), ""]

    hourly["weekend"] = hourly.time.dt.dayofweek >= 5
    wk = hourly.groupby(["city", "weekend"])["pm2_5"].mean().unstack().reindex(columns=[False, True]).round(1)
    wk.columns = ["weekday", "weekend"]       # reindex keeps both columns even if data covers only one kind of day
    L += ["## Weekday vs weekend PM2.5", wk.to_string(), ""]

    hourly["hour"] = hourly.time.dt.hour
    prof = hourly.groupby(["hour", "city"])["pm2_5"].mean().unstack()
    L += ["## Peak pollution hour per city", prof.idxmax().to_string(), ""]
    (report_dir / "summary.md").write_text("\n".join(L), encoding="utf-8")

    fig, ax = plt.subplots(figsize=(11, 5))
    for city, g in daily.groupby("city"): ax.plot(g["date"], g["avg_pm2_5"], label=city)
    ax.axhline(NAAQS_PM25, ls="--", c="grey", lw=1, label="NAAQS limit")
    ax.set(title="Daily average PM2.5", ylabel="µg/m³"); ax.legend(); fig.autofmt_xdate()
    fig.tight_layout(); fig.savefig(report_dir / "daily_pm25.png", dpi=120); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5))
    prof.plot(ax=ax); ax.set(title="Average PM2.5 by hour of day", xlabel="hour", ylabel="µg/m³")
    fig.tight_layout(); fig.savefig(report_dir / "hourly_profile.png", dpi=120); plt.close(fig)
    log.info("report written to %s", report_dir)


# ---------------- stage 6: archive / retention ----------------
def archive(con, now=None, raw_dir=RAW_DIR):
    now = now if now is not None else now_ist()
    cutoff = (now - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M")
    n = con.execute("SELECT COUNT(*) FROM raw_readings WHERE time < ?", (cutoff,)).fetchone()[0]
    con.execute("INSERT OR REPLACE INTO archive_readings SELECT * FROM raw_readings WHERE time < ?", (cutoff,))
    con.execute("DELETE FROM raw_readings WHERE time < ?", (cutoff,))
    con.commit()
    deadline = now - timedelta(days=RAW_FILE_KEEP_DAYS)
    deleted = 0
    for f in raw_dir.glob("*.json"):
        if datetime.fromtimestamp(f.stat().st_mtime) < deadline:
            f.unlink(); deleted += 1
    con.execute("VACUUM")
    log.info("archived %d rows, deleted %d old raw files", n, deleted)
    return n


# ---------------- orchestration ----------------
def record_run(con, started, stats, status, msg):
    con.rollback()                       # never leave a half-finished transaction behind a failure
    con.execute("INSERT INTO pipeline_runs (started_at,finished_at,rows_collected,rows_cleaned,"
                "rows_archived,status,message) VALUES (?,?,?,?,?,?,?)",
                (started, now_ist().isoformat(timespec="seconds"), stats.get("collected", 0),
                 stats.get("cleaned", 0), stats.get("archived", 0), status, msg))
    con.commit()


def run(con, past_days=2, raw_dir=RAW_DIR, report_dir=REPORT_DIR, now=None):
    """Run all stages once and audit the outcome in pipeline_runs.
    Returns "success", "partial" (some cities failed) or "failed"."""
    con.executescript(SCHEMA)
    started, stats, status, msg, stage = now_ist().isoformat(timespec="seconds"), {}, "success", "", "collect"
    try:
        stats["collected"], failed = collect(con, past_days, raw_dir)
        if failed:
            status, msg = "partial", "cities failed: " + "; ".join(failed.values())
            log.warning(msg)
        stage = "clean"; stats["cleaned"] = clean(con, now)
        stage = "transform"; transform(con)
        stage = "analyze"; analyze(con, report_dir)
        stage = "archive"; stats["archived"] = archive(con, now, raw_dir)
    except (PipelineError, sqlite3.Error, OSError) as e:   # failures we understand: audit and report
        status, msg = "failed", f"{stage}: {type(e).__name__}: {e}"
        log.error("pipeline failed at %s: %s", stage, e)
    except Exception as e:                                  # a bug: still audit it, then let it crash loudly
        record_run(con, started, stats, "failed", f"{stage}: {type(e).__name__}: {e}")
        log.exception("unexpected error at %s", stage)
        raise
    record_run(con, started, stats, status, msg)
    log.info("run finished: %s", status)
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help=f"pull last {BACKFILL_DAYS} days instead of 2")
    args = ap.parse_args()
    setup_logging()
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    try:
        status = run(con, BACKFILL_DAYS if args.backfill else 2)
    finally:
        con.close()
    sys.exit(1 if status == "failed" else 0)    # non-zero exit lets cron / CI notice a failed run


if __name__ == "__main__":
    main()
