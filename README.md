# 🌫️ AirLens — air quality data lifecycle pipeline

An end‑to‑end data pipeline that collects hourly air‑quality data for five Indian metros, cleans and models it in SQLite, analyses it against India's national standard, archives old data, and serves everything in a Streamlit dashboard.

Every stage of the **data lifecycle** — collect, store, clean, transform, analyse, share, archive, govern — is implemented and traceable in code.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-storage-003B57?logo=sqlite&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-transform-150458?logo=pandas&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-FF4B4B?logo=streamlit&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-charts-3F4F75?logo=plotly&logoColor=white)

## Findings (92 days, Aug – Oct 2026)

| City | Avg PM2.5 (µg/m³) | Days above NAAQS limit (60) | Peak hour |
|------|------------------:|----------------------------:|----------:|
| Delhi | **63.9** | **55%** | 23:00 |
| Kolkata | 38.2 | 27% | 22:00 |
| Mumbai | 30.7 | 14% | 14:00 |
| Chennai | 18.6 | 0% | 19:00 |
| Bengaluru | 13.3 | 0% | 20:00 |

- Delhi breached the national 24‑hour PM2.5 limit on more than half of all days.
- Pollution peaks late at night in most cities — consistent with a shallow night‑time boundary layer trapping emissions — while Mumbai peaks mid‑afternoon.

![Daily PM2.5](reports/daily_pm25.png)
![Hourly profile](reports/hourly_profile.png)

## Lifecycle mapping

| Stage | What the code does | Where |
|-------|--------------------|-------|
| Collect | Hourly PM2.5 / PM10 / NO₂ / AQI for 5 cities from the Open‑Meteo API | `collect()` |
| Store | Raw JSON snapshots + SQLite `raw_readings` (idempotent upsert) | `data/raw/`, `raw_readings` |
| Clean | Drop forecast rows, negatives → NaN, interpolate gaps ≤ 3 h, flag 3σ outliers against a 7‑day rolling mean | `clean()` |
| Transform | Daily aggregates + AQI category | `transform()` → `daily_summary` |
| Analyse | City ranking, NAAQS exceedance, weekday vs weekend, peak hour, charts | `analyze()` → `reports/` |
| Share | Interactive Streamlit + Plotly dashboard | `dashboard.py` |
| Archive | Rows > 60 days → `archive_readings`, raw JSON > 30 days deleted, `VACUUM` | `archive()` |
| Govern | Every run logged to `pipeline_runs` and `pipeline.log` | `main()` |

## Run it

```bash
git clone https://github.com/Hit3nDev/air_quality_pipeline.git
cd air_quality_pipeline
pip install -r requirements.txt

python pipeline.py --backfill    # first run: pulls the last 92 days
python pipeline.py               # later runs: incremental (last 2 days)
streamlit run dashboard.py       # http://localhost:8501
```

### Schedule it daily

- **Linux / macOS (cron):** `0 7 * * * cd /path/to/air_quality_pipeline && python pipeline.py`
- **Windows:** Task Scheduler → Create Basic Task → Daily → `python pipeline.py` (start in the project folder)

## Structure

```
pipeline.py      collect → store → clean → transform → analyse → archive
dashboard.py     Streamlit dashboard
report.py        generates the Word project report
reports/         charts + summary.md produced by the pipeline
data/            SQLite database + raw JSON snapshots (sample run included)
```

Data source: [Open‑Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) (free, no key needed).

---

Built by [Hiten Mandhyan](https://github.com/Hit3nDev)
