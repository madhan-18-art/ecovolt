"""GridWise backend: detect anomalies in hourly electricity data.
Usage: python gridwise_analyze.py readings.csv --rate 7   (CSV: timestamp,kWh)
Prints JSON that can be served to the front end (e.g. via Flask/FastAPI).
"""
import csv, json, argparse
from datetime import datetime
from statistics import median
from collections import defaultdict


def load(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.reader(f):
            try:
                rows.append((datetime.fromisoformat(r[0].strip()), float(r[1])))
            except (ValueError, IndexError):
                continue  # header or bad row
    return sorted(rows)


def analyse(rows, rate=7.0):
    by_hour = defaultdict(list)
    for t, v in rows:
        by_hour[t.hour].append(v)
    med = {h: median(v) for h, v in by_hour.items()}
    mad = {h: max(median(abs(x - med[h]) for x in v), 0.05) for h, v in by_hour.items()}

    anomalies = []
    for t, v in rows:
        excess = v - med[t.hour]
        z = 0.6745 * excess / mad[t.hour]  # modified z-score
        if z > 3.5 and excess > 0.5:
            anomalies.append({"time": t.isoformat(), "kwh": v, "excess_kwh": round(excess, 2)})

    total = sum(v for _, v in rows)
    profile = {h: sum(v) / len(v) for h, v in by_hour.items()}
    peak_hours = sorted(sorted(profile, key=profile.get, reverse=True)[:4])
    waste = sum(a["excess_kwh"] for a in anomalies)
    days = len({t.date() for t, _ in rows})
    return {
        "total_kwh": round(total, 1),
        "estimated_cost": round(total * rate, 2),
        "projected_monthly_cost": round(total / days * 30 * rate, 2),
        "peak_hours": peak_hours,
        "potential_waste_kwh": round(waste, 1),
        "potential_waste_cost": round(waste * rate, 2),
        "anomalies": anomalies,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--rate", type=float, default=7.0, help="tariff in Rs/kWh")
    a = ap.parse_args()
    print(json.dumps(analyse(load(a.csv), a.rate), indent=2))
