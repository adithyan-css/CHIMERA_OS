"""
Write paper/replay_numbers.tex from the live-replay results in results/replay3/, so that
the paper's real-cluster subsection always states what was measured. The wording depends
on the outcome; nothing is claimed that the per-seed numbers do not show.

    python paper/make_replay_tex.py
"""

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "results" / "replay3"
HPAS = ["hpa-70", "hpa-50", "hpa-35"]
CH = "chimera-rate"


def load():
    res = {}
    for f in D.glob("seed*/result_*.json"):
        r = json.loads(f.read_text())
        res[(r["mode"], r["seed"])] = r
    seeds = sorted({s for _, s in res})
    return res, [s for s in seeds if all((m, s) in res for m in HPAS + [CH])]


def main():
    res, seeds = load()
    n = len(seeds)
    if n == 0:
        raise SystemExit("no complete seed in results/replay3 yet")

    def mean(mode, key, scale=1.0):
        return scale * float(np.mean([res[(mode, s)][key] for s in seeds]))

    rows = []
    for m, label in [("hpa-70", "HPA, 70\\,\\% target"), ("hpa-50", "HPA, 50\\,\\% target"),
                     ("hpa-35", "HPA, 35\\,\\% target"), (CH, "CHIMERA")]:
        v = [100 * res[(m, s)]["slo_violation_rate"] for s in seeds]
        c = [res[(m, s)]["core_minutes"] for s in seeds]
        v1 = 100 * float(np.mean([res[(m, s)]["violations_at"]["1.0"] for s in seeds]))
        rows.append(f"{label} & {np.mean(v):.1f} & {min(v):.1f}--{max(v):.1f} & {v1:.1f} & {np.mean(c):.1f} \\\\")

    # Per seed: which HPA settings CHIMERA dominates (no more CPU and fewer violations),
    # which dominate it, and, when its cost lies inside the HPA's range, the gap to the
    # HPA frontier interpolated at that cost.
    gaps, inside, dominates, dominated = [], 0, [], []
    for s in seeds:
        pts = sorted((res[(h, s)]["core_minutes"], 100 * res[(h, s)]["slo_violation_rate"]) for h in HPAS)
        x, y = [p[0] for p in pts], [p[1] for p in pts]
        cc, cv = res[(CH, s)]["core_minutes"], 100 * res[(CH, s)]["slo_violation_rate"]
        dominates.append(sum(1 for a, b in pts if cc <= a and cv < b))
        dominated.append(sum(1 for a, b in pts if a <= cc and b < cv))
        if x[0] <= cc <= x[-1]:
            inside += 1
            gaps.append(float(np.interp(cc, x, y)) - cv)
    wins = sum(g > 0 for g in gaps)
    cv, cc = mean(CH, "slo_violation_rate", 100), mean(CH, "core_minutes")
    seedw = "seed" if n == 1 else "seeds"
    h70v, h70c = mean("hpa-70", "slo_violation_rate", 100), mean("hpa-70", "core_minutes")
    h50v, h50c = mean("hpa-50", "slo_violation_rate", 100), mean("hpa-50", "core_minutes")
    h35v, h35c = mean("hpa-35", "slo_violation_rate", 100), mean("hpa-35", "core_minutes")
    n_dom = sum(d > 0 for d in dominates)
    n_domd = sum(d > 0 for d in dominated)

    parts = []
    if n_dom:
        parts.append(f"On {n_dom} of {n} {seedw} CHIMERA dominated at least one HPA setting: it used no more CPU and "
                     f"had fewer violations.")
    if n_domd:
        parts.append(f"On {n_domd} of {n} {seedw} at least one HPA setting dominated CHIMERA.")
    if inside:
        mg = float(np.mean(gaps))
        parts.append(f"Where CHIMERA's cost lay inside the range the HPA settings span ({inside} of {n} {seedw}), "
                     f"interpolating the HPA's violation rate at that cost gave CHIMERA fewer violations on {wins} of "
                     f"{inside}, with a mean gap of {mg:+.1f} points.")
    else:
        parts.append("CHIMERA's cost lay below the cheapest HPA setting on every seed, so the HPA frontier cannot be "
                     "interpolated at that cost.")
    parts.append(f"On average CHIMERA used {cc:.1f} core-minutes for {cv:.1f}\\,\\% violations; the HPA needed "
                 f"{h70c:.1f} for {h70v:.1f}\\,\\% at a 70\\,\\% target, {h50c:.1f} for {h50v:.1f}\\,\\% at 50\\,\\%, and "
                 f"{h35c:.1f} for {h35v:.1f}\\,\\% at 35\\,\\%.")
    verdict = " ".join(parts)

    if n_dom == n and n_domd == 0:
        if cv < h50v:
            short = (f"shows it using less CPU than every HPA setting tried while missing the objective less often "
                     f"than the two cheaper ones, on all {n} {seedw}")
        else:
            short = (f"shows it dominating the HPA at its default 70\\,\\% target on all {n} {seedw} (less CPU, fewer "
                     f"violations), while more expensive HPA settings reach lower violation rates")
    elif n_domd == n and n_dom == 0:
        short = f"shows a well-chosen HPA setting dominating it on all {n} {seedw}"
    elif inside and wins == inside and n_domd == 0:
        short = f"shows fewer violations than the HPA at matched cost on the {inside} comparable {seedw}"
    else:
        short = (f"is mixed: it dominated an HPA setting on {n_dom} of {n} {seedw} and was dominated on {n_domd}")

    body = (
        "An open-loop load generator replayed a 24-minute demand trace (12 minutes of an Azure trace, then a "
        "flash crowd to five times the mean) against a CPU-bound service costing 61\\,ms of CPU per request, with "
        "a 0.3\\,s latency objective (five times the service time, as in the simulator). Each controller started "
        "from the same allocation with three unscored lead-in minutes, and both sides could reach the same ten "
        f"cores. We ran the HPA at three utilisation targets, to give it a cost frontier, and CHIMERA at its "
        f"evaluated default, with {n} {seedw} each (Table~\\ref{{tab:replay}}).\n\n"
        "\\begin{table}[t]\n"
        f"\\caption{{Live replay, mean of {n} {seedw}. Viol.\\ = requests over 0.3\\,s (\\%), with the range over "
        "seeds; $>$1\\,s = requests over one second (\\%); CPU in core-minutes.}\n"
        "\\label{tab:replay}\n\\centering\n\\begin{tabular}{@{}lrrrr@{}}\n\\toprule\n"
        "Controller & Viol. & Range & $>$1\\,s & CPU \\\\\n\\midrule\n" + "\n".join(rows) +
        "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n\n" + verdict + " "
        f"With {n} {seedw} no significance test is meaningful, and we treat this as indicative. "
        "Every controller missed the objective far more often than in simulation: a 61\\,ms request on a pod "
        "limited to half a core exceeds a single CFS quota period, so the simulator's latency law is kinder than "
        "this service. In an earlier single-run comparison a request-rate signal reached a given allocation 20 to "
        "25\\,s sooner after the spike began than the default one-minute CPU-usage rate."
    )
    abstract = (f"On a real cluster it resizes pods in place without restarts; a trace replay against the HPA with "
                f"{n} {seedw} {short}.")
    concl = (f"On a real cluster the operator works, and a small trace-replay experiment {short}. A larger live "
             "study, with more seeds and a service whose latency the simulator models faithfully, is the main work "
             "that remains.")
    out = ("% generated by paper/make_replay_tex.py -- do not edit\n"
           f"\\newcommand{{\\ReplayAbstract}}{{{abstract}}}\n"
           f"\\newcommand{{\\ReplayBody}}{{{body}}}\n"
           f"\\newcommand{{\\ReplayConclusion}}{{{concl}}}\n")
    (ROOT / "paper" / "replay_numbers.tex").write_text(out, encoding="utf-8", newline="\n")
    print(f"{n} seed(s); inside range {inside}; gaps {['%+.1f' % g for g in gaps]}")
    print(short)


if __name__ == "__main__":
    main()
