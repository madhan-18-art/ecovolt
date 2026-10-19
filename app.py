"""GridWise - Smart Electricity Waste Detector (full Python version)
Run:  pip install flask
      python app.py       then open http://127.0.0.1:5000
CSV format: timestamp,kWh   (hourly)   e.g. 2026-09-01 14:00,1.35
"""
import random
from datetime import datetime, timedelta
from statistics import median
from collections import defaultdict
from flask import Flask, request, render_template_string

app = Flask(__name__)


# ---------- 1. DATA ----------
def sample_data():
    """30 days of hourly data with 3 planted problems."""
    rnd = random.Random(42)
    base = [.4, .35, .35, .35, .4, .6, 1, 1.4, 1.2, .9, .8, .8,
            .9, .9, .9, 1, 1.3, 1.9, 2.4, 2.5, 2.3, 1.6, .9, .55]
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    rows = []
    for d in range(30):
        for h in range(24):
            v = base[h] * (.9 + rnd.random() * .2)
            if d == 11 and 1 <= h <= 5: v += 2.6    # AC left on overnight
            if d == 20 and 13 <= h <= 15: v += 4.2  # heavy appliance spike
            if d == 25: v += 1.4                    # whole day high
            rows.append((start + timedelta(days=d, hours=h), round(v, 2)))
    return rows


def parse_csv(text):
    rows = []
    for line in text.splitlines():
        p = line.split(",")
        try:
            rows.append((datetime.fromisoformat(p[0].strip()), float(p[1])))
        except (ValueError, IndexError):
            continue  # header / bad line
    return sorted(rows)


# ---------- 2. ANALYSIS ----------
def analyse(rows, rate):
    by_hour = defaultdict(list)
    for t, v in rows:
        by_hour[t.hour].append(v)
    med = {h: median(v) for h, v in by_hour.items()}
    mad = {h: max(median(abs(x - med[h]) for x in v), 0.05) for h, v in by_hour.items()}
    prof = [sum(by_hour[h]) / len(by_hour[h]) if by_hour[h] else 0 for h in range(24)]

    # flag hours far above the normal for that same hour (modified z-score)
    flags = []
    for t, v in rows:
        ex = v - med[t.hour]
        if 0.6745 * ex / mad[t.hour] > 3.5 and ex > 0.5:
            flags.append((t.date().isoformat(), t.hour, v, ex))

    # group consecutive flagged hours into events
    events = []
    for day, h, v, ex in flags:
        if events and events[-1]["day"] == day and h - events[-1]["end"] <= 1:
            e = events[-1]
            e["end"], e["ex"], e["peak"] = h, e["ex"] + ex, max(e["peak"], v)
        else:
            events.append({"day": day, "start": h, "end": h, "ex": ex, "peak": v})

    days = defaultdict(float)
    for t, v in rows:
        days[t.date().isoformat()] += v
    total, nd = sum(days.values()), len(days)
    day_avg = total / nd
    night = sum(prof[1:5]) / 4
    rank = sorted(sorted(range(24), key=lambda h: prof[h], reverse=True)[:4])
    peak_share = sum(prof[h] for h in rank) / sum(prof)
    waste = sum(e["ex"] for e in events)

    recs = []
    if night > 0.3:
        recs.append(f"Overnight base load (1-5 AM) is about {night:.2f} kWh/hour. Switch off standby devices and idle appliances.")
    if any(e["start"] <= 5 for e in events):
        recs.append("Overnight spikes found. Use timers or smart plugs for AC, geyser and water pump.")
    if any(e["start"] > 5 for e in events):
        recs.append("Daytime spikes suggest heavy appliances running together. Stagger their use.")
    recs.append(f"{peak_share*100:.0f}% of energy is used between {rank[0]:02d}:00 and {(rank[-1]+1)%24:02d}:00. Shift flexible loads (laundry, pumping, charging) out of this window.")
    if waste > 0:
        recs.append(f"Fixing the flagged events could save about Rs {waste*rate:,.0f} in this period (about Rs {waste*rate/nd*30:,.0f} per month if they repeat).")
    recs.append("Set AC to 24-26 C, use BEE 5-star appliances and LED lighting.")

    for e in events:
        e["when"] = f"{e['start']:02d}:00" if e["start"] == e["end"] else f"{e['start']:02d}:00-{e['end']+1:02d}:00"
        e["night"] = e["start"] <= 5
        e["cost"] = e["ex"] * rate
    return dict(total=total, nd=nd, cost=total * rate, month=day_avg * 30 * rate, day_avg=day_avg,
                waste=waste, wcost=waste * rate, rank=rank, peak_share=peak_share,
                events=sorted(events, key=lambda e: -e["ex"])[:6], days=dict(sorted(days.items())),
                prof=prof, recs=recs, rate=rate)


# ---------- 3. CHARTS (server-side SVG) ----------
def bars(vals, labels, colors, unit, step):
    W, H, L, B = 720, 200, 34, 22
    mx = (max(vals) * 1.1) or 1
    bw = (W - L) / len(vals)
    s = f'<svg viewBox="0 0 {W} {H}">'
    for i in range(5):
        y = H - B - (H - B - 8) * i / 4
        s += f'<line x1="{L}" x2="{W}" y1="{y}" y2="{y}" stroke="#dce5e0"/><text x="{L-4}" y="{y+3}" text-anchor="end">{mx*i/4:.1f}</text>'
    for i, v in enumerate(vals):
        h = (H - B - 8) * v / mx
        x = L + i * bw
        s += f'<rect x="{x+1}" y="{H-B-h}" width="{max(bw-2,1)}" height="{h}" rx="2" fill="{colors[i]}"><title>{labels[i]}: {v:.1f} {unit}</title></rect>'
        if i % step == 0:
            s += f'<text x="{x+bw/2}" y="{H-6}" text-anchor="middle">{labels[i]}</text>'
    return s + "</svg>"


# ---------- 4. WEB PAGE ----------
PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GridWise</title><style>
body{margin:0;background:#f4f7f5;color:#14231c;font:15px/1.5 system-ui,sans-serif}.w{max-width:960px;margin:auto;padding:20px 16px 48px}
h1 span{color:#0f8a5f}.card,.k{background:#fff;border:1px solid #dce5e0;border-radius:14px;padding:16px;margin-bottom:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}.k{margin:0}.k b{display:block;font-size:22px}
small,.mut{color:#5d6f66}button{background:#0f8a5f;color:#fff;border:0;border-radius:9px;padding:9px 14px;font-weight:600;cursor:pointer}
input{padding:7px;border:1px solid #dce5e0;border-radius:9px}svg{width:100%;height:auto}text{fill:#5d6f66;font-size:10px}
.g{display:grid;gap:14px}@media(min-width:760px){.g{grid-template-columns:1fr 1fr}}
.al{border-left:4px solid #d93f3f;padding:8px 12px;margin:8px 0;background:#fdeeee;border-radius:0 8px 8px 0}.al.w{border-color:#e0a21a;background:#fdf6e3}
</style></head><body><div class="w">
<h1>Grid<span>Wise</span> ⚡</h1><p class="mut">Upload hourly electricity readings and spot waste, unusual usage and peak demand.</p>
<form class="card" method="post" enctype="multipart/form-data">
<button name="action" value="sample">Load sample data</button> &nbsp; or &nbsp;
<input type="file" name="file" accept=".csv,.txt"> <button name="action" value="upload">Analyse CSV</button> &nbsp;
Tariff Rs/kWh <input type="number" name="rate" value="{{ rate }}" step="0.1" min="0" style="width:80px">
<p><small>CSV format: timestamp,kWh per hour, e.g. 2026-09-01 14:00,1.35</small></p></form>
{% if error %}<div class="card">{{ error }}</div>{% endif %}
{% if a %}
<div class="kpis">
<div class="k"><small>Total used</small><b>{{ '{:,.0f}'.format(a.total) }} kWh</b><small>{{ a.nd }} days</small></div>
<div class="k"><small>Estimated bill</small><b>Rs {{ '{:,.0f}'.format(a.cost) }}</b><small>at Rs {{ a.rate }}/kWh</small></div>
<div class="k"><small>Projected / month</small><b>Rs {{ '{:,.0f}'.format(a.month) }}</b><small>{{ '%.1f'|format(a.day_avg) }} kWh/day</small></div>
<div class="k"><small>Potential waste</small><b>{{ '{:,.0f}'.format(a.waste) }} kWh</b><small>about Rs {{ '{:,.0f}'.format(a.wcost) }} lost</small></div>
<div class="k"><small>Peak window</small><b>{{ '%02d'|format(a.rank[0]) }}:00-{{ '%02d'|format((a.rank[-1]+1)%24) }}:00</b><small>{{ '%.0f'|format(a.peak_share*100) }}% of usage in 4 hrs</small></div>
</div>
<div class="card"><h3>Daily consumption (red = contains anomaly)</h3>{{ c1|safe }}</div>
<div class="g"><div class="card"><h3>Average usage by hour (orange = high demand)</h3>{{ c2|safe }}</div>
<div class="card"><h3>Anomaly alerts</h3>
{% for e in a.events %}<div class="al {{ '' if e.ex>3 else 'w' }}"><b>{{ e.day }}, {{ e.when }}</b> - {{ '%.1f'|format(e.ex) }} kWh above normal (peak {{ '%.1f'|format(e.peak) }} kWh)
<br><small>{{ 'Overnight load: something may have been left running.' if e.night else 'Unusual spike compared with the same hour on other days.' }} Cost about Rs {{ '{:,.0f}'.format(e.cost) }}</small></div>
{% else %}<p>No unusual usage detected.</p>{% endfor %}</div></div>
<div class="card"><h3>Energy-saving recommendations</h3><ul>{% for r in a.recs %}<li>{{ r }}</li>{% endfor %}</ul></div>
{% endif %}</div></body></html>"""


class Obj(dict):  # lets the template use a.total instead of a['total']
    __getattr__ = dict.get


@app.route("/", methods=["GET", "POST"])
def home():
    ctx = dict(a=None, error=None, rate=7.0, c1="", c2="")
    if request.method == "POST":
        try:
            ctx["rate"] = float(request.form.get("rate") or 7)
        except ValueError:
            ctx["rate"] = 7.0
        if request.form.get("action") == "sample":
            rows = sample_data()
        else:
            f = request.files.get("file")
            rows = parse_csv(f.read().decode("utf-8", "ignore")) if f and f.filename else []
        if len(rows) < 24:
            ctx["error"] = "Need at least 24 hourly readings (timestamp,kWh)."
        else:
            a = analyse(rows, ctx["rate"])
            bad = {e["day"] for e in a["events"]}
            ks = list(a["days"])
            ctx["c1"] = bars([a["days"][k] for k in ks], [k[5:] for k in ks],
                             ["#d93f3f" if k in bad else "#7cc4a4" for k in ks], "kWh", max(1, len(ks) // 10))
            ctx["c2"] = bars(a["prof"], [f"{h:02d}" for h in range(24)],
                             ["#e0a21a" if h in a["rank"] else "#7cc4a4" for h in range(24)], "kWh", 3)
            ctx["a"] = Obj(a)
    return render_template_string(PAGE, **ctx)


if __name__ == "__main__":
    app.run(debug=True)
