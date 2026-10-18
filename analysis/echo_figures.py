"""Step 6: the echo figures (TCP echo servers in C, Java and Erlang, the MIPRO 2024 echo paper), in the style of
figures.py, as images and as data (echo_figures.json).

    ./srv/bin/python3 analysis/echo_figures.py --new results/<echo-new> [--rerun results/<re-run>]
                                               --old results/<echo-old> [--width one|two] [--out graphs/paper/echo]

--rerun replaces the runs of --new it measured again (same server, busy-waiting, deploy and test): the Docker
nosleep re-run with ENV_TCP_TW_REUSE=1. Images (one per deploy where it matters):
  echo1_<new|old>_<deploy>  busy-waiting on vs off for Erlang, per test: (a) server energy, (b) platform saved
  echo2_new_<deploy>        all servers per test: (a) server energy, (b) platform energy
  echo3_old_vs_new          the tests both campaigns have: the old campaign's medians against the new one's
A test whose runs mostly had the meter behind (tools/progress.py meter_behind) is drawn hollow: its server energy
is coarse (C in nosleep: one thread per connection, up to 10,000, stalled Scaphandre).
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

ON, OFF, JAVA, C, INK, MUTED, GRID = figures.ON, figures.OFF, figures.JAVA, "#9c3d8f", figures.INK, figures.MUTED, \
    figures.GRID
ERLANG, JAVA_S, C_S = "ec-erlang-pure-29-1-1", "ec-java-pure-27", "ec-c-pure-14"
# (series, server, busy-waiting, colour, marker, label); the order is the drawing order within a test row
SERIES = [("erlang_on", ERLANG, "on", ON, "o", "Erlang, busy-waiting on"),
          ("erlang_off", ERLANG, "off", OFF, "o", "Erlang, off"),
          ("java", JAVA_S, "on", JAVA, "^", "Java"),
          ("c", C_S, "on", C, "s", "C")]
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
SHARED = ("connect 100 clients spacing 0.1 s", "connect 1000 clients spacing 0.1 s")
WHERE = {"container": "Docker (container)", "native": "native (no Docker)"}
CAPTIONS = {
    "echo1": "TCP echo server in Erlang, busy-waiting on (default) vs off (+sbwt none), per test. (a) Energy attributed "
             "to the server process (CPU-time share), log scale; (b) platform energy (RAPL psys, measured) saved with "
             "busy-waiting off, with bootstrap 95% confidence intervals. Tests ordered from mostly waiting (clients "
             "0.1 s apart) to always busy (all clients at once).",
    "echo2": "TCP echo servers in C (an OS thread per connection), Java (a virtual thread per connection) and Erlang (a "
             "process per connection, busy-waiting on and off), per test. (a) Attributed server energy, log scale; "
             "(b) platform energy (RAPL psys). Hollow: the meter fell behind in most runs (fewer than half its "
             "readings), so the attributed energy is coarse.",
    "echo3": "The tests both echo campaigns contain (clients 0.1 s apart, 100 and 1,000 clients): median of the old "
             "setup against the new setup, for every server, busy-waiting setting and deployment. On the line: the "
             "same result in both campaigns. (a) Attributed server energy; (b) platform energy.",
}


def runs(folders):
    """{(server, deploy, 'on'|'off', load): {"server_j": [...], "machine_j": [...], "behind": n}}; a later folder
    replaces the runs of an earlier one with the same key (the re-run of a test)."""
    out = {}
    for folder in folders:
        found = {}
        for r in data.rows(folder):
            g = found.setdefault((data.server(r), r["Deploy"], data.busy_wait(r), data.load(r)),
                                 {"server_j": [], "machine_j": [], "behind": 0})
            g["server_j"].append(data.num(r, "Container Energy (J)"))
            g["machine_j"].append(data.num(r, "Host Energy (J)"))
            g["behind"] += progress.meter_behind(r)
        out.update(found)
    return out


def coarse(g):
    return g["behind"] * 2 > len(g["server_j"])


def about(fig_id, setting):
    figures.CONTEXT["text"], figures.CONTEXT["caption"] = setting, CAPTIONS[fig_id]


def log_axis(ax, label, subs=(1,)):
    ax.set_xscale("log")                       # equal steps = equal ratios
    ax.xaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=subs))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel(label)


def tests_axis(ax, tests):
    y = list(range(len(tests)))[::-1]
    ax.set_yticks(y, [t[1] for t in tests])
    return dict(zip([t[0] for t in tests], y))


def echo1(r, which, deploy, width, path, dpi):
    """Erlang on vs off per test: (a) server energy with the ratio, (b) platform energy saved with off, 95% CI."""
    tests = [t for t in TESTS[which] if (ERLANG, deploy, "on", t[0]) in r]
    rng = random.Random(stats.SEED)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(figures.WIDTH_IN[width], 0.22 * len(tests) + 0.8),
                               gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.08})
    y = tests_axis(a, tests)
    out = []
    for load, name in tests:
        on, off = r[(ERLANG, deploy, "on", load)], r[(ERLANG, deploy, "off", load)]
        s_on, s_off = data.median(on["server_j"]), data.median(off["server_j"])
        row = dict(test=load, label=name, server_on=s_on, server_off=s_off,
                   ratio=stats.ratio(on["server_j"], off["server_j"]),
                   ratio_ci=stats.bootstrap(on["server_j"], off["server_j"], stats.ratio, rng),
                   saved=stats.saved(on["machine_j"], off["machine_j"]),
                   saved_ci=stats.bootstrap(on["machine_j"], off["machine_j"], stats.saved, rng))
        a.plot([s_off, s_on], [y[load]] * 2, color=GRID, lw=2.2, zorder=1, solid_capstyle="round")
        for v, color in ((s_off, OFF), (s_on, ON)):
            a.scatter(v, y[load], s=16, color=color, zorder=3, edgecolors="white", linewidths=0.6)
        a.text(s_on * 1.25, y[load], f"{row['ratio']:.1f}×", va="center", fontsize=6.5, color=MUTED)
        lo, hi = row["saved_ci"]
        b.plot([lo, hi], [y[load]] * 2, color=OFF, lw=0.9, alpha=0.55, zorder=2)
        b.scatter(row["saved"], y[load], s=14, color=OFF, zorder=3, marker="s", edgecolors="white", linewidths=0.5)
        out.append(row)
    log_axis(a, "Server energy (J, log scale)")
    lo, hi = a.get_xlim()
    a.set_xlim(lo, hi * 2.2)
    a.set_title("(a) Server, attributed", loc="left")
    b.axvline(0, color=MUTED, lw=0.6)
    b.set_xlabel("Saved with off (%)")
    b.set_title("(b) Platform, measured", loc="left")
    b.plot([], [], color=OFF, lw=0.9, alpha=0.55, marker="s", markersize=3.5, label="median, 95% CI")
    figures.legend(fig, [figures.dot(ON, "busy-waiting on (default)"), figures.dot(OFF, "off (+sbwt none)")]
                   + b.get_legend_handles_labels()[0])
    figures.save(fig, path, dpi)
    return out


def echo2(r, deploy, width, path, dpi):
    """Every server per test: (a) server energy, (b) platform energy; hollow where the meter fell behind."""
    tests = TESTS["new"]
    fig, axes = plt.subplots(1, 2, sharey=True, figsize=(figures.WIDTH_IN[width], 0.36 * len(tests) + 0.8),
                             gridspec_kw={"wspace": 0.08})
    y = tests_axis(axes[0], tests)
    dy = {s[0]: d for s, d in zip(SERIES, (0.24, 0.08, -0.08, -0.24))}
    out = []
    for load, name in tests:
        row = dict(test=load, label=name)
        for key, server, bw, color, marker, _ in SERIES:
            g = r.get((server, deploy, bw, load))
            if not g:
                continue
            hollow = coarse(g)
            row[key] = dict(server_j=data.median(g["server_j"]), machine_j=data.median(g["machine_j"]),
                            meter_behind_runs=g["behind"], runs=len(g["server_j"]))
            for ax, k in zip(axes, ("server_j", "machine_j")):
                ax.scatter(row[key][k], y[load] + dy[key], s=15, marker=marker, zorder=3, linewidths=0.8,
                           color=color, facecolors="white" if hollow and k == "server_j" else color,
                           edgecolors=color if hollow and k == "server_j" else "white")
        out.append(row)
    for yi in list(y.values())[1:]:
        for ax in axes:
            ax.axhline(yi + 0.5, color=GRID, lw=0.6, zorder=0)
    log_axis(axes[0], "Server energy (J, log scale)")
    axes[0].set_title("(a) Server, attributed", loc="left")
    log_axis(axes[1], "Platform energy (J, log scale)", subs=(1, 2, 5))
    axes[1].set_title("(b) Platform, measured", loc="left")
    items = [figures.dot(color, label, marker) for _, _, _, color, marker, label in SERIES]
    items.append(figures.dot(MUTED, "meter behind (coarse)", "s", hollow=True))
    figures.legend(fig, items, ncol=5)
    figures.save(fig, path, dpi)
    return out


def echo3(old, new, width, path, dpi):
    """Old against new campaign for the tests both have: each point one server, busy-waiting and deploy."""
    fig, axes = plt.subplots(1, 2, figsize=(figures.WIDTH_IN[width], figures.WIDTH_IN[width] * 0.5 + 0.45),
                             gridspec_kw={"wspace": 0.32})
    out, diffs = [], {"server_j": [], "machine_j": []}
    for load in SHARED:
        for key, server, bw, color, marker, label in SERIES:
            for deploy in ("container", "native"):
                k = (server, deploy, bw, load)
                if k not in old or k not in new:
                    continue
                row = dict(test=load, series=key, deploy=deploy)
                for ax, m in zip(axes, ("server_j", "machine_j")):
                    a_v, b_v = data.median(old[k][m]), data.median(new[k][m])
                    row[m] = dict(old=a_v, new=b_v, diff_pct=(b_v / a_v - 1) * 100)
                    diffs[m].append(abs(row[m]["diff_pct"]))
                    ax.scatter(a_v, b_v, s=14, marker=marker, color=color, zorder=3, linewidths=0.8,
                               facecolors="white" if deploy == "native" else color,
                               edgecolors=color if deploy == "native" else "white")
                out.append(row)
    for ax, m, title in zip(axes, ("server_j", "machine_j"), ("(a) Server, attributed", "(b) Platform, measured")):
        ax.set_xscale("log")
        ax.set_yscale("log")
        lo = min(ax.get_xlim()[0], ax.get_ylim()[0])
        hi = max(ax.get_xlim()[1], ax.get_ylim()[1])
        ax.plot([lo, hi], [lo, hi], color=MUTED, lw=0.6, zorder=1)
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        for axis in (ax.xaxis, ax.yaxis):
            axis.set_major_locator(matplotlib.ticker.LogLocator(subs=(1,) if m == "server_j" else (1, 2, 5)))
            axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
            axis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.grid(True, which="major", axis="both")
        ax.set_xlabel("Old setup (J, log scale)")
        ax.set_ylabel("New setup (J, log scale)")
        ax.set_title(title, loc="left")
        med = sorted(diffs[m])[len(diffs[m]) // 2]
        ax.text(0.97, 0.04, f"median |difference| {med:.0f}%\nlargest {max(diffs[m]):.0f}%", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=6.5, color=MUTED)
    short = {"erlang_on": "Erlang on", "erlang_off": "Erlang off", "java": "Java", "c": "C"}
    items = [figures.dot(color, short[key], marker) for key, _, _, color, marker, _ in SERIES]
    items += [figures.dot(MUTED, "Docker", "o"), figures.dot(MUTED, "native", "o", hollow=True)]
    figures.legend(fig, items, ncol=6)
    figures.save(fig, path, dpi)
    return out, {m: dict(median_abs_diff_pct=sorted(v)[len(v) // 2], max_abs_diff_pct=max(v)) for m, v in diffs.items()}


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
    new, old = runs(new_folders), runs([args.old])

    def out(name):
        path = os.path.join(args.out, f"{name}.{args.format}")
        print(path)
        return path
    result = {"echo1": {}, "echo2": {}}
    for which, r in (("new", new), ("old", old)):
        for deploy in ("container", "native"):
            about("echo1", f"TCP echo, {which} setup · {WHERE[deploy]} · Erlang · median of 5 runs")
            result["echo1"][f"{which}_{deploy}"] = echo1(r, which, deploy, args.width, out(f"echo1_{which}_{deploy}"),
                                                         args.dpi)
    for deploy in ("container", "native"):
        about("echo2", f"TCP echo, new setup · {WHERE[deploy]} · C, Java, Erlang · median of 5 runs")
        result["echo2"][deploy] = echo2(new, deploy, args.width, out(f"echo2_new_{deploy}"), args.dpi)
    about("echo3", "TCP echo · old vs new setup · shared tests · median of 5 runs each")
    result["echo3"], result["echo3_agreement"] = echo3(old, new, args.width, out("echo3_old_vs_new"), args.dpi)
    with open(os.path.join(args.out, "echo_figures.json"), "w") as fh:
        json.dump({"source": {"new": new_folders, "old": args.old}, "figures": result}, fh, indent=1)
    print(os.path.join(args.out, "echo_figures.json"))
    with open(os.path.join(args.out, "FIGURES.md"), "w") as fh:
        fh.write(f"# Echo figures in this folder\n\nMade by `analysis/echo_figures.py` ({args.width} column, "
                 f"{args.dpi} dpi) from: new {', '.join(new_folders)}; old {args.old}\n\n")
        for name, where, caption in sorted(figures.DESCRIBED):
            fh.write(f"## {name}\n\n- **Shows:** {where}\n- **Draft caption:** {caption}\n\n")
    print(os.path.join(args.out, "FIGURES.md"))


if __name__ == "__main__":
    sys.exit(main())
