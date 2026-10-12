"""Step 4: BEAM busy-waiting on vs off, WebSocket. Only tests with enough meter readings are used: the
streams (100 clients, 10 messages per second each, 5 s; 8 or more readings). The bursts last 1.7-2.6 s
(2-4 readings at 0.5 s) and are only listed, not tested.

    ./srv/bin/python3 analysis/busy_wait_websocket.py results/<websocket folder> [results/<mist re-run folder>]
"""
import collections
import random
import sys

import busy_wait_http
import data
import stats

USED = ("stream 8 KB", "stream 1024 KB")


def table(folder):
    out = collections.defaultdict(lambda: {"on": collections.defaultdict(list), "off": collections.defaultdict(list)})
    for r in data.rows(folder):
        key = (data.server(r), r["Deploy"], data.load(r))
        side = out[key][data.busy_wait(r)]
        side["server_j"].append(data.num(r, "Container Energy (J)"))
        side["machine_j"].append(data.num(r, "Host Energy (J)"))
        side["latency_ms"].append(data.num(r, "Avg Latency (ms)"))
    return {k: v for k, v in out.items() if v["off"]["server_j"]}


def show(folder):
    rng = random.Random(stats.SEED)
    print(f"\n=== {folder}")
    print("server energy on/off [95% CI] p delta | machine saved | latency on -> off (ms)")
    for (server, deploy, load), g in sorted(table(folder).items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
        s_on, s_off, m_on, m_off = g["on"]["server_j"], g["off"]["server_j"], g["on"]["machine_j"], g["off"]["machine_j"]
        lo, hi = stats.bootstrap(s_on, s_off, stats.ratio, rng)
        mark = "" if load in USED else "   (too short, not tested)"
        print(f"  {load:>14} {deploy:9} {busy_wait_http.short(server):15} "
              f"{data.median(s_on):6.2f}/{data.median(s_off):6.2f} J = {stats.ratio(s_on, s_off):4.1f}x [{lo:4.1f}-{hi:4.1f}] "
              f"p={stats.mann_whitney_p(s_on, s_off):.3f} d={stats.cliffs_delta(s_on, s_off):+.2f} | "
              f"{stats.saved(m_on, m_off):5.1f}% | {data.median(g['on']['latency_ms']):7.1f} -> {data.median(g['off']['latency_ms']):7.1f}{mark}")


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        show(folder)
