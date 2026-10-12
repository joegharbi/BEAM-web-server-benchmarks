"""Step 3: statistics for busy-waiting on vs off (HTTP), without extra libraries.

Per server, deploy and load (5 runs on vs 5 runs off):
  - Mann-Whitney U, exact two-sided p (with 5 vs 5 the smallest possible p is 2/252 = 0.008)
  - Cliff's delta: P(on > off) - P(on < off); 1 = every on run above every off run
  - bootstrap 95% confidence intervals (10,000 resamples, fixed seed) for the ratio of medians
    (server energy on/off) and for the machine energy saved with busy-waiting off (%)
Across the BEAM servers: an exact sign test (how many servers show the effect, and how likely that is by chance).

    ./srv/bin/python3 analysis/stats.py results/<static folder> results/<dynamic folder>
"""
import itertools
import math
import random
import sys

import busy_wait_http
import data

RESAMPLES = 10_000
SEED = 1


def mann_whitney_p(a, b):
    """Exact two-sided p of the Mann-Whitney U test (all ways to split the pooled values; ties count half)."""
    pooled = a + b

    def u(x, y):
        return sum((xi > yi) + 0.5 * (xi == yi) for xi in x for yi in y)
    observed = abs(u(a, b) - len(a) * len(b) / 2)
    extreme = total = 0
    for idx in itertools.combinations(range(len(pooled)), len(a)):
        x = [pooled[i] for i in idx]
        y = [pooled[i] for i in range(len(pooled)) if i not in idx]
        total += 1
        extreme += abs(u(x, y) - len(a) * len(b) / 2) >= observed - 1e-12
    return extreme / total


def cliffs_delta(a, b):
    return sum((x > y) - (x < y) for x in a for y in b) / (len(a) * len(b))


def bootstrap(a, b, statistic, rng):
    """95% interval of statistic(resample of a, resample of b)."""
    values = sorted(statistic([rng.choice(a) for _ in a], [rng.choice(b) for _ in b]) for _ in range(RESAMPLES))
    return values[int(0.025 * RESAMPLES)], values[int(0.975 * RESAMPLES) - 1]


def sign_test_p(successes, n):
    """Exact two-sided sign test: chance of a split at least this uneven if on and off were alike."""
    k = max(successes, n - successes)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def ratio(on, off):
    return data.median(on) / data.median(off)


def saved(on, off):
    return (1 - data.median(off) / data.median(on)) * 100


def analyse(folder):
    rng = random.Random(SEED)
    out = []
    for (server, deploy, load), g in sorted(busy_wait_http.table(folder).items()):
        s_on, s_off = g["on"]["server_j"], g["off"]["server_j"]
        m_on, m_off = g["on"]["machine_j"], g["off"]["machine_j"]
        out.append(dict(
            server=server, deploy=deploy, load=load,
            ratio=ratio(s_on, s_off), ratio_ci=bootstrap(s_on, s_off, ratio, rng),
            p_server=mann_whitney_p(s_on, s_off), d_server=cliffs_delta(s_on, s_off),
            saved=saved(m_on, m_off), saved_ci=bootstrap(m_on, m_off, saved, rng),
            p_machine=mann_whitney_p(m_on, m_off), d_machine=cliffs_delta(m_on, m_off),
        ))
    return out


def show(folder):
    rows = analyse(folder)
    print(f"\n=== {folder}")
    for deploy in ("container", "native"):
        print(f"\n{deploy}, 80,000 requests: server energy on/off [95% CI], p, Cliff's delta | machine saved [95% CI], p, delta")
        for r in (r for r in rows if r["deploy"] == deploy and r["load"] == 80000):
            print(f"  {busy_wait_http.short(r['server']):15} {r['ratio']:4.1f}x [{r['ratio_ci'][0]:4.1f}-{r['ratio_ci'][1]:4.1f}] "
                  f"p={r['p_server']:.3f} d={r['d_server']:+.2f} | {r['saved']:5.1f}% [{r['saved_ci'][0]:5.1f} to {r['saved_ci'][1]:5.1f}] "
                  f"p={r['p_machine']:.3f} d={r['d_machine']:+.2f}")
    print("\nAcross the 11 BEAM servers (sign test): servers with on > off")
    print(f"{'deploy':10} {'load':>6} {'server energy':>16} {'machine energy':>16} {'complete separation (d=1)':>27} {'machine CI above 0':>19}")
    for deploy in ("container", "native"):
        for load in sorted({r["load"] for r in rows}):
            sel = [r for r in rows if r["deploy"] == deploy and r["load"] == load]
            n = len(sel)
            k_s = sum(r["ratio"] > 1 for r in sel)
            k_m = sum(r["saved"] > 0 for r in sel)
            sep_s = sum(r["d_server"] == 1 for r in sel)
            sep_m = sum(r["d_machine"] == 1 for r in sel)
            ci_m = sum(r["saved_ci"][0] > 0 for r in sel)
            print(f"{deploy:10} {load:>6} {k_s:>3}/{n} p={sign_test_p(k_s, n):.4f} {k_m:>3}/{n} p={sign_test_p(k_m, n):.4f} "
                  f"{'server ' + str(sep_s) + '/' + str(n) + ', machine ' + str(sep_m) + '/' + str(n):>27} {ci_m:>15}/{n}")


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        show(folder)
