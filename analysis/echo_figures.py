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
  echo_spread_<setup>_<deploy>  the 5 runs per test, as % from their median: Erlang busy-waiting on vs off, and
                                Java and C as references; with the spread (CV) of each: how jumpy the measurement is
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
           "(b) platform energy (RAPL psys, measured), log scale. Tests ordered from mostly waiting (clients 0.1 s "
           "apart) to always busy (all clients at once). \u2020 C, nosleep: the meter fell behind (a new thread per "
           "connection, about 2,000 per second); (a) is the server's exact CPU time \u00d7 the platform's price per "
           "CPU-second over the load (CPU-time method); (b) includes the meter's own extra work, about 20% (measured "
           "with the meter on and off).")
SPREAD_CAPTION = ("Run-to-run variation of the attributed server energy: the 5 runs per test as deviation from that "
                  "test's median, for Erlang with busy-waiting on and off and for the references without busy-waiting "
                  "(Java, C). Right: coefficient of variation. Sleep with 100 clients: the servers use 0.005-0.07 J, "
                  "so a single meter reading changes a run by tens of percent. "
                  "\u2020 C, nosleep: CPU-time method (the meter fell behind).")


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
    """(a) server energy, attributed; (b) platform energy, measured: in both, Erlang off -> on, Java and C as
    references (as panel (b) of the HTTP Fig. 4). Each test row has three lanes: Erlang (on/off), Java, C."""
    tests = [t for t in TESTS[which] if (ERLANG, deploy, "on", t[0]) in r]
    rng = random.Random(stats.SEED)
    lay = figures.Rows(len(tests), 3, extra_in=0.17)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(figures.WIDTH_IN[width], lay.height),
                               gridspec_kw={"width_ratios": [1, 1], "wspace": 0.08})
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
        figures.pair(a, s_off, s_on, yi + lay.lane(0), lay)
        figures.ratio_label(a, s_on, yi + lay.lane(0), f"{row['ratio']:.1f}×")
        figures.pair(b, row["platform_off"], row["platform_on"], yi + lay.lane(0), lay)
        for lane, (key, server, color, marker, *_) in enumerate(REFERENCES, start=1):
            g = r.get((server, deploy, "on", load))
            if not g:
                continue
            value, method = server_energy(g)
            any_coarse |= method != "meter"
            row[key] = dict(server_j=value, method=method, meter_j=data.median(g["server_j"]),
                            platform_j=data.median(g["machine_j"]))
            figures.point(a, value, yi + lay.lane(lane), color, marker)
            figures.point(b, row[key]["platform_j"], yi + lay.lane(lane), color, marker)
            if method != "meter":
                for ax, v in ((a, value), (b, row[key]["platform_j"])):
                    ax.annotate("\u2020", (v, yi + lay.lane(lane)), xytext=(-6, 0), textcoords="offset points",
                                va="center", ha="right", fontsize=7, color=MUTED)
        out.append(row)
    for yi in y[:-1]:
        for ax in (a, b):
            ax.axhline(yi - 0.5, color=GRID, lw=0.6, zorder=0)
    for ax, subs in ((a, (1,)), (b, (1, 2, 5))):
        ax.set_xscale("log")                  # equal steps = equal ratios
        ax.xaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=subs))
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    lo, hi = a.get_xlim()
    a.set_xlim(lo, hi * 2.2)
    a.set_xlabel("Server energy (J, log scale)")
    a.set_title("(a) Server, attributed", loc="left")
    b.set_xlabel("Platform energy (J, log scale)")
    b.set_title("(b) Platform, measured", loc="left")
    items = [figures.dot(ON, "Erlang, busy-waiting on"), figures.dot(OFF, "Erlang, off")]
    items += [figures.dot(color, text, marker) for _, _, color, marker, text, _ in REFERENCES]
    if any_coarse:
        items.append(plt.Line2D([], [], ls="", marker="$\u2020$", color=MUTED, markersize=figures.LEGEND,
                                label="CPU-time method (caption)"))
    figures.legend(fig, items, ncol=3, legend_rows=2)
    figures.save(fig, path, dpi)
    return out


def spread(r, which, deploy, width, path, dpi):
    """The 5 runs per test as % from their median: Erlang with busy-waiting on scatters, off stays close, and so do
    the references without busy-waiting (Java, C). Where the meter fell behind, the CPU-time method's values.
    Each test row has four lanes: Erlang on, Erlang off, Java, C."""
    tests = [t for t in TESTS[which] if (ERLANG, deploy, "on", t[0]) in r]
    series = [("on", ERLANG, "on", ON, "o"), ("off", ERLANG, "off", OFF, "o")] + \
             [(key, server, "on", color, marker) for key, server, color, marker, *_ in REFERENCES]
    lay = figures.Rows(len(tests), 4, extra_in=0.17)
    fig, a = plt.subplots(figsize=(figures.WIDTH_IN[width], lay.height))
    y = list(range(len(tests)))[::-1]
    a.set_yticks(y, [t[1] for t in tests])
    out, dagger = [], False
    for yi, (load, name) in zip(y, tests):
        row = dict(test=load, label=name)
        cvs = []
        for lane, (key, server, bw, color, marker) in enumerate(series):
            g = r.get((server, deploy, bw, load))
            if not g:
                cvs.append("\u2013")
                continue
            by_cpu = coarse(g) and None not in g["cpu_j"]
            values = g["cpu_j"] if by_cpu else g["server_j"]
            m = data.median(values)
            dev = [(v / m - 1) * 100 for v in values] if m else [0.0] * len(values)
            row[f"deviation_{key}"], row[f"cv_{key}"], row[f"method_{key}"] = dev, data.cv(values), \
                "cpu-time" if by_cpu else "meter"
            for v, dy in zip(dev, lay.runs(len(dev))):
                figures.point(a, v, yi + lay.lane(lane) + dy, color, marker, alpha=0.85)
            cvs.append(f"{row[f'cv_{key}']:.0f}")
            row["dagger"] = row.get("dagger", False) or by_cpu
            dagger = dagger or by_cpu
        a.text(1.01, yi, " / ".join(cvs) + "%" + ("\u2020" if row.get("dagger") else ""),
               transform=a.get_yaxis_transform(), va="center", fontsize=6.5, color=MUTED)
        out.append(row)
    for yi in y[:-1]:
        a.axhline(yi - 0.5, color=GRID, lw=0.6, zorder=0)
    a.text(1.01, len(tests) - 0.45, figures.SPREAD_HEADER + "on / off / Java / C", transform=a.get_yaxis_transform(),
           fontsize=6.5, color=MUTED, va="bottom")
    a.axvline(0, color=MUTED, lw=0.6)
    lo, hi = a.get_xlim()
    a.set_xlim(lo, hi + 0.06 * (hi - lo))
    a.set_xlabel("Server energy of each run, from its median (%)")
    items = [figures.dot(ON, "Erlang, busy-waiting on"), figures.dot(OFF, "Erlang, off")]
    items += [figures.dot(color, text, marker) for _, _, color, marker, text, _ in REFERENCES]
    if dagger:
        items.append(plt.Line2D([], [], ls="", marker="$\u2020$", color=MUTED, markersize=figures.LEGEND,
                                label="CPU-time method (caption)"))
    figures.legend(fig, items, ncol=3, legend_rows=2)
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
