"""Step 5: the paper's figures, as images for the paper and as data (figures.json) for the web page.
Both come from the same numbers (busy_wait_http.py, stats.py), so the page and the paper always agree.

    ./srv/bin/python3 analysis/figures.py [--width one|two] [--format png|jpg|svg] [--dpi 300] [--out graphs/paper]
                                          results/<static folder> results/<dynamic folder>

--width one = one IEEE column (8.8 cm), two = the full page width (18.1 cm). One image per workload and deploy,
e.g. graphs/paper/fig1_static_container.png, and graphs/paper/figures.json.
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

import busy_wait_http  # noqa: E402
import data  # noqa: E402
import stats  # noqa: E402

WIDTH_IN = {"one": 3.46, "two": 7.13}            # IEEE column and page width (8.8 cm, 18.1 cm)
ON, OFF, JAVA, INK, MUTED, GRID = "#d95926", "#2a78d6", "#1b9e72", "#1f2328", "#5b636b", "#e3e6e8"
LANG = {"erlang": "Erlang", "elixir": "Elixir", "gleam": "Gleam", "java": "Java"}
KIND = {"pure": "pure", "index": "index", "cowboy": "Cowboy", "yaws": "Yaws", "phoenix": "Phoenix", "mist": "mist",
        "netty": "Netty"}
ORDER = ["pure", "index", "cowboy", "yaws", "phoenix", "mist", "netty"]


def label(server):
    """st-erlang-cowboy-29-1-1 -> 'Erlang Cowboy'."""
    _, lang, kind = server.split("-")[:3]
    return f"{LANG[lang]} {KIND[kind]}"


def sort_key(server):
    _, lang, kind = server.split("-")[:3]
    return list(LANG).index(lang), ORDER.index(kind)


def style():
    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["Nimbus Roman", "Liberation Serif", "DejaVu Serif"],
        "font.size": 7.5, "axes.titlesize": 7.5, "axes.labelsize": 7.5, "xtick.labelsize": 7, "ytick.labelsize": 7,
        "legend.fontsize": 7, "axes.edgecolor": MUTED, "axes.linewidth": 0.6, "xtick.color": MUTED,
        "ytick.color": INK, "xtick.major.width": 0.6, "ytick.major.size": 0, "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "x", "grid.color": GRID,
        "grid.linewidth": 0.6, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
    })


def workload(folder):
    """'static' or 'dynamic', from the rows' Type column."""
    return data.rows(folder)[0]["Type"]


def fig1_data(folder, deploy, load=80000):
    """Per BEAM server: server energy on/off (median, 95% CI of the ratio) and machine energy saved (95% CI)."""
    rng = random.Random(stats.SEED)
    out = []
    for (server, dep, lvl), g in busy_wait_http.table(folder).items():
        if dep != deploy or lvl != load:
            continue
        s_on, s_off, m_on, m_off = g["on"]["server_j"], g["off"]["server_j"], g["on"]["machine_j"], g["off"]["machine_j"]
        out.append(dict(
            server=server, label=label(server),
            server_on=data.median(s_on), server_off=data.median(s_off), runs_on=s_on, runs_off=s_off,
            ratio=stats.ratio(s_on, s_off), ratio_ci=stats.bootstrap(s_on, s_off, stats.ratio, rng),
            saved=stats.saved(m_on, m_off), saved_ci=stats.bootstrap(m_on, m_off, stats.saved, rng),
        ))
    return sorted(out, key=lambda r: sort_key(r["server"]))


def fig1(rows, title, width, path, dpi):
    """(a) server energy, busy-waiting on vs off; (b) whole-machine energy saved with it off, 95% CI."""
    n = len(rows)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], 0.19 * n + 0.75),
                               gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.08})
    y = list(range(n))[::-1]
    for yi, r in zip(y, rows):
        a.plot([r["server_off"], r["server_on"]], [yi, yi], color=GRID, lw=2.2, zorder=1, solid_capstyle="round")
        a.scatter(r["server_off"], yi, s=16, color=OFF, zorder=3, edgecolors="white", linewidths=0.6)
        a.scatter(r["server_on"], yi, s=16, color=ON, zorder=3, edgecolors="white", linewidths=0.6)
        a.text(r["server_on"] + 4, yi, f"{r['ratio']:.1f}×", va="center", fontsize=6.5, color=MUTED)
        lo, hi = r["saved_ci"]
        b.plot([lo, hi], [yi, yi], color=OFF, lw=0.9, alpha=0.55, zorder=2)
        b.scatter(r["saved"], yi, s=14, color=OFF, zorder=3, marker="s", edgecolors="white", linewidths=0.5)
    a.set_yticks(y, [r["label"] for r in rows])
    a.set_xlim(0, max(r["server_on"] for r in rows) * 1.18)
    a.set_xlabel("Server energy (J)")
    a.set_title("(a) Server, attributed", loc="left")
    b.axvline(0, color=MUTED, lw=0.6)
    b.set_xlabel("Saved with off (%)")
    b.set_title("(b) Machine, measured", loc="left")
    b.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(5))
    a.scatter([], [], s=16, color=ON, label="busy-waiting on (default)")
    a.scatter([], [], s=16, color=OFF, label="off (+sbwt none)")
    b.plot([], [], color=OFF, lw=0.9, alpha=0.55, marker="s", markersize=3.5, label="median, 95% CI")
    handles = a.get_legend_handles_labels()[0] + b.get_legend_handles_labels()[0]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3, frameon=False,
               handletextpad=0.2, columnspacing=1.0)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folders", nargs="+")
    p.add_argument("--width", choices=WIDTH_IN, default="one")
    p.add_argument("--format", choices=["png", "jpg", "svg"], default="png")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--out", default="graphs/paper")
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    style()
    figures = {"fig1": {}}
    for folder in args.folders:
        wl = workload(folder)
        for deploy in ("container", "native"):
            rows = fig1_data(folder, deploy)
            figures["fig1"][f"{wl}_{deploy}"] = rows
            name = {"container": "Docker", "native": "native"}[deploy]
            path = os.path.join(args.out, f"fig1_{wl}_{deploy}.{args.format}")
            fig1(rows, f"{wl.capitalize()} HTTP, 80,000 requests, {name}, median of 5 runs", args.width, path, args.dpi)
            print(path)
    with open(os.path.join(args.out, "figures.json"), "w") as fh:
        json.dump({"source": args.folders, "figures": figures}, fh, indent=1)
    print(os.path.join(args.out, "figures.json"))


if __name__ == "__main__":
    sys.exit(main())
