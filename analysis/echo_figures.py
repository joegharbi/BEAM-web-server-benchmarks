"""Step 6: the echo figure (TCP echo servers in C, Java and Erlang, the MIPRO 2024 echo paper), in the style of
figures.py, as images and as data (echo_figures.json). One figure per setup: Erlang with busy-waiting on vs off
per test, with Java and C as references.

    ./srv/bin/python3 analysis/echo_figures.py --new results/<echo-new> [--rerun results/<re-run>]
                                               --old results/<echo-old> [--width one|two] [--out graphs/paper/echo]

--rerun replaces the runs of --new it measured again (same server, busy-waiting, deploy and test): the Docker
nosleep re-run with ENV_TCP_TW_REUSE=1. Images: echo_<new|old>_<container|native> with
  (a) server energy (attributed) per test: Erlang off -> on with the ratio; Java and C as reference marks
  (b) platform energy (measured) saved with busy-waiting off, with its 95% range
Where the meter fell behind in most runs of a server and test (tools/progress.py meter_behind: C in nosleep, one
thread per connection, up to 10,000, stalled Scaphandre), its server energy is the one of the CPU-time method
(data.energy_by_cpu_time) instead, marked with a dagger; everything else is the meter's (Scaphandre).
  echo_spread_<setup>_<deploy>  the 5 runs of Erlang per test, as % from their median, busy-waiting on vs off,
                                with the spread (CV) of each: how jumpy the measurement is
echo_figures.json also holds, per test, the latency and throughput of every server (for the meeting page; not a
paper figure: the Python client's ~1.5 ms round trip hides wake-up gains of microseconds).
"""
import argparse
import json
import os
import random
import sys

import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import data  # noqa: E402
import figures  # noqa: E402
import stats  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import progress  # noqa: E402

ON, OFF, JAVA, C, MUTED, GRID = figures.ON, figures.OFF, figures.JAVA, "#9c3d8f", figures.MUTED, figures.GRID
ERLANG = "ec-erlang-pure-29-1-1"
REFERENCES = [("java", "ec-java-pure-27", JAVA, "^", "Java (reference)", -0.14),   # last: offset below the row centre
              ("c", "ec-c-pure-14", C, "s", "C (reference)", -0.3)]
TESTS = {   # load (data.load) -> label; ordered from "the server mostly waits" to "it is always busy"
    "new": [("connect 100 clients spacing 0.1 s", "sleep, 100 clients"),
            ("connect 1000 clients spacing 0.1 s", "sleep, 1,000 clients"),
            ("stream 100 clients 10/s", "stream, 100 × 10 msg/s"),
            ("burst 100 clients 2000 msg", "burst, 100 × 2,000 msg"),
            ("nosleep 10000 clients x5 rounds", "nosleep, 10,000 × 5")],
    "old": [("connect 100 clients spacing 0.1 s", "sleep, 100 clients"),
            ("connect 1000 clients spacing 0.1 s", "sleep, 1,000 clients"),
            ("connect 10000 clients spacing 0.1 s", "sleep, 10,000 clients"),
            ("nosleep 4000 clients", "nosleep, 4,000 clients")],
}
SETUP = {"new": "new setup", "old": "old setup (MIPRO 2024)"}
WHERE = {"container": "Docker (container)", "native": "native (no Docker)"}
CAPTION = ("TCP echo servers, busy-waiting on (default) vs off (+sbwt none) for Erlang, per test, with Java and C as "
           "references (no busy-waiting). (a) Energy attributed to the server process (CPU-time share), log scale; "
           "(b) platform energy (RAPL psys, measured) saved with busy-waiting off, with bootstrap 95% confidence "
           "intervals. Tests ordered from mostly waiting (clients 0.1 s apart) to always busy (all clients at once). "
           "\u2020 C, nosleep: the meter fell behind (up to 10,000 threads), so this value is the server's exact "
           "CPU time \u00d7 the platform's price per CPU-second over the load (CPU-time method).")
SPREAD_CAPTION = ("Run-to-run variation of the attributed server energy of the Erlang echo server: its 5 runs per test as "
                  "deviation from that test's median, busy-waiting on vs off. Right: coefficient of variation on / off.")


def runs(folders):
    """{(server, deploy, 'on'|'off', load): {"server_j": [...], "machine_j": [...], "behind": n}}; a later folder
    replaces the runs of an earlier one with the same key (the re-run of a test)."""
    out = {}
    for folder in folders:
        found = {}
        for r in data.rows(folder):
            g = found.setdefault((data.server(r), r["Deploy"], data.busy_wait(r), data.load(r)),
                                 {"server_j": [], "machine_j": [], "cpu_j": [], "behind": 0})
            g["server_j"].append(data.num(r, "Container Energy (J)"))
            g["cpu_j"].append(data.energy_by_cpu_time(r, data.cpu_counter_exact(folder)))
            g["machine_j"].append(data.num(r, "Host Energy (J)"))
            g["behind"] += progress.meter_behind(r)
            g.setdefault("latency_ms", []).append(data.num(r, "Avg Latency (ms)"))
            g.setdefault("rate", []).append(data.num(r, "Messages/s"))
            g["failed"] = g.get("failed", 0) + int(data.num(r, "Failed Messages") or 0)
        out.update(found)
    return out


def coarse(g):
    return g["behind"] * 2 > len(g["server_j"])


def server_energy(g):
    """(median server energy, method): the meter's, or the CPU-time method's where the meter fell behind in most
    runs (only with an exact CPU counter; without one the meter's value stays, marked 'coarse')."""
    if coarse(g) and None not in g["cpu_j"]:
        return data.median(g["cpu_j"]), "cpu-time"
    return data.median(g["server_j"]), "coarse" if coarse(g) else "meter"


def echo(r, which, deploy, width, path, dpi):
    """(a) server energy: Erlang off -> on with the ratio, Java and C as references; (b) platform saved with off."""
    tests = [t for t in TESTS[which] if (ERLANG, deploy, "on", t[0]) in r]
    rng = random.Random(stats.SEED)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(figures.WIDTH_IN[width], 0.36 * len(tests) + 0.95),
                               gridspec_kw={"width_ratios": [1.5, 1], "wspace": 0.08})
    y = list(range(len(tests)))[::-1]
    a.set_yticks(y, [t[1] for t in tests])
    out, any_coarse = [], False
    for yi, (load, name) in zip(y, tests):
        on, off = r[(ERLANG, deploy, "on", load)], r[(ERLANG, deploy, "off", load)]
        s_on, s_off = data.median(on["server_j"]), data.median(off["server_j"])
        row = dict(test=load, label=name, server_on=s_on, server_off=s_off,
                   ratio=stats.ratio(on["server_j"], off["server_j"]),
                   ratio_ci=stats.bootstrap(on["server_j"], off["server_j"], stats.ratio, rng),
                   saved=stats.saved(on["machine_j"], off["machine_j"]),
                   saved_ci=stats.bootstrap(on["machine_j"], off["machine_j"], stats.saved, rng),
                   platform_on=data.median(on["machine_j"]), platform_off=data.median(off["machine_j"]))
        yb = yi + 0.12                                     # Erlang just above the row's centre, references below
        a.plot([s_off, s_on], [yb, yb], color=GRID, lw=2.2, zorder=1, solid_capstyle="round")
        for v, color in ((s_off, OFF), (s_on, ON)):
            a.scatter(v, yb, s=16, color=color, zorder=3, edgecolors="white", linewidths=0.6)
        a.text(s_on * 1.3, yb, f"{row['ratio']:.1f}×", va="center", fontsize=6.5, color=MUTED)
        for key, server, color, marker, _, dy in REFERENCES:
            g = r.get((server, deploy, "on", load))
            if not g:
                continue
            value, method = server_energy(g)
            any_coarse |= method != "meter"
            row[key] = dict(server_j=value, method=method, meter_j=data.median(g["server_j"]),
                            platform_j=data.median(g["machine_j"]))
            a.scatter(value, yi + dy, s=13, marker=marker, zorder=3, color=color, edgecolors="white", linewidths=0.6)
            if method != "meter":
                a.text(value / 1.25, yi + dy, "\u2020", va="center", ha="right", fontsize=7, color=MUTED)
        lo, hi = row["saved_ci"]
        b.plot([lo, hi], [yi, yi], color=OFF, lw=0.9, alpha=0.55, zorder=2)
        b.scatter(row["saved"], yi, s=14, color=OFF, zorder=3, marker="s", edgecolors="white", linewidths=0.5)
        out.append(row)
    for yi in y[:-1]:
        for ax in (a, b):
            ax.axhline(yi - 0.5, color=GRID, lw=0.6, zorder=0)
    a.set_xscale("log")                       # equal steps = equal ratios
    a.xaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=(1,)))
    a.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    a.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    lo, hi = a.get_xlim()
    a.set_xlim(lo, hi * 2.2)
    a.set_xlabel("Server energy (J, log scale)")
    a.set_title("(a) Server, attributed", loc="left")
    b.axvline(0, color=MUTED, lw=0.6)
    b.set_xlabel("Saved with off (%)")
    b.set_title("(b) Platform, measured", loc="left")
    items = [figures.dot(ON, "Erlang, busy-waiting on"), figures.dot(OFF, "Erlang, off")]
    items += [figures.dot(color, label, marker) for _, _, color, marker, label, _ in REFERENCES]
    items.append(plt.Line2D([], [], color=OFF, lw=0.9, alpha=0.55, marker="s", markersize=3.5,
                            label="saved, 95% CI"))
    if any_coarse:
        items.append(plt.Line2D([], [], ls="", marker="$\u2020$", color=MUTED, markersize=5,
                                label="CPU-time method (caption)"))
    figures.legend(fig, items, ncol=3)
    fig.subplots_adjust(top=1 - (0.42 + 0.17) / fig.get_size_inches()[1])   # room for the legend's second row
    figures.save(fig, path, dpi)
    return out


def spread(r, which, deploy, width, path, dpi):
    """The 5 runs of Erlang per test as % from their median: busy-waiting on scatters, off stays close."""
    tests = [t for t in TESTS[which] if (ERLANG, deploy, "on", t[0]) in r]
    fig, a = plt.subplots(figsize=(figures.WIDTH_IN[width], 0.22 * len(tests) + 0.75))
    y = list(range(len(tests)))[::-1]
    a.set_yticks(y, [t[1] for t in tests])
    out = []
    for yi, (load, name) in zip(y, tests):
        row = dict(test=load, label=name)
        for bw, color, dy in (("on", ON, 0.13), ("off", OFF, -0.13)):
            values = r[(ERLANG, deploy, bw, load)]["server_j"]
            m = data.median(values)
            dev = [(v / m - 1) * 100 for v in values]
            row[f"deviation_{bw}"], row[f"cv_{bw}"] = dev, data.cv(values)
            a.scatter(dev, [yi + dy] * len(dev), s=9, color=color, alpha=0.85, zorder=3, edgecolors="none")
        a.text(1.01, yi, f"{row['cv_on']:.0f}% / {row['cv_off']:.0f}%", transform=a.get_yaxis_transform(),
               va="center", fontsize=6.5, color=MUTED)
        out.append(row)
    a.text(1.01, len(tests) - 0.35, "CV on / off", transform=a.get_yaxis_transform(), fontsize=6.5, color=MUTED)
    a.axvline(0, color=MUTED, lw=0.6)
    a.set_xlabel("Server energy of each run, from its median (%)")
    figures.legend(fig, [figures.dot(ON, "Erlang, busy-waiting on, 5 runs"), figures.dot(OFF, "off, 5 runs")],
                   ncol=2, titles=False)
    figures.save(fig, path, dpi)
    return out


def latency(r, which, deploy):
    """Per test: average latency (ms) and messages/s of every server, medians of 5 runs, and failed messages."""
    out = []
    for load, name in TESTS[which]:
        row = dict(test=load, label=name)
        for key, server, bw in (("erlang_on", ERLANG, "on"), ("erlang_off", ERLANG, "off"),
                                ("java", REFERENCES[0][1], "on"), ("c", REFERENCES[1][1], "on")):
            g = r.get((server, deploy, bw, load))
            if g:
                row[key] = dict(latency_ms=data.median(g["latency_ms"]), messages_per_s=data.median(g["rate"]),
                                failed=g["failed"])
        out.append(row)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--new", required=True, help="results folder of campaigns/paper/echo-new.config")
    p.add_argument("--rerun", default=None, help="results folder of a re-run of some of --new's tests")
    p.add_argument("--old", required=True, help="results folder of campaigns/paper/echo-old.config")
    p.add_argument("--width", choices=figures.WIDTH_IN, default="one")
    p.add_argument("--format", choices=["png", "jpg", "svg"], default="png")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--out", default="graphs/paper/echo")
    p.add_argument("--title", action="store_true", help="draw the setting on top of every figure (as figures.py)")
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    figures.style()
    new_folders = [args.new] + ([args.rerun] if args.rerun else [])
    figures.CONTEXT["show"], figures.CONTEXT["sources"] = args.title, new_folders + [args.old]
    result, spreads, latencies = {}, {}, {}
    for which, r in (("new", runs(new_folders)), ("old", runs([args.old]))):
        for deploy in ("container", "native"):
            key = f"{which}_{deploy}"
            figures.CONTEXT["text"] = f"TCP echo, {SETUP[which]} · {WHERE[deploy]} · median of 5 runs"
            figures.CONTEXT["caption"] = CAPTION
            path = os.path.join(args.out, f"echo_{key}.{args.format}")
            print(path)
            result[key] = echo(r, which, deploy, args.width, path, args.dpi)
            figures.CONTEXT["text"] = f"TCP echo, {SETUP[which]} · {WHERE[deploy]} · Erlang · 5 runs"
            figures.CONTEXT["caption"] = SPREAD_CAPTION
            path = os.path.join(args.out, f"echo_spread_{key}.{args.format}")
            print(path)
            spreads[key] = spread(r, which, deploy, args.width, path, args.dpi)
            latencies[key] = latency(r, which, deploy)
    with open(os.path.join(args.out, "echo_figures.json"), "w") as fh:
        json.dump({"source": {"new": new_folders, "old": args.old}, "figures": result, "spread": spreads,
                   "latency": latencies}, fh, indent=1)
    print(os.path.join(args.out, "echo_figures.json"))
    with open(os.path.join(args.out, "FIGURES.md"), "w") as fh:
        fh.write(f"# Echo figures in this folder\n\nMade by `analysis/echo_figures.py` ({args.width} column, "
                 f"{args.dpi} dpi) from: new {', '.join(new_folders)}; old {args.old}\n\n")
        for name, where, caption in sorted(figures.DESCRIBED):
            fh.write(f"## {name}\n\n- **Shows:** {where}\n- **Draft caption:** {caption}\n\n")
    print(os.path.join(args.out, "FIGURES.md"))


if __name__ == "__main__":
    sys.exit(main())
