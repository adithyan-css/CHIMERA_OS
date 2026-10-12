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

    gaps, inside = [], 0
    for s in seeds:
        pts = sorted((res[(h, s)]["core_minutes"], 100 * res[(h, s)]["slo_violation_rate"]) for h in HPAS)
        x, y = [p[0] for p in pts], [p[1] for p in pts]
        cc, cv = res[(CH, s)]["core_minutes"], 100 * res[(CH, s)]["slo_violation_rate"]
        if x[0] <= cc <= x[-1]:
            inside += 1
            gaps.append(float(np.interp(cc, x, y)) - cv)
    wins = sum(g > 0 for g in gaps)
    cv, cc = mean(CH, "slo_violation_rate", 100), mean(CH, "core_minutes")
    best_h = min(HPAS, key=lambda h: mean(h, "slo_violation_rate"))
    hv, hc = mean(best_h, "slo_violation_rate", 100), mean(best_h, "core_minutes")
    cheaper = [h for h in HPAS if mean(h, "core_minutes") <= cc]
    seedw = "seed" if n == 1 else "seeds"

    if inside == 0:
        verdict = (f"CHIMERA's CPU cost fell outside the range spanned by the three HPA settings on every seed, so the "
                   f"two cannot be compared at equal cost; at its own operating point it had {cv:.1f}\\,\\% violations "
                   f"for {cc:.1f} core-minutes.")
        short = "does not allow a comparison at matched cost"
    else:
        mg = float(np.mean(gaps))
        if wins == len(gaps) and inside == n:
            verdict = (f"Interpolating the HPA's violation rate between its settings at the CPU CHIMERA used, seed by "
                       f"seed, CHIMERA had fewer violations on all {n} {seedw}, by {mg:.1f} points on average.")
            short = f"shows fewer violations than the HPA at matched cost on all {n} {seedw}, by {mg:.1f} points"
        elif wins == 0:
            verdict = (f"Interpolating the HPA's violation rate between its settings at the CPU CHIMERA used, seed by "
                       f"seed, the HPA had fewer violations on all {len(gaps)} comparable {seedw}, by {-mg:.1f} points "
                       f"on average: on this service and trace CHIMERA did not beat a well-chosen HPA setting.")
            short = f"shows the HPA ahead at matched cost, by {-mg:.1f} points"
        else:
            verdict = (f"Interpolating the HPA's violation rate between its settings at the CPU CHIMERA used, CHIMERA "
                       f"had fewer violations on {wins} of {len(gaps)} comparable {seedw} (mean gap {mg:+.1f} points; "
                       f"its cost was inside the HPA's range on {inside} of {n}): the result is mixed.")
            short = f"is mixed at matched cost ({wins} of {len(gaps)} {seedw} in CHIMERA's favour)"

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
