import sqlite3, subprocess, sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).parent
DB, RAW_DIR, REPORT_DIR = BASE / "data" / "air_quality.db", BASE / "data" / "raw", BASE / "reports"
LIMIT = 60                                    # India NAAQS 24-hour PM2.5 standard, µg/m³
EVENTS = {"Diwali": "2026-11-08"}             # edit or add dates to mark on the trend chart

INK, SMOKE, PAPER, RULE, LIMIT_RED = "#2B3138", "#6B7480", "#EEF1F3", "#C9CFD4", "#7A1F2B"
# µg/m³ -> colour. Blue is clear air, amber is the legal limit, brown and soot beyond it.
STOPS = [(0, (0xB7, 0xD6, 0xEC)), (30, (0xDD, 0xE4, 0xD4)), (60, (0xE2, 0xB0, 0x4E)),
         (120, (0xB7, 0x5C, 0x1F)), (200, (0x62, 0x30, 0x12)), (300, (0x20, 0x15, 0x0E))]
VMAX = 300
SCALE = [[s / VMAX, "#%02x%02x%02x" % c] for s, c in STOPS]

def haze(v):
    if pd.isna(v): return "#E4E8EB"
    v = min(max(v, 0), VMAX)
    for (a, ca), (b, cb) in zip(STOPS, STOPS[1:]):
        if v <= b:
            t = (v - a) / (b - a)
            return "#%02x%02x%02x" % tuple(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % STOPS[-1][1]

st.set_page_config(page_title="Air bulletin", layout="wide")
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Public+Sans:wght@400;500;600&display=swap');
header[data-testid="stHeader"] {{ background: transparent; }}
#MainMenu, footer, .stAppDeployButton {{ display: none; }}
.block-container {{ max-width: 1180px; padding-top: 2.5rem; }}
html, body, [class*="css"], p, li, td, th, button, input {{ font-family: 'Public Sans', sans-serif; color: {INK}; }}
div[data-testid="stMarkdown"] h2 {{ font-family: 'Newsreader', serif !important; font-weight: 400 !important;
    font-size: 1.9rem !important; letter-spacing: -0.01em; margin: 3.2rem 0 .4rem !important; padding: 0 !important; }}
.masthead {{ font-size: .95rem; color: {SMOKE}; border-bottom: 1px solid {RULE}; padding-bottom: .6rem; margin-bottom: 2.4rem; }}
div[data-testid="stMarkdown"] p.lede {{ font-family: 'Newsreader', serif !important; font-size: 3.1rem !important;
    line-height: 1.08 !important; font-weight: 400; letter-spacing: -0.01em; max-width: 18ch; margin: 0 0 .8rem !important; }}
p.lede em {{ font-style: italic; color: {LIMIT_RED}; }}
div[data-testid="stMarkdown"] p.dek, div[data-testid="stMarkdown"] p.sub {{ font-size: 1.05rem !important; color: {SMOKE} !important;
    max-width: 64ch; line-height: 1.5 !important; margin-bottom: 1.6rem !important; }}
.strip {{ display: grid; grid-template-columns: 110px 1fr 70px; align-items: center; gap: 1rem; margin-bottom: 7px; }}
.strip .city {{ font-family: 'Newsreader', serif; font-size: 1.15rem; }}
.strip .band {{ display: flex; height: 30px; }}
.strip .band i {{ flex: 1; display: block; }}
.strip .now {{ font-size: .95rem; text-align: right; font-variant-numeric: tabular-nums; }}
.ticks {{ display: grid; grid-template-columns: 110px 1fr 70px; gap: 1rem; color: {SMOKE}; font-size: .78rem; }}
.ticks .row {{ display: flex; justify-content: space-between; }}
.scalebar {{ height: 8px; margin: .9rem 0 .25rem; }}
.rail {{ position: relative; display: grid; grid-template-columns: repeat(7, 1fr); gap: 1.2rem; padding-top: 1.4rem; margin: 1rem 0 .5rem; }}
.rail:before {{ content: ""; position: absolute; left: 6px; right: 6px; top: 6px; border-top: 1px solid {INK}; }}
.stop:before {{ content: ""; display: block; width: 11px; height: 11px; border-radius: 50%; background: {INK};
               position: relative; top: -1.4rem; margin-bottom: -1.4rem; }}
.stop .name {{ font-family: 'Newsreader', serif; font-size: 1.2rem; margin-top: .9rem; }}
.stop .fig {{ font-size: 1.6rem; font-weight: 500; font-variant-numeric: tabular-nums; margin: .15rem 0; }}
.stop .note {{ font-size: .82rem; color: {SMOKE}; line-height: 1.4; }}
.ledger {{ display: grid; grid-template-columns: 150px 1fr 90px; row-gap: .55rem; column-gap: 1rem; align-items: center; margin-top: 1.2rem; }}
.ledger .lbl {{ font-family: 'Newsreader', serif; font-size: 1.05rem; }}
.ledger .lbl.loss {{ font-family: 'Public Sans', sans-serif; font-size: .85rem; color: {LIMIT_RED}; padding-left: 1rem; }}
.ledger .bar {{ height: 14px; background: {INK}; }}
.ledger .bar.loss {{ background: {LIMIT_RED}; height: 6px; }}
.ledger .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.ledger .num.loss {{ color: {LIMIT_RED}; font-size: .85rem; }}
.ledger .note {{ grid-column: 1 / -1; font-size: .82rem; color: {SMOKE}; margin: -.3rem 0 .4rem 150px; padding-left: 1rem; }}
div[data-testid="stMarkdown"] p.advice {{ border-left: 2px solid {LIMIT_RED}; padding: .3rem 0 .3rem 1rem; max-width: 62ch;
    margin: 1.6rem 0 0 !important; line-height: 1.5 !important; }}
div[data-testid="stDataFrame"] {{ border: 1px solid {RULE}; }}
.stButton button, .stDownloadButton button {{ border-radius: 2px; border: 1px solid {INK}; background: transparent; }}
.stButton button:hover, .stDownloadButton button:hover {{ background: {INK}; color: {PAPER}; }}
</style>""", unsafe_allow_html=True)

PLOT = dict(template="simple_white")
def tidy(fig, h):
    fig.update_layout(height=h, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Public Sans, Helvetica Neue, Arial, sans-serif", color=INK, size=12),
                      margin=dict(l=10, r=20, t=30, b=10), legend=dict(orientation="h", y=1.08, x=0, title=None))
    fig.update_xaxes(showgrid=False, linecolor=RULE, zeroline=False, ticks="")
    fig.update_yaxes(showgrid=True, gridcolor="#E2E6E9", linecolor="rgba(0,0,0,0)", zeroline=False, ticks="")
    return fig

# ---------- data ----------
if not DB.exists():
    st.markdown("## Nothing to show yet"); st.write("Run `python pipeline.py --backfill` once, then reload this page."); st.stop()
def q(sql, **kw):
    with sqlite3.connect(DB) as con: return pd.read_sql(sql, con, **kw)
daily = q("SELECT * FROM daily_summary", parse_dates=["date"])
clean = q("SELECT * FROM clean_readings", parse_dates=["time"])
runs  = q("SELECT * FROM pipeline_runs ORDER BY run_id DESC")
n = q("""SELECT (SELECT COUNT(*) FROM raw_readings) raw, (SELECT COUNT(*) FROM clean_readings) clean,
         (SELECT COUNT(*) FROM clean_readings WHERE is_outlier=1) outliers,
         (SELECT COUNT(*) FROM clean_readings WHERE was_imputed=1) imputed,
         (SELECT COUNT(*) FROM daily_summary) daily, (SELECT COUNT(*) FROM archive_readings) archive""").iloc[0]
if daily.empty: st.write("The database is empty. Run the pipeline first."); st.stop()
hourly = clean[clean.is_outlier == 0]
cities = sorted(daily.city.unique())

with st.sidebar:
    st.markdown("Settings")
    focus = st.selectbox("City to examine", cities)
    window = st.slider("Days shown in trends", 14, 92, 60)
    if st.button("Refresh data", use_container_width=True):
        with st.spinner("Collecting, cleaning, summarising…"):
            r = subprocess.run([sys.executable, "pipeline.py"], cwd=BASE, capture_output=True, text=True)
        if r.returncode: st.error(r.stderr[-600:])
        st.rerun()
    if not runs.empty:
        st.caption(f"Last refresh {runs.iloc[0].finished_at[:16].replace('T', ' ')}")

# ---------- masthead + lede ----------
latest = hourly.sort_values("time").groupby("city").tail(1).set_index("city")
reading_time = hourly.time.max()
worst = latest.pm2_5.idxmax(); w = latest.loc[worst]
ratio = w.pm2_5 / LIMIT
st.markdown(f'<div class="masthead">Air bulletin for five Indian cities, readings to {reading_time:%A %d %B, %H:%M}</div>',
            unsafe_allow_html=True)
if ratio >= 1:
    lede = f"{worst} is breathing <em>{ratio:.1f} times</em> the legal limit."
else:
    lede = f"Every city is within the legal limit this hour. {worst} is the haziest."
st.markdown(f'<p class="lede">{lede}</p>', unsafe_allow_html=True)
st.markdown(f'<p class="dek">PM2.5 are particles small enough to pass from the lungs into the blood. India\'s standard allows '
            f'{LIMIT} µg/m³ averaged over a day. The bands below show the last seven days, hour by hour: blue is clear air, '
            f'amber is the legal limit, and brown means the limit has been left well behind.</p>', unsafe_allow_html=True)

end = reading_time.floor("h"); start = end - pd.Timedelta(days=7) + pd.Timedelta(hours=1)
idx = pd.date_range(start, end, freq="h")
for c in cities:
    s = hourly[hourly.city == c].set_index("time").pm2_5.reindex(idx)
    band = "".join(f'<i style="background:{haze(v)}"></i>' for v in s)
    st.markdown(f'<div class="strip"><div class="city">{c}</div><div class="band">{band}</div>'
                f'<div class="now">{latest.loc[c].pm2_5:.0f} µg/m³</div></div>', unsafe_allow_html=True)
grad = ", ".join(haze(v) for v in range(0, VMAX + 1, 15))
st.markdown(f'<div class="ticks"><div></div><div><div class="row"><span>{start:%a %d %b}</span><span>{end:%a %d %b}</span></div>'
            f'<div class="scalebar" style="background:linear-gradient(90deg,{grad})"></div>'
            f'<div class="row"><span>0, clear</span><span>60, the legal limit</span><span>150</span><span>300 and above</span></div></div><div></div></div>',
            unsafe_allow_html=True)

def band_for(v):
    for lim, t in [(50, "clean"), (100, "acceptable"), (150, "unhealthy for children, the elderly and anyone with asthma"),
                   (200, "unhealthy for everyone"), (300, "a health alert")]:
        if v <= lim: return t
    return "hazardous"
advice = {"clean": "Outdoor activity is fine.",
          "acceptable": "Fine for most people; sensitive groups should avoid long exertion outdoors.",
          "unhealthy for children, the elderly and anyone with asthma": "Those groups should keep outdoor exertion short.",
          "unhealthy for everyone": "Keep outdoor activity short and wear an N95 mask outside.",
          "a health alert": "Stay indoors with windows closed; use a purifier if you have one.",
          "hazardous": "Stay indoors."}
cat = band_for(w.us_aqi)
st.markdown(f'<p class="advice">By the US AQI scale, the air in {worst} is currently {cat}. {advice[cat]}</p>', unsafe_allow_html=True)

# ---------- lifecycle rail ----------
st.markdown("## How the data moves through its lifecycle")
st.markdown('<p class="sub">Each figure is read live from the pipeline\'s own database and folders, so this rail is '
            'the pipeline describing itself rather than a diagram of it.</p>', unsafe_allow_html=True)
fresh_h = (datetime.now() - reading_time).total_seconds() / 3600
stops = [("1 Collect", f"{len(cities)}", "cities polled hourly from the Open-Meteo air quality service"),
         ("2 Store", f"{n.raw:,}", f"raw hourly rows, plus {len(list(RAW_DIR.glob('*.json')))} untouched JSON snapshots"),
         ("3 Clean", f"{n.clean:,}", f"rows kept; {n.imputed} short gaps filled, {n.outliers} outliers flagged"),
         ("4 Transform", f"{n.daily:,}", "city-days aggregated with an AQI category"),
         ("5 Analyse", f"{len(list(REPORT_DIR.glob('*')))}", "report files: rankings, exceedance, charts"),
         ("6 Share", f"{fresh_h:.0f} h", "since the newest reading on this page"),
         ("7 Archive", f"{n.archive:,}", "rows moved out after 60 days; aggregates are kept")]
st.markdown('<div class="rail">' + "".join(
    f'<div class="stop"><div class="name">{a}</div><div class="fig">{b}</div><div class="note">{c}</div></div>'
    for a, b, c in stops) + "</div>", unsafe_allow_html=True)

c1, c2 = st.columns([3, 2], gap="large")
with c1:
    st.markdown("**Where every row ends up**")
    total = int(n.raw + n.archive)
    dropped, valid = max(int(n.raw - n.clean), 0), int(n.clean - n.outliers)
    rows = [("Collected", total, "", f"{len(list(RAW_DIR.glob('*.json')))} raw JSON snapshots kept untouched beside the database"),
            ("Raw store", total, "", ""),
            ("Dropped", dropped, "loss", "forecast hours and gaps longer than three hours"),
            ("Clean store", int(n.clean), "", f"{n.imputed} short gaps filled by interpolation"),
            ("Flagged", int(n.outliers), "loss", "outliers kept in the store but left out of every chart"),
            ("Used in analysis", valid, "", ""),
            ("Daily summary", int(n.daily), "", "hourly rows folded into one line per city per day; these are never deleted"),
            ("Archive", int(n.archive), "", "hourly rows older than 60 days, moved out of the working tables")]
    html = '<div class="ledger">'
    for lbl, v, kind, note in rows:
        w_pct = 100 * v / max(total, 1)
        html += (f'<div class="lbl {kind}">{lbl}</div>'
                 f'<div><div class="bar {kind}" style="width:{max(w_pct, 0.6):.1f}%"></div></div>'
                 f'<div class="num {kind}">{v:,}</div>')
        if note: html += f'<div class="note">{note}</div>'
    st.markdown(html + "</div>", unsafe_allow_html=True)
with c2:
    st.markdown("**Quality of what was kept**")
    span = clean.groupby("city").time.agg(lambda s: (s.max() - s.min()).total_seconds() / 3600 + 1)
    qd = pd.DataFrame({"Complete": (clean.groupby("city").size() / span * 100).clip(upper=100).round(1).astype(str) + " %",
                       "Gaps filled": clean.groupby("city").was_imputed.sum(),
                       "Outliers": clean.groupby("city").is_outlier.sum()})
    st.dataframe(qd, use_container_width=True)
    st.caption("A gap is filled only when it is three hours or shorter. An outlier is a reading more than three standard "
               "deviations from the city's rolling seven-day mean; it is kept in the store but excluded from analysis.")

# ---------- trends ----------
st.markdown("## Each city against the limit")
st.markdown(f'<p class="sub">Daily averages for the last {window} days. The red rule is the legal limit; a red mark is a day that broke it.</p>',
            unsafe_allow_html=True)
d = daily[daily.date >= daily.date.max() - pd.Timedelta(days=window)]
fig = px.line(d, x="date", y="avg_pm2_5", facet_row="city", category_orders={"city": cities}, **PLOT)
fig.update_traces(line=dict(color=INK, width=1.3), hovertemplate="%{x|%d %b}: %{y:.0f} µg/m³")
for i, c in enumerate(cities):
    b = d[(d.city == c) & (d.avg_pm2_5 > LIMIT)]
    fig.add_scatter(x=b.date, y=b.avg_pm2_5, mode="markers", marker=dict(color=LIMIT_RED, size=5), showlegend=False,
                    row=len(cities) - i, col=1, hovertemplate="%{x|%d %b}: %{y:.0f} µg/m³<extra></extra>")
fig.add_hline(y=LIMIT, line=dict(color=LIMIT_RED, width=1))
for name, day in EVENTS.items():
    if d.date.min() <= pd.Timestamp(day) <= d.date.max():
        fig.add_vline(x=pd.Timestamp(day).timestamp() * 1000, line=dict(color=SMOKE, width=1, dash="dot"),
                      annotation_text=name, annotation_position="top left", annotation_font_color=SMOKE)
fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1], font=dict(family="Newsreader, Georgia, serif", size=15), textangle=0, x=0, xanchor="left"))
fig.update_yaxes(title=None, matches="y"); fig.update_xaxes(title=None)
st.plotly_chart(tidy(fig, 120 * len(cities) + 40), use_container_width=True)

# ---------- when is it worst ----------
st.markdown(f"## When {focus} is worst")
c1, c2 = st.columns([3, 2], gap="large")
with c1:
    h = hourly[hourly.city == focus].assign(hour=lambda x: x.time.dt.hour, dow=lambda x: x.time.dt.day_name().str[:3])
    pv = h.pivot_table(index="dow", columns="hour", values="pm2_5").reindex(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    hm = px.imshow(pv, color_continuous_scale=SCALE, zmin=0, zmax=VMAX, aspect="auto",
                   labels=dict(color="µg/m³", x="hour of day", y=""), **PLOT)
    hm.update_layout(coloraxis_colorbar=dict(thickness=8, len=.8, title=None))
    st.plotly_chart(tidy(hm, 300), use_container_width=True)
with c2:
    prof = h.groupby("hour").pm2_5.mean()
    wk = h.groupby(h.time.dt.dayofweek >= 5).pm2_5.mean()
    st.markdown(f"<p class='sub' style='margin-top:1.2rem'>Averaged over everything collected, {focus}'s air is dirtiest around "
                f"{prof.idxmax():02d}:00 ({prof.max():.0f} µg/m³) and cleanest around {prof.idxmin():02d}:00 ({prof.min():.0f} µg/m³). "
                f"Weekends run at {wk.get(True, float('nan')):.0f} µg/m³ against {wk.get(False, float('nan')):.0f} on weekdays. "
                f"Overnight peaks are typical of winter inversions, when cool air traps smoke close to the ground.</p>",
                unsafe_allow_html=True)
    over = (daily.assign(o=daily.avg_pm2_5 > LIMIT).groupby("city").o.mean() * 100).sort_values(ascending=False)
    st.markdown("**Share of days over the limit**")
    st.dataframe(over.round(0).astype(int).astype(str).add(" %").rename("Days").to_frame(), use_container_width=True)

# ---------- calendar ----------
st.markdown("## The whole record, one square per day")
cal = daily.pivot(index="city", columns="date", values="avg_pm2_5").reindex(cities)
fig = go.Figure(go.Heatmap(z=cal.values, x=cal.columns, y=cal.index, colorscale=SCALE, zmin=0, zmax=VMAX, xgap=2, ygap=6,
                           showscale=False, hovertemplate="%{y}, %{x|%d %b}: %{z:.0f} µg/m³<extra></extra>"))
fig.update_yaxes(autorange="reversed")
st.plotly_chart(tidy(fig, 50 + 34 * len(cities)), use_container_width=True)

# ---------- record keeping ----------
st.markdown("## Record keeping")
c1, c2 = st.columns([3, 2], gap="large")
with c1:
    st.markdown("**Every run the pipeline has made**")
    st.dataframe(runs[["started_at", "rows_collected", "rows_cleaned", "rows_archived", "status"]].head(12)
                 .rename(columns={"started_at": "Started", "rows_collected": "Collected", "rows_cleaned": "Cleaned",
                                  "rows_archived": "Archived", "status": "Result"}),
                 use_container_width=True, hide_index=True)
with c2:
    st.markdown("**Take the data with you**")
    st.download_button("Download daily summary", daily.to_csv(index=False), "daily_summary.csv", use_container_width=True)
    st.download_button("Download hourly readings", hourly.to_csv(index=False), "clean_readings.csv", use_container_width=True)
    rep = REPORT_DIR / "summary.md"
    if rep.exists():
        st.download_button("Download written report", rep.read_text(encoding="utf-8"), "summary.md", use_container_width=True)
    st.caption("Retention: hourly rows older than 60 days move to the archive table, raw JSON older than 30 days is deleted, "
               "and daily aggregates are kept indefinitely.")

st.markdown(f'<div class="masthead" style="border-bottom:0;border-top:1px solid {RULE};padding-top:.8rem;margin-top:3rem">'
            f'Readings from the Open-Meteo air quality service, based on CAMS (Copernicus Atmosphere Monitoring Service). '
            f'Limit figures from India\'s National Ambient Air Quality Standards.</div>', unsafe_allow_html=True)