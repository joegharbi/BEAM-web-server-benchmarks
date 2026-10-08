#!/usr/bin/env python3
"""Statistics per configuration of repeated measurement runs, with outlier checks.

Reads one or more results CSVs produced by measure_docker.py or measure_websocket.py,
where the same configuration appears on several rows (one row per repeat). It groups
the rows by configuration (server, workload, load and settings) and reports, for every
measured column: mean, sd, +/-95% (half-width of the 95% confidence interval of the
mean), median, Q1, Q3, IQR, min, max and CV% (sd as a percentage of the mean).

For energy it also applies two outlier rules and reports the mean after each, with how
many runs each rule dropped, so you can see what a rule changed; the raw statistics
are always reported as well:
  * IQR     - drop runs outside Q1 - 1.5 IQR .. Q3 + 1.5 IQR (the box-plot rule)
  * Hampel  - drop runs more than 3 scaled MADs from the median

With a single run the measures of spread (sd, +/-95%, IQR, CV%) are left empty.

Standard library only. Usage:
  python3 tools/aggregate_repeats.py results/manual/st-erlang-index-29-1-1_repeats.csv
  python3 tools/aggregate_repeats.py in.csv --output summary.csv
  python3 tools/aggregate_repeats.py static/*.csv --output static/summary.csv   # all servers in one table
"""
import argparse
import csv
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import csv_columns  # noqa: E402

# t value for a 95% two-sided interval, by degrees of freedom (number of runs - 1),
# to 4 decimals. Above 30 a Cornish-Fisher expansion around the normal value is used,
# which matches the exact t value to 4 decimals there.
_T95 = {1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706, 6: 2.4469, 7: 2.3646,
        8: 2.3060, 9: 2.2622, 10: 2.2281, 11: 2.2010, 12: 2.1788, 13: 2.1604, 14: 2.1448,
        15: 2.1314, 16: 2.1199, 17: 2.1098, 18: 2.1009, 19: 2.0930, 20: 2.0860, 21: 2.0796,
        22: 2.0739, 23: 2.0687, 24: 2.0639, 25: 2.0595, 26: 2.0555, 27: 2.0518, 28: 2.0484,
        29: 2.0452, 30: 2.0423}


def t95(df):
    if df <= 0:
        return 0.0
    if df in _T95:
        return _T95[df]
    z = 1.959964
    return (z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3))


# Columns that name a configuration. Rows sharing these are repeats of one thing.
# Everything else that looks numeric is averaged.
KEY_COLS = ["Container Name", "Variant", "Deploy", "Type", "Test Type", "Total Requests", "HTTP Max Workers",
            "HTTP Connection Mode", "Pattern", "Num Clients", "Message Size (KB)",
            "Rate (msg/s)", "Bursts", "Interval (s)", "Duration (s)", "Energy Sampling Step (ms)"]


def is_number(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def mean_and_giveortake(values):
    """Return (n, mean, give-or-take). Give-or-take is 0 when there is one value."""
    mean = statistics.mean(values)
    if len(values) >= 2:
        sd = statistics.stdev(values)
        ci = t95(len(values) - 1) * sd / math.sqrt(len(values))
    else:
        ci = 0.0
    return len(values), mean, ci


def _quantile(sorted_vals, q):
    """Value at fraction q (0..1) of a sorted list, with linear interpolation."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (pos - lo) * (sorted_vals[hi] - sorted_vals[lo])


def iqr_keep(values):
    """Return a True/False list: True to keep, False if flagged as an outlier (IQR)."""
    s = sorted(values)
    q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
    iqr = q3 - q1
    if iqr == 0:                       # no spread to judge against; keep all
        return [True] * len(values)
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [lo <= v <= hi for v in values]


def hampel_keep(values):
    """Return a True/False list: True to keep, False if flagged as an outlier (Hampel)."""
    med = statistics.median(values)
    scaled_mad = 1.4826 * statistics.median([abs(v - med) for v in values])
    if scaled_mad == 0:               # typical wobble is zero; keep all
        return [True] * len(values)
    return [abs(v - med) <= 3 * scaled_mad for v in values]


def filtered(values, keep):
    return [v for v, k in zip(values, keep) if k]


# Statistics written for every measured column, in this order.
STATS = ["mean", "sd", "+/-95%", "median", "Q1", "Q3", "IQR", "min", "max", "CV%"]


def describe(values):
    """Statistics of one column within one configuration.

    sd is the sample standard deviation; +/-95% is the half-width of the 95% confidence
    interval of the mean (t distribution); Q1/Q3 use linear interpolation (as numpy/R
    default); CV% = sd / mean * 100. Measures of spread are left empty with a single run,
    because one run says nothing about spread.
    """
    s = sorted(values)
    n = len(s)
    mean = statistics.mean(s)
    q1, med, q3 = _quantile(s, 0.25), statistics.median(s), _quantile(s, 0.75)
    if n >= 2:
        sd = statistics.stdev(s)
        ci = t95(n - 1) * sd / math.sqrt(n)
        iqr = q3 - q1
        cv = 100 * sd / mean if mean else ""
    else:
        sd = ci = iqr = cv = ""
    vals = [mean, sd, ci, med, q1, q3, iqr, s[0], s[-1], cv]
    return [round(v, 4) if v != "" else "" for v in vals]


def read_rows(paths):
    """Rows of all CSVs with current column names (files of earlier releases are translated)."""
    rows, headers = [], []
    for path in paths:
        header, file_rows = csv_columns.read(path)
        for h in header:
            if h not in headers:
                headers.append(h)
        rows.extend(file_rows)
    return rows, headers


def main():
    ap = argparse.ArgumentParser(description="Statistics per configuration of repeated runs.")
    ap.add_argument("input_csv", nargs="+", help="One or more CSVs from measure_docker.py or measure_websocket.py")
    ap.add_argument("--output", default=None,
                    help="Summary CSV path (default for one input: <input>_summary.csv; required for several)")
    args = ap.parse_args()
    if len(args.input_csv) > 1 and not args.output:
        sys.exit("--output is required when summarising several CSVs together.")

    rows, headers = read_rows(args.input_csv)
    if not rows:
        sys.exit("No rows in the input file(s).")

    key_cols = [c for c in KEY_COLS if c in headers]
    value_cols = [c for c in headers
                  if c not in key_cols and c not in csv_columns.NOT_MEASURED
                  and any(is_number(r.get(c, "")) for r in rows)]
    energy_col = "Container Energy (J)" if "Container Energy (J)" in value_cols else None

    groups = {}
    for r in rows:
        groups.setdefault(tuple(r.get(c, "") for c in key_cols), []).append(r)

    out = args.output or os.path.splitext(args.input_csv[0])[0] + "_summary.csv"
    summary_headers = key_cols + ["Repeats"]
    for c in value_cols:
        summary_headers += [f"{c} {stat}" for stat in STATS]
    if energy_col:
        for rule in ("IQR", "Hampel"):
            summary_headers += [f"{energy_col} mean, {rule}-filtered", f"{energy_col} +/-95%, {rule}-filtered",
                                f"{energy_col} runs dropped, {rule} rule"]

    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(summary_headers)
        for key, grp in groups.items():
            row = list(key) + [len(grp)]
            for c in value_cols:
                vals = [float(r[c]) for r in grp if is_number(r.get(c, ""))]
                row += describe(vals) if vals else [""] * len(STATS)
            if energy_col:
                ev = [float(r[energy_col]) for r in grp if is_number(r.get(energy_col, ""))]
                for keep_fn in (iqr_keep, hampel_keep):
                    if len(ev) >= 2:
                        kept = filtered(ev, keep_fn(ev))
                        _, m, ci = mean_and_giveortake(kept)
                        row += [round(m, 4), round(ci, 4) if len(kept) >= 2 else "", len(ev) - len(kept)]
                    else:
                        row += ["", "", 0]
            w.writerow(row)

    print(f"Wrote {out}  ({len(groups)} configuration(s))\n")
    label_cols = [c for c in ("Container Name", "Total Requests", "Pattern", "Num Clients")
                  if c in key_cols]
    if energy_col:
        print("Energy (J): median [Q1-Q3], mean +/- 95%, and the mean after each outlier rule:")
        for key, grp in groups.items():
            d = dict(zip(key_cols, key))
            ev = [float(r[energy_col]) for r in grp if is_number(r.get(energy_col, ""))]
            if not ev:
                continue
            label = " ".join(str(d.get(c, "")) for c in label_cols)
            st = dict(zip(STATS, describe(ev)))
            if len(ev) < 2:
                print(f"  {label:<30} {st['mean']:8.3f} (only 1 run)")
                continue
            line = (f"  {label:<30} median {st['median']:8.3f} [{st['Q1']:.3f}-{st['Q3']:.3f}]"
                    f" mean {st['mean']:8.3f} +/- {st['+/-95%']:.3f} (n={len(ev)}, CV {st['CV%']:.1f}%)")
            for name, keep_fn in (("IQR", iqr_keep), ("Hampel", hampel_keep)):
                kept = filtered(ev, keep_fn(ev))
                line += f" | {name} {statistics.mean(kept):8.3f} (dropped {len(ev) - len(kept)})"
            print(line)


if __name__ == "__main__":
    main()
