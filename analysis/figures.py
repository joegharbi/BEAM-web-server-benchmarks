"""Step 5: the paper's figures, as images for the paper and as data (figures.json) for the web page.
Both come from the same numbers (busy_wait_http.py, stats.py), so the page and the paper always agree.

    ./srv/bin/python3 analysis/figures.py [--width one|two] [--format png|jpg|svg] [--dpi 300] [--out graphs/paper]
                                          results/<static folder> results/<dynamic folder>
                                          [results/<websocket folder> results/<mist re-run folder>]

--width one = one IEEE column (8.8 cm), two = the full page width (18.1 cm). One image per workload and deploy,
e.g. graphs/paper/fig1_static_container.png, plus figures.json (the numbers) and FIGURES.md (what every image shows
and a draft caption). The setting is not drawn on the images (the paper's caption says it) unless --title is given;
PNG and SVG files also carry it inside (title and description fields).
"""
import argparse
import json
import math
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
# One drawing rule for every figure (figures.py and echo_figures.py):
MARK = 28                                  # area (pt^2) of every point, whatever it shows: a median, one run, a reference
AREA = {"o": 1.0, "s": 0.8, "^": 1.3, "D": 0.75}    # per shape, so a square or a triangle looks as heavy as a circle
LEGEND = 5.3                               # legend marks (diameter, pt): the same size as the points
LANE_IN = 0.13                             # height of one line of points (a lane), in inches, in every figure
ROW_GAP_IN = 0.07                          # space between two rows, in inches
FRAME_IN = 0.8                             # axis labels, titles and one legend row, in inches
SAVED_LABEL = "Laptop energy saved\nwith busy-waiting off (%)\n← off uses more | less →"
LANG = {"erlang": "Erlang", "elixir": "Elixir", "gleam": "Gleam", "java": "Java"}
KIND = {"pure": "pure", "index": "index", "cowboy": "Cowboy", "yaws": "Yaws", "phoenix": "Phoenix", "mist": "mist",
        "bandit": "Bandit", "netty": "Netty"}
ORDER = ["pure", "index", "cowboy", "yaws", "phoenix", "bandit", "mist", "netty"]
WS_TESTS = (("stream 8 KB", "(a) 8 KB: server waits"), ("stream 1024 KB", "(b) 1 MB: server busy"))


COMPRESSED = "+compressed"      # a server's runs from before the client stopped offering compression (mist)


def size(marker="o"):
    return MARK * AREA.get(marker, 1.0)


def point(ax, x, y, color, marker="o", hollow=False, alpha=1.0):
    """Every point of every figure: the same size per shape, a thin white edge (or a coloured one when hollow)."""
    return ax.scatter(x, y, s=size(marker), marker=marker, zorder=3, alpha=alpha, color=color,
                      facecolors="white" if hollow else color, edgecolors=color if hollow else "white",
                      linewidths=0.9 if hollow else 0.5)


class Rows:
    """The vertical layout of a figure with `n` rows of `lanes` lines of points each: the figure height and where each
    lane and each run sits, so one lane has the same height (LANE_IN) in every figure."""

    def __init__(self, n, lanes, extra_in=0.0):
        self.n, self.lanes = n, lanes
        row_in = lanes * LANE_IN + ROW_GAP_IN
        self.unit = LANE_IN / row_in                       # one lane, in row units (a row is 1 unit high)
        self.height = n * row_in + FRAME_IN + extra_in

    def lane(self, i, of=None):
        """Centre of lane i (0 = top) of a row drawn with `of` lanes (default: all)."""
        k = self.lanes if of is None else of
        return ((k - 1) / 2 - i) * self.unit

    def runs(self, n_runs):
        """Small vertical offsets of the runs within one lane, so equal values stay visible."""
        half = 0.3 * self.unit
        return [(-half + 2 * half * i / (n_runs - 1)) if n_runs > 1 else 0.0 for i in range(n_runs)]


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


def ratio_label(ax, x, y, text):
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=6.5, color=MUTED)


def saved_panel(b, yi, saved, ci):
    """Panel (b): the laptop energy saved with busy-waiting off (square) and its 95% range (line)."""
    b.plot(list(ci), [yi, yi], color=OFF, lw=0.9, alpha=0.55, zorder=2)
    point(b, saved, yi, OFF, "s")


def saved_axis(b):
    b.axvline(0, color=MUTED, lw=0.6)
    b.set_xlabel(SAVED_LABEL)
    b.set_title("(b) Platform, measured", loc="left")


def saved_legend():
    return plt.Line2D([], [], color=OFF, lw=0.9, alpha=0.55, marker="s", markersize=LEGEND * 0.9,
                      label="saved: median, 95% range")


def fig1(rows, title, width, path, dpi):
    """(a) server energy, busy-waiting on vs off; (b) platform energy (RAPL psys) saved with it off, 95% CI."""
    lay = Rows(len(rows), 1)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], lay.height),
                               gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.08})
    y = list(range(len(rows)))[::-1]
    for yi, r in zip(y, rows):
        pair(a, r["server_off"], r["server_on"], yi, lay)
        ratio_label(a, r["server_on"], yi, f"{r['ratio']:.1f}×")
        saved_panel(b, yi, r["saved"], r["saved_ci"])
    a.set_yticks(y, [r["label"] for r in rows])
    a.set_xlim(0, max(r["server_on"] for r in rows) * 1.18)
    a.set_xlabel("Server energy (J)")
    a.set_title("(a) Server, attributed", loc="left")
    saved_axis(b)
    b.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(5))
    legend(fig, [dot(ON, "busy-waiting on (default)"), dot(OFF, "off (+sbwt none)"), saved_legend()])
    save(fig, path, dpi)


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


# What the figure being drawn shows (workload, Docker or native, load) and its draft caption; set by main().
# The setting is drawn on top only with --title (the paper's caption says it); it is always stored in the file.
CONTEXT = {"text": "", "show": False, "caption": "", "sources": []}
DESCRIBED = []          # (file, setting, caption) of every image written, for FIGURES.md

CAPTIONS = {
    "fig1": "Busy-waiting on (default) vs off (+sbwt none) for 11 BEAM servers. (a) Energy attributed to the server "
            "process (CPU-time share); (b) platform energy (RAPL psys, measured) saved with busy-waiting off, with bootstrap "
            "95% confidence intervals.",
    "fig2": "Docker vs native deployment. (a) Additional platform energy (RAPL psys) in Docker relative to native, for BEAM "
            "(busy-waiting on and off) and Java; (b) busy-wait on/off ratio of the attributed server energy in Docker "
            "(filled) and native (hollow).",
    "fig3": "Run-to-run variation of the attributed server energy: the 5 runs of every server as deviation from that "
            "server's median, BEAM with busy-waiting on vs off, Java (no busy-waiting) as reference. Right: coefficient "
            "of variation.",
    "fig4": "Java reference servers (no busy-waiting) vs BEAM servers with busy-waiting on and off. (a) Attributed "
            "server energy (CPU-time share); (b) platform energy (RAPL psys, measured).",
    "fig5": "WebSocket streams (100 clients, 10 messages/s each, 5 s): attributed server energy with busy-waiting on "
            "vs off, log scale. (a) 8 KB messages; (b) 1 MB messages. Java Netty as reference. Gleam mist without "
            "per-message compression (re-run) and with it (first run; the only server that accepted the client's offer).",
    "fig6": "WebSocket streams: run-to-run variation of the attributed server energy, the 5 runs of every server as "
            "deviation from its median, busy-waiting on vs off, Java Netty as reference. (a) 8 KB; (b) 1 MB messages.",
}


def about(fig_id, setting):
    """Set what the next figure shows: its setting line and its draft caption."""
    CONTEXT["text"], CONTEXT["caption"] = setting, CAPTIONS[fig_id]


def save(fig, path, dpi):
    """Write the image with its description inside (PNG and SVG text fields) and note it for FIGURES.md."""
    description = f"{CONTEXT['text']}. {CONTEXT['caption']} Data: {', '.join(CONTEXT['sources'])}."
    fmt = os.path.splitext(path)[1][1:]
    meta = {"png": {"Title": CONTEXT["text"], "Description": description, "Software": "WSEB analysis/figures.py"},
            "svg": {"Title": CONTEXT["text"], "Description": description}}.get(fmt)
    settle_pairs(fig)
    fig.savefig(path, dpi=dpi, metadata=meta)
    plt.close(fig)
    DESCRIBED.append((os.path.basename(path), CONTEXT["text"], CONTEXT["caption"]))


PAIRS = []          # pairs drawn but not yet checked for overlap (done in save(), once the axes are final)
OVERLAP = 0.03      # two points closer than this share of the axis width would cover each other


def pair(ax, off, on, yi, lay):
    """Busy-waiting off (blue) and on (orange) on one lane, joined by a grey bar. Where the two would cover each
    other on the drawn axis (no real effect), save() moves them slightly above and below each other instead."""
    bar = ax.plot([off, on], [yi, yi], color=GRID, lw=2.2, zorder=1, solid_capstyle="round")[0]
    PAIRS.append((ax, off, on, yi, lay.unit, bar, point(ax, off, yi, OFF), point(ax, on, yi, ON)))


def settle_pairs(fig):
    """Separate the pairs of this figure whose two points would overlap, judged on the final axis (log or linear)."""
    keep = []
    for ax, off, on, yi, unit, bar, p_off, p_on in PAIRS:
        if ax.figure is not fig:
            keep.append((ax, off, on, yi, unit, bar, p_off, p_on))
            continue
        lo, hi = ax.get_xlim()
        f = (lambda v: math.log10(v)) if ax.get_xscale() == "log" else (lambda v: v)
        if abs(f(on) - f(off)) < OVERLAP * abs(f(hi) - f(lo)):
            bar.remove()
            p_off.set_offsets([[off, yi - 0.35 * unit]])
            p_on.set_offsets([[on, yi + 0.35 * unit]])
    PAIRS[:] = keep


def legend(fig, items, ncol=3, titles=True, legend_rows=1):
    """The context line, then the legend, above the panels and close to their titles."""
    height = fig.get_size_inches()[1]
    context = 0.17 if CONTEXT["show"] and CONTEXT["text"] else 0.0
    fig.subplots_adjust(top=1 - ((0.42 if titles else 0.2) + 0.17 * (legend_rows - 1) + context) / height)
    if context:
        fig.text(0.5, 1.0, CONTEXT["text"], ha="center", va="top", fontsize=7.5, color=INK, fontweight="bold")
    fig.legend(handles=items, loc="upper center", bbox_to_anchor=(0.5, 1.0 - context / height), ncol=ncol,
               frameon=False, handletextpad=0.2, columnspacing=1.0)


def dot(color, text, marker="o", hollow=False):
    return plt.Line2D([], [], ls="", marker=marker, markersize=LEGEND * (AREA.get(marker, 1.0) ** 0.5), label=text,
                      color=color, markerfacecolor="white" if hollow else color)


def fig2(r, width, path, dpi):
    """Docker vs native: (a) what Docker adds to the machine energy, (b) the busy-wait ratio in both."""
    names = servers(r)
    med = lambda s, d, b, k: data.median(r[(s, d, b)][k])
    lay = Rows(len(names), 2)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], lay.height),
                               gridspec_kw={"width_ratios": [1, 1], "wspace": 0.08})
    y = rows_axis(a, names)
    out = []
    for s in names:
        java = "-java-" in s
        row = {"server": s, "label": label(s)}
        for i, (bw, color) in enumerate((("on", JAVA if java else ON), ("off", OFF))):
            if (s, "native", bw) not in r:
                continue
            extra = (med(s, "container", bw, "machine_j") / med(s, "native", bw, "machine_j") - 1) * 100
            row[f"docker_extra_{bw}"] = extra
            point(a, extra, y[s] + (lay.lane(0, of=1) if java else lay.lane(i)), color)
        if not java:
            for i, (d, hollow) in enumerate((("container", False), ("native", True))):
                ratio = med(s, d, "on", "server_j") / med(s, d, "off", "server_j")
                row[f"ratio_{d}"] = ratio
                point(b, ratio, y[s] + lay.lane(i), ON, "D", hollow=hollow)
        out.append(row)
    a.axvline(0, color=MUTED, lw=0.6)
    a.set_xlabel("Docker vs native, platform (%)")
    a.set_title("(a) Docker's extra, platform", loc="left")
    b.set_xlim(0, None)
    b.set_xlabel("Busy-wait on/off, server (×)")
    b.set_title("(b) Busy-wait effect", loc="left")
    legend(fig, [dot(ON, "BEAM, on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java"),
                 dot(ON, "Docker", "D"), dot(ON, "native", "D", hollow=True)], ncol=5)
    save(fig, path, dpi)
    return out


def spread_rows(ax, r_runs, names, lay, cv_header):
    """The 5 runs of every server as % from its own median: BEAM with busy-waiting on (top lane) and off (bottom
    lane), Java (one lane, the reference without busy-waiting); the spread (CV) of each on the right."""
    y = rows_axis(ax, names)
    out = []
    for s in names:
        row = {"server": s, "label": label(s)}
        series = [("java", "on", JAVA, 0, 1)] if "-java-" in s else [("on", "on", ON, 0, 2), ("off", "off", OFF, 1, 2)]
        cvs = []
        for key, bw, color, lane, of in series:
            values = r_runs(s, bw)
            m = data.median(values)
            dev = [(v / m - 1) * 100 for v in values]
            row[f"deviation_{key}"], row[f"cv_{key}"] = dev, data.cv(values)
            base = y[s] + lay.lane(lane, of=of)
            for v, dy in zip(dev, lay.runs(len(dev))):
                point(ax, v, base + dy, color, alpha=0.85)
            cvs.append(f"{row[f'cv_{key}']:.0f}%")
        ax.text(1.01, y[s], " / ".join(cvs), transform=ax.get_yaxis_transform(), va="center", fontsize=6.5, color=MUTED)
        out.append(row)
    ax.text(1.01, len(names) - 0.45, cv_header, transform=ax.get_yaxis_transform(), fontsize=6.5, color=MUTED,
            va="bottom")
    ax.axvline(0, color=MUTED, lw=0.6)
    return out


def fig3(r, deploy, width, path, dpi):
    """The 5 runs of every server, as % from their own median: busy-waiting on scatters, off and Java are tight."""
    names = servers(r)
    lay = Rows(len(names), 2)
    fig, a = plt.subplots(figsize=(WIDTH_IN[width], lay.height))
    out = spread_rows(a, lambda s, bw: r[(s, deploy, bw)]["server_j"], names, lay, "Spread (CV)\non / off; Java")
    a.set_xlabel("Server energy of each run, from its median (%)")
    legend(fig, [dot(ON, "BEAM, busy-waiting on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java (no busy-waiting)")],
           titles=False)
    save(fig, path, dpi)
    return out


def fig4(r, deploy, width, path, dpi):
    """Java as the reference: (a) server energy, attributed; (b) machine energy, measured."""
    names = servers(r)
    med = lambda s, b, k: data.median(r[(s, deploy, b)][k])
    lay = Rows(len(names), 1)
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], lay.height),
                               gridspec_kw={"width_ratios": [1, 1], "wspace": 0.08})
    y = rows_axis(a, names)
    out = []
    for s in names:
        row = {"server": s, "label": label(s)}
        for ax, k in ((a, "server_j"), (b, "machine_j")):
            if "-java-" in s:
                v = med(s, "on", k)
                row[k] = v
                point(ax, v, y[s], JAVA)
                continue
            on, off = med(s, "on", k), med(s, "off", k)
            row[k + "_on"], row[k + "_off"] = on, off
            pair(ax, off, on, y[s], lay)
        out.append(row)
    a.set_xlim(0, None)
    a.set_xlabel("Server energy (J)")
    a.set_title("(a) Server, attributed", loc="left")
    b.set_xlabel("Platform energy (J, axis starts above 0)")
    b.set_title("(b) Platform, measured", loc="left")
    legend(fig, [dot(ON, "BEAM, busy-waiting on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java (no busy-waiting)")])
    save(fig, path, dpi)
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


def log_axis(ax, label_text):
    ax.set_xscale("log")                      # equal steps = equal ratios, so 2x looks the same everywhere
    lo, hi = ax.get_xlim()
    ax.set_xlim(lo, hi * 1.6)
    ax.xaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=(1, 2, 5)))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel(label_text)


def ws_names(r):
    names = sorted({k[0] for k in r}, key=sort_key)
    return [s for s in names if "-java-" not in s] + [s for s in names if "-java-" in s]


def fig5(r, deploy, width, path, dpi):
    """WebSocket streams: busy-waiting on vs off for small (8 KB) and large (1 MB) messages, server energy."""
    names = ws_names(r)
    lay = Rows(len(names), 1, extra_in=0.2)
    fig, axes = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], lay.height), gridspec_kw={"wspace": 0.08})
    y = rows_axis(axes[0], names)
    out = []
    for s in names:
        row = {"server": s, "label": label(s)}
        for ax, (test, _) in zip(axes, WS_TESTS):
            if "-java-" in s:
                v = data.median(r[(s, deploy, "on", test)])
                row[test] = v
                point(ax, v, y[s], JAVA)
                continue
            on, off = data.median(r[(s, deploy, "on", test)]), data.median(r[(s, deploy, "off", test)])
            row[test + " on"], row[test + " off"] = on, off
            pair(ax, off, on, y[s], lay)
            ratio_label(ax, max(on, off), y[s], f"{on / off:.1f}×")
        out.append(row)
    for ax, (_, title) in zip(axes, WS_TESTS):
        log_axis(ax, "Server energy (J, log scale)")
        ax.set_title(title, loc="left")
    legend(fig, [dot(ON, "BEAM, busy-waiting on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java (no busy-waiting)")])
    save(fig, path, dpi)
    return out


def fig6(r, deploy, width, path, dpi):
    """WebSocket streams: the 5 runs of every server as % from its median, 8 KB and 1 MB, Java Netty as reference."""
    names = ws_names(r)
    lay = Rows(len(names), 2, extra_in=0.2)
    fig, axes = plt.subplots(1, 2, sharey=True, figsize=(WIDTH_IN[width], lay.height), gridspec_kw={"wspace": 0.35})
    out = {}
    for ax, (test, title) in zip(axes, WS_TESTS):
        out[test] = spread_rows(ax, lambda s, bw: r[(s, deploy, bw, test)], names, lay, "CV\non / off")
        ax.set_title(title, loc="left")
        ax.set_xlabel("Each run, from its median (%)")
    legend(fig, [dot(ON, "BEAM, busy-waiting on"), dot(OFF, "BEAM, off"), dot(JAVA, "Java (no busy-waiting)")])
    save(fig, path, dpi)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folders", nargs="+")
    p.add_argument("--width", choices=WIDTH_IN, default="one")
    p.add_argument("--format", choices=["png", "jpg", "svg"], default="png")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--out", default="graphs/paper")
    p.add_argument("--title", action="store_true",
                   help="draw the setting (workload, Docker or native, load) on top of every figure; without it (the "
                        "default, for the paper) the caption says it, and it is stored in the file and in FIGURES.md")
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    style()
    CONTEXT["show"], CONTEXT["sources"] = args.title, args.folders
    figures = {"fig1": {}, "fig2": {}, "fig3": {}, "fig4": {}, "fig5": {}, "fig6": {}}

    def out(name):
        path = os.path.join(args.out, f"{name}.{args.format}")
        print(path)
        return path
    websocket = [f for f in args.folders if data.is_websocket(data.rows(f)[0])]
    if websocket:
        r = ws_runs(websocket)
        for deploy in ("container", "native"):
            about("fig5", f"WebSocket streams \u00b7 {'Docker (container)' if deploy == 'container' else 'native (no Docker)'} "
                          "\u00b7 100 clients \u00b7 median of 5 runs")
            figures["fig5"][deploy] = fig5(r, deploy, args.width, out(f"fig5_websocket_{deploy}"), args.dpi)
            about("fig6", f"WebSocket streams \u00b7 {'Docker (container)' if deploy == 'container' else 'native (no Docker)'} "
                          "\u00b7 100 clients \u00b7 5 runs")
            figures["fig6"][deploy] = fig6(r, deploy, args.width, out(f"fig6_websocket_spread_{deploy}"), args.dpi)
    for folder in (f for f in args.folders if f not in websocket):
        wl, r = workload(folder), runs(folder)
        setting = lambda deploy: f"{wl.capitalize()} HTTP \u00b7 {deploy} \u00b7 80,000 requests \u00b7 median of 5 runs"
        about("fig2", setting("Docker vs native"))
        figures["fig2"][wl] = fig2(r, args.width, out(f"fig2_{wl}"), args.dpi)
        for deploy in ("container", "native"):
            where = setting({"container": "Docker (container)", "native": "native (no Docker)"}[deploy])
            rows = fig1_data(folder, deploy)
            about("fig1", where)
            fig1(rows, "", args.width, out(f"fig1_{wl}_{deploy}"), args.dpi)
            figures["fig1"][f"{wl}_{deploy}"] = rows
            about("fig3", where)
            figures["fig3"][f"{wl}_{deploy}"] = fig3(r, deploy, args.width, out(f"fig3_{wl}_{deploy}"), args.dpi)
            about("fig4", where)
            figures["fig4"][f"{wl}_{deploy}"] = fig4(r, deploy, args.width, out(f"fig4_{wl}_{deploy}"), args.dpi)
    with open(os.path.join(args.out, "figures.json"), "w") as fh:
        json.dump({"source": args.folders, "load": 80000, "figures": figures}, fh, indent=1)
    print(os.path.join(args.out, "figures.json"))
    with open(os.path.join(args.out, "FIGURES.md"), "w") as fh:
        fh.write(f"# Figures in this folder\n\nMade by `analysis/figures.py` ({args.width} column, {args.dpi} dpi"
                 f"{', setting drawn on top' if args.title else ''}) from: {', '.join(args.folders)}\n\n")
        for name, where, caption in sorted(DESCRIBED):
            fh.write(f"## {name}\n\n- **Shows:** {where}\n- **Draft caption:** {caption}\n\n")
    print(os.path.join(args.out, "FIGURES.md"))


if __name__ == "__main__":
    sys.exit(main())
