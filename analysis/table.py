"""Table I of the busy-wait paper: the busy-wait effect for every workload and deploy, in LaTeX.
One row per workload x deploy (80,000 requests, 11 BEAM servers, median of 5 runs each):
  - server energy on/off: median over servers (range), attributed
  - platform energy (RAPL psys) saved with busy-waiting off: median over servers (range), measured; servers whose 95% CI is above 0
  - spread between the 5 repeats (CV): median over servers (max), busy-waiting on and off
  - sign test across the 11 servers (servers with on > off for server and machine energy)

    ./srv/bin/python3 analysis/table.py results/<static folder> results/<dynamic folder>
"""
import random
import sys

import busy_wait_http
import data
import stats


def rows(folder):
    rng = random.Random(stats.SEED)
    out = {}
    for (server, deploy, load), g in busy_wait_http.table(folder).items():
        if load != 80000:
            continue
        s_on, s_off, m_on, m_off = g["on"]["server_j"], g["off"]["server_j"], g["on"]["machine_j"], g["off"]["machine_j"]
        out.setdefault(deploy, []).append(dict(
            ratio=stats.ratio(s_on, s_off), saved=stats.saved(m_on, m_off),
            saved_ci=stats.bootstrap(m_on, m_off, stats.saved, rng),
            cv_on=data.cv(s_on), cv_off=data.cv(s_off)))
    return out


def main(folders):
    print("% Table I, made by analysis/table.py from: " + ", ".join(folders))
    print(r"\begin{table}[t]")
    print(r"\caption{Busy-waiting on vs off, 80,000 requests, 11 BEAM servers, median of 5 runs per server. "
          r"Server energy is attributed by CPU-time share; platform energy (RAPL psys) is measured. "
          r"Values: median over servers (range); spread: median (max).}")
    print(r"\label{tab:busywait}")
    print(r"\centering\small\setlength{\tabcolsep}{3pt}")
    print(r"\begin{tabular}{llccccc}")
    print(r"\hline")
    print(r" & & Server & Platform & CI$>$0 & CV on & CV off \\")
    print(r" & & on/off & saved (\%) & & (\%) & (\%) \\")
    print(r"\hline")
    for folder in folders:
        wl = data.rows(folder)[0]["Type"]
        for deploy, name in (("container", "Docker"), ("native", "native")):
            r = rows(folder)[deploy]
            col = lambda k: [x[k] for x in r]
            ci = sum(x["saved_ci"][0] > 0 for x in r)
            k_s, k_m, n = sum(x > 1 for x in col("ratio")), sum(x > 0 for x in col("saved")), len(r)
            assert (k_s, k_m) == (n, n), "the sign test line in the caption assumes all servers show the effect"
            print(f"{wl.capitalize() if deploy == 'container' else ''} & {name} & "
                  f"{data.median(col('ratio')):.1f}$\\times$ ({min(col('ratio')):.1f}--{max(col('ratio')):.1f}) & "
                  f"{data.median(col('saved')):.1f} ({min(col('saved')):.1f}--{max(col('saved')):.1f}) & "
                  f"{ci}/{n} & {data.median(col('cv_on')):.1f} ({max(col('cv_on')):.0f}) & "
                  f"{data.median(col('cv_off')):.1f} ({max(col('cv_off')):.0f}) \\\\")
    print(r"\hline")
    print(r"\multicolumn{7}{l}{\footnotesize All 11 servers: on $>$ off for server and platform energy in every row (sign test $p=0.001$).} \\")
    print(r"\multicolumn{7}{l}{\footnotesize CI$>$0: servers whose bootstrap 95\% CI of the platform saving excludes 0.} \\")
    print(r"\end{tabular}")
    print(r"\end{table}")


if __name__ == "__main__":
    main(sys.argv[1:])
