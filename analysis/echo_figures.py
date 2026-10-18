"""Step 6: the echo figure (TCP echo servers in C, Java and Erlang, the MIPRO 2024 echo paper), in the style of
figures.py, as images and as data (echo_figures.json). One figure per setup: Erlang with busy-waiting on vs off
per test, with Java and C as references.

    ./srv/bin/python3 analysis/echo_figures.py --new results/<echo-new> [--rerun results/<re-run>]
                                               --old results/<echo-old> [--width one|two] [--out graphs/paper/echo]

--rerun replaces the runs of --new it measured again (same server, busy-waiting, deploy and test): the Docker
nosleep re-run with ENV_TCP_TW_REUSE=1. Images: echo_<new|old>_<container|native> with
  (a) server energy (attributed) per test: Erlang off -> on with the ratio; Java and C as reference marks
  (b) platform energy (measured) saved with busy-waiting off, with its 95% range
A reference whose runs mostly had the meter behind (tools/progress.py meter_behind) is drawn hollow: its server
energy is coarse (C in nosleep: one thread per connection, up to 10,000, stalled Scaphandre).
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
           "Hollow: the meter fell behind in most runs, so that value is coarse.")


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
            hollow = coarse(g)
            any_coarse |= hollow
            row[key] = dict(server_j=data.median(g["server_j"]), platform_j=data.median(g["machine_j"]), coarse=hollow)
            a.scatter(row[key]["server_j"], yi + dy, s=13, marker=marker, zorder=3, linewidths=0.8, color=color,
                      facecolors="white" if hollow else color, edgecolors=color if hollow else "white")
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
        items.append(figures.dot(MUTED, "hollow: meter behind", "s", hollow=True))
    figures.legend(fig, items, ncol=3)
    fig.subplots_adjust(top=1 - (0.42 + 0.17) / fig.get_size_inches()[1])   # room for the legend's second row
    figures.save(fig, path, dpi)
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
    figures.CONTEXT["caption"] = CAPTION
    result = {}
    for which, r in (("new", runs(new_folders)), ("old", runs([args.old]))):
        for deploy in ("container", "native"):
            figures.CONTEXT["text"] = f"TCP echo, {SETUP[which]} · {WHERE[deploy]} · median of 5 runs"
            path = os.path.join(args.out, f"echo_{which}_{deploy}.{args.format}")
            print(path)
            result[f"{which}_{deploy}"] = echo(r, which, deploy, args.width, path, args.dpi)
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
