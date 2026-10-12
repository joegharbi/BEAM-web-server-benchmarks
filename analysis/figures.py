"""Step 5: the paper's figures, as images for the paper and as data (figures.json) for the web page.
Both come from the same numbers (busy_wait_http.py, stats.py), so the page and the paper always agree.

    ./srv/bin/python3 analysis/figures.py [--width one|two] [--format png|jpg|svg] [--dpi 300] [--out graphs/paper]
                                          results/<static folder> results/<dynamic folder>
                                          [results/<websocket folder> results/<mist re-run folder>]

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
        "bandit": "Bandit", "netty": "Netty"}
ORDER = ["pure", "index", "cowboy", "yaws", "phoenix", "bandit", "mist", "netty"]
WS_TESTS = (("stream 8 KB", "(a) 8 KB: server waits"), ("stream 1024 KB", "(b) 1 MB: server busy"))


COMPRESSED = "+compressed"      # a server's runs from before the client stopped offering compression (mist)


def label(server):
    """st-erlang-cowboy-29-1-1 -> 'Erlang Cowboy'."""
    _, lang, kind = server.split("-")[:3]
    return f"{LANG[lang]} {KIND[kind]}" + (" (compressed)" if server.endswith(COMPRESSED) else "")


def sort_key(server):
    _, lang, kind = server.split("-")[:3]
    return list(LANG).index(lang), ORDER.index(kind), server.endswith(COMPRESSED)


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
    legend(fig, a.get_legend_handles_labels()[0] + b.get_legend_handles_labels()[0])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def runs(folder, load=80000):
    """{(server, deploy, 'on'|'off'): {"server_j": [...], "machine_j": [...]}} for every server, Java included."""
    out = {}
    for r in data.rows(folder):
        if data.load(r) != load:
            continue
        g = out.setdefault((data.server(r), r["Deploy"], data.busy_wait(r)), {"server_j": [], "machine_j": []})
        g["server_j"].append(data.num(r, "Container Energy (J)"))
        g["machine_j"].append(data.num(r, "Host Energy (J)"))
    return out


def servers(r, java=True):
    names = sorted({k[0] for k in r if java or "-java-" not in k[0]}, key=sort_key)
    return [s for s in names if "-java-" not in s] + [s for s in names if "-java-" in s]


def rows_axis(ax, names):
    y = list(range(len(names)))[::-1]
    ax.set_yticks(y, [label(s) for s in names])
    n_beam = sum("-java-" not in s for s in names)
    if n_beam < len(names):                                        # a thin line between BEAM and Java
        ax.axhline(len(names) - n_beam - 0.5, color=GRID, lw=0.8)
    return dict(zip(names, y))


# The line on top of every figure that says what it shows (workload, Docker or native, load); set by main()
CONTEXT = {"text": ""}


def legend(fig, items, ncol=3, titles=True):
    """The context line, then one legend row, above the panels and close to their titles."""
    height = fig.get_size_inches()[1]
    context = 0.17 if CONTEXT["text"] else 0.0
    fig.subplots_adjust(top=1 - ((0.42 if titles else 0.2) + context) / height)
    if context:
        fig.text(0.5, 1.0, CONTEXT["text"], ha="center", va="top", fontsize=7.5, color=INK, fontweight="bold")
    fig.legend(handles=items, loc="upper center", bbox_to_anchor=(0.5, 1.0 - context / height), ncol=ncol,
               frameon=False, handletextpad=0.2, columnspacing=1.0)


def dot(color, text, marker="o", hollow=False):
    return plt.Line2D([], [], ls="", marker=marker, markersize=4, label=text, color=color,
                      markerfacecolor="white" if hollow else color)


def fig2(r, width, path, dpi):
    """Docker vs native: (a) what Docker adds to the machine energy, (b) the busy-wait ratio in both."""
    names = servers(r)
    med = lambda s, d, b, k: data.median(r[(s, d, b)][k])
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], 0.19 * len(names) + 0.75),
                               gridspec_kw={"width_ratios": [1, 1], "wspace": 0.08})
    y = rows_axis(a, names)
    out = []
    for s in names:
        java = "-java-" in s
        row = {"server": s, "label": label(s)}
        for bw, color in (("on", JAVA if java else ON), ("off", OFF)):
            if (s, "native", bw) not in r:
                continue
            extra = (med(s, "container", bw, "machine_j") / med(s, "native", bw, "machine_j") - 1) * 100
            row[f"docker_extra_{bw}"] = extra
            a.scatter(extra, y[s], s=16, color=color, zorder=3, edgecolors="white", linewidths=0.6)
        if not java:
            for d, hollow in (("container", False), ("native", True)):
                ratio = med(s, d, "on", "server_j") / med(s, d, "off", "server_j")
                row[f"ratio_{d}"] = ratio
                b.scatter(ratio, y[s] + (0.13 if d == "container" else -0.13), s=14, marker="D", zorder=3,
                          color=ON, facecolors="white" if hollow else ON, linewidths=0.8)
        out.append(row)
    a.axvline(0, color=MUTED, lw=0.6)
    a.set_xlabel("Docker vs native, machine (%)")
    a.set_title("(a) Docker's extra, machine", loc="left")
    b.set_xlim(0, None)
    b.set_xlabel("Busy-wait on/off, server (×)")
    b.set_title("(b) Busy-wait effect", loc="left")
    legend(fig, [dot(ON, "BEAM, on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java"),
                 dot(ON, "Docker", "D"), dot(ON, "native", "D", hollow=True)], ncol=5)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return out


def fig3(r, deploy, width, path, dpi):
    """The 5 runs of every BEAM server, as % from their own median: busy-waiting on scatters, off is tight."""
    names = servers(r, java=False)
    fig, a = plt.subplots(figsize=(WIDTH_IN[width], 0.19 * len(names) + 0.75))
    y = rows_axis(a, names)
    out = []
    for s in names:
        row = {"server": s, "label": label(s)}
        for bw, color, dy in (("on", ON, 0.13), ("off", OFF, -0.13)):
            values = r[(s, deploy, bw)]["server_j"]
            m = data.median(values)
            dev = [(v / m - 1) * 100 for v in values]
            row[f"deviation_{bw}"], row[f"cv_{bw}"] = dev, data.cv(values)
            a.scatter(dev, [y[s] + dy] * len(dev), s=9, color=color, alpha=0.85, zorder=3, edgecolors="none")
        a.text(1.01, y[s], f"{row['cv_on']:.0f}% / {row['cv_off']:.0f}%", transform=a.get_yaxis_transform(),
               va="center", fontsize=6.5, color=MUTED)
        out.append(row)
    a.text(1.01, len(names) - 0.35, "CV on / off", transform=a.get_yaxis_transform(), fontsize=6.5, color=MUTED)
    a.axvline(0, color=MUTED, lw=0.6)
    a.set_xlabel("Server energy of each run, from its median (%)")
    legend(fig, [dot(ON, "busy-waiting on, 5 runs"), dot(OFF, "off, 5 runs")], ncol=2, titles=False)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return out


def fig4(r, deploy, width, path, dpi):
    """Java as the reference: (a) server energy, attributed; (b) machine energy, measured."""
    names = servers(r)
    med = lambda s, b, k: data.median(r[(s, deploy, b)][k])
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], 0.19 * len(names) + 0.75),
                               gridspec_kw={"width_ratios": [1, 1], "wspace": 0.08})
    y = rows_axis(a, names)
    out = []
    for s in names:
        row = {"server": s, "label": label(s)}
        for ax, k in ((a, "server_j"), (b, "machine_j")):
            if "-java-" in s:
                v = med(s, "on", k)
                row[k] = v
                ax.scatter(v, y[s], s=16, color=JAVA, zorder=3, edgecolors="white", linewidths=0.6)
                continue
            on, off = med(s, "on", k), med(s, "off", k)
            row[k + "_on"], row[k + "_off"] = on, off
            ax.plot([off, on], [y[s], y[s]], color=GRID, lw=2.2, zorder=1, solid_capstyle="round")
            ax.scatter(off, y[s], s=16, color=OFF, zorder=3, edgecolors="white", linewidths=0.6)
            ax.scatter(on, y[s], s=16, color=ON, zorder=3, edgecolors="white", linewidths=0.6)
        out.append(row)
    a.set_xlim(0, None)
    a.set_xlabel("Server energy (J)")
    a.set_title("(a) Server, attributed", loc="left")
    b.set_xlabel("Machine energy (J)")
    b.set_title("(b) Machine, measured", loc="left")
    legend(fig, [dot(ON, "BEAM, busy-waiting on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java (no busy-waiting)")])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return out


def ws_runs(folders):
    """{(server, deploy, 'on'|'off', test): [server energy of each run]} of the WebSocket streams. A later folder
    replaces an earlier one's runs of the same server, and those earlier runs are kept as '<server>+compressed':
    the mist re-run without compression replaces the first run, in which only mist accepted compression."""
    out = {}
    for folder in folders:
        found = {}
        for r in data.rows(folder):
            found.setdefault((data.server(r), r["Deploy"], data.busy_wait(r), data.load(r)), []).append(
                data.num(r, "Container Energy (J)"))
        for key in found:
            if key in out:
                out[(key[0] + COMPRESSED,) + key[1:]] = out[key]
        out.update(found)
    return out


def fig5(r, deploy, width, path, dpi):
    """WebSocket streams: busy-waiting on vs off for small (8 KB) and large (1 MB) messages, server energy."""
    names = sorted({k[0] for k in r}, key=sort_key)
    names = [s for s in names if "-java-" not in s] + [s for s in names if "-java-" in s]
    fig, axes = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], 0.19 * len(names) + 0.95),
                             gridspec_kw={"wspace": 0.08})
    y = rows_axis(axes[0], names)
    out = []
    for s in names:
        row = {"server": s, "label": label(s)}
        for ax, (test, _) in zip(axes, WS_TESTS):
            if "-java-" in s:
                v = data.median(r[(s, deploy, "on", test)])
                row[test] = v
                ax.scatter(v, y[s], s=16, color=JAVA, zorder=3, edgecolors="white", linewidths=0.6)
                continue
            on, off = data.median(r[(s, deploy, "on", test)]), data.median(r[(s, deploy, "off", test)])
            row[test + " on"], row[test + " off"] = on, off
            ax.plot([off, on], [y[s], y[s]], color=GRID, lw=2.2, zorder=1, solid_capstyle="round")
            ax.scatter(off, y[s], s=16, color=OFF, zorder=3, edgecolors="white", linewidths=0.6)
            ax.scatter(on, y[s], s=16, color=ON, zorder=3, edgecolors="white", linewidths=0.6)
            ax.text(max(on, off) * 1.12, y[s], f"{on / off:.1f}\u00d7", va="center", fontsize=6.5, color=MUTED)
        out.append(row)
    for ax, (_, title) in zip(axes, WS_TESTS):
        ax.set_xscale("log")                      # equal steps = equal ratios, so 2x looks the same everywhere
        lo, hi = ax.get_xlim()
        ax.set_xlim(lo, hi * 1.6)
        ax.xaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=(1, 2, 5)))
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_xlabel("Server energy (J, log scale)")
        ax.set_title(title, loc="left")
    legend(fig, [dot(ON, "BEAM, busy-waiting on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java (no busy-waiting)")])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folders", nargs="+")
    p.add_argument("--width", choices=WIDTH_IN, default="one")
    p.add_argument("--format", choices=["png", "jpg", "svg"], default="png")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--out", default="graphs/paper")
    p.add_argument("--no-context", action="store_true",
                   help="leave out the line on top (workload, Docker or native, load) when the caption says it")
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    style()
    figures = {"fig1": {}, "fig2": {}, "fig3": {}, "fig4": {}, "fig5": {}}

    def out(name):
        path = os.path.join(args.out, f"{name}.{args.format}")
        print(path)
        return path
    websocket = [f for f in args.folders if data.is_websocket(data.rows(f)[0])]
    if websocket:
        r = ws_runs(websocket)
        for deploy in ("container", "native"):
            CONTEXT["text"] = "" if args.no_context else (
                f"WebSocket streams \u00b7 {'Docker (container)' if deploy == 'container' else 'native (no Docker)'} "
                "\u00b7 100 clients \u00b7 median of 5 runs")
            figures["fig5"][deploy] = fig5(r, deploy, args.width, out(f"fig5_websocket_{deploy}"), args.dpi)
    for folder in (f for f in args.folders if f not in websocket):
        wl, r = workload(folder), runs(folder)
        setting = lambda deploy: "" if args.no_context else \
            f"{wl.capitalize()} HTTP \u00b7 {deploy} \u00b7 80,000 requests \u00b7 median of 5 runs"
        CONTEXT["text"] = setting("Docker vs native")
        figures["fig2"][wl] = fig2(r, args.width, out(f"fig2_{wl}"), args.dpi)
        for deploy in ("container", "native"):
            CONTEXT["text"] = setting({"container": "Docker (container)", "native": "native (no Docker)"}[deploy])
            rows = fig1_data(folder, deploy)
            fig1(rows, "", args.width, out(f"fig1_{wl}_{deploy}"), args.dpi)
            figures["fig1"][f"{wl}_{deploy}"] = rows
            figures["fig3"][f"{wl}_{deploy}"] = fig3(r, deploy, args.width, out(f"fig3_{wl}_{deploy}"), args.dpi)
            figures["fig4"][f"{wl}_{deploy}"] = fig4(r, deploy, args.width, out(f"fig4_{wl}_{deploy}"), args.dpi)
    with open(os.path.join(args.out, "figures.json"), "w") as fh:
        json.dump({"source": args.folders, "load": 80000, "figures": figures}, fh, indent=1)
    print(os.path.join(args.out, "figures.json"))


if __name__ == "__main__":
    sys.exit(main())
