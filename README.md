# Air Quality Data Lifecycle Pipeline

## Run
pip install -r requirements.txt
python pipeline.py --backfill        # first time: 92 days of history
python pipeline.py                   # every later run: incremental
streamlit run dashboard.py           # opens http://localhost:8501

## Schedule daily
Linux/Mac (cron):   0 7 * * * cd /path/to/air_quality_pipeline && python pipeline.py
Windows: Task Scheduler -> Create Basic Task -> Daily -> Action: python pipeline.py (Start in: project folder)

## Lifecycle mapping
| Stage      | What the code does                                         | Where                        |
|------------|------------------------------------------------------------|------------------------------|
| Collect    | Hourly PM2.5/PM10/NO2/AQI for 5 cities from Open-Meteo API | collect()                    |
| Store      | Raw JSON snapshots + SQLite raw_readings (upsert)          | data/raw/, raw_readings      |
| Clean      | Drop forecast rows, negatives→NaN, interpolate ≤3h gaps, 3σ outlier flag | clean_readings |
| Transform  | Daily aggregates + AQI category                            | daily_summary                |
| Analyze    | Rankings, NAAQS exceedance, weekday/weekend, peak hour, charts | reports/                 |
| Share      | Streamlit dashboard                                        | dashboard.py                 |
| Archive    | Rows >60 days → archive_readings, delete JSON >30 days, VACUUM | archive()                |
| Governance | Every run logged to pipeline_runs + pipeline.log           | pipeline_runs                |