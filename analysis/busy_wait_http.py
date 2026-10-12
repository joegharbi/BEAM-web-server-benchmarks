"""Step 2: BEAM busy-waiting on vs off, HTTP. Each server is compared with itself (as built vs -nobw),
same deploy and load, median of the 5 repeats.

    ./srv/bin/python3 analysis/busy_wait_http.py results/<static folder> [results/<dynamic folder>]
"""
import collections
import sys

import data

# What is compared, per server, deploy and load
MEASURES = {
    "server_j": "Container Energy (J)",          # energy of the server's processes
    "machine_j": "Host Energy (J)",              # energy of the whole laptop (also the load client)
    # CPU time of the server (%*s). Only the on/off RATIO is used: the total assumes one sample every 0.5 s,
    # but the real sample gap differed (docker stats is slow), so absolute values are off in results up to v1.6.1.
    "cpu_s": "Container Total CPU (%*s)",
    "rate": "Requests/s",
    "power_w": "Container Avg Power (W)",
}


def table(folder):
    """{(server, deploy, load): {"on": {measure: [5 values]}, "off": {...}}}, BEAM servers only."""
    out = collections.defaultdict(lambda: {"on": collections.defaultdict(list), "off": collections.defaultdict(list)})
    for r in data.rows(folder):
        key = (data.server(r), r["Deploy"], data.load(r))
        for name, column in MEASURES.items():
            out[key][data.busy_wait(r)][name].append(data.num(r, column))
    return {k: v for k, v in out.items() if v["off"]["server_j"]}     # Java has no -nobw variant


def compare(groups):
    """One line per server, deploy and load with the on/off figures."""
    lines = []
    for (server, deploy, load), g in sorted(groups.items()):
        on, off = g["on"], g["off"]
        m = {s: {k: data.median(v) for k, v in side.items()} for s, side in (("on", on), ("off", off))}
        lines.append(dict(
            server=server, deploy=deploy, load=load,
            server_on=m["on"]["server_j"], server_off=m["off"]["server_j"],
            ratio=m["on"]["server_j"] / m["off"]["server_j"],
            cv_on=data.cv(on["server_j"]), cv_off=data.cv(off["server_j"]),
            machine_on=m["on"]["machine_j"], machine_off=m["off"]["machine_j"],
            machine_saved=(1 - m["off"]["machine_j"] / m["on"]["machine_j"]) * 100,
            cpu_ratio=m["on"]["cpu_s"] / m["off"]["cpu_s"],
            rate_change=(m["off"]["rate"] / m["on"]["rate"] - 1) * 100,
        ))
    return lines


def short(server):
    return server.split("-", 1)[1].rsplit("-", 3 if server.count("-") > 4 else 1)[0]


def show(folder):
    lines = compare(table(folder))
    print(f"\n=== {folder}")
    for deploy in ("container", "native"):
        sel = [l for l in lines if l["deploy"] == deploy and l["load"] == 80000]
        print(f"\n{deploy}, 80,000 requests (median of 5)")
        print(f"{'server':16} {'server J on':>11} {'off':>6} {'on/off':>7} {'CV on':>6} {'CV off':>6} "
              f"{'machine on':>10} {'off':>6} {'saved':>6} {'CPU on/off*':>11} {'rate':>6}")
        for l in sel:
            print(f"{short(l['server']):16} {l['server_on']:11.1f} {l['server_off']:6.1f} {l['ratio']:6.1f}x "
                  f"{l['cv_on']:5.1f}% {l['cv_off']:5.1f}% {l['machine_on']:10.0f} {l['machine_off']:6.0f} "
                  f"{l['machine_saved']:5.1f}% {l['cpu_ratio']:9.1f}x {l['rate_change']:+5.1f}%")
    print("* CPU on/off: approximate, see MEASURES")
    print("\nBy load (median over the 11 BEAM servers; range in brackets)")
    print(f"{'deploy':10} {'load':>6} {'server on/off':>18} {'CV on':>6} {'CV off':>6} {'machine saved':>18} {'CPU on/off*':>11} {'rate':>6}")
    for deploy in ("container", "native"):
        for load in sorted({l["load"] for l in lines}):
            sel = [l for l in lines if l["deploy"] == deploy and l["load"] == load]
            col = lambda k: [l[k] for l in sel]
            print(f"{deploy:10} {load:>6} {data.median(col('ratio')):6.1f}x ({min(col('ratio')):.1f}-{max(col('ratio')):.1f}) "
                  f"{data.median(col('cv_on')):5.1f}% {data.median(col('cv_off')):5.1f}% "
                  f"{data.median(col('machine_saved')):6.1f}% ({min(col('machine_saved')):.1f}-{max(col('machine_saved')):.1f}) "
                  f"{data.median(col('cpu_ratio')):9.1f}x {data.median(col('rate_change')):+5.1f}%")


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        show(folder)
