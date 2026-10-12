"""
Write paper/replay_numbers.tex from the live-replay results in results/replay3/, so that
the paper's real-cluster subsection always states what was measured. The wording depends
on the outcome; nothing is claimed that the per-seed numbers do not show.

A run "dominates" another run of the same seed (the same request stream) when it used no
more CPU and had fewer violations.

    python paper/make_replay_tex.py
"""

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "results" / "replay3"
HPAS = [("hpa-70", "HPA, 70\\,\\% target"), ("hpa-50", "HPA, 50\\,\\% target"), ("hpa-35", "HPA, 35\\,\\% target")]
CHIS = [("chimera-rate", "CHIMERA, 0.46 (default)"), ("chimera-rate-35", "CHIMERA, 0.35"),
        ("chimera-rate-25", "CHIMERA, 0.25")]


def load(first_attempts=False):
    res = {}
    for f in D.glob("seed*/result_*.json"):
        r = json.loads(f.read_text())
        res[(r["mode"], r["seed"])] = r
    if first_attempts:                       # swap repeated runs back to their first attempt
        for f in D.glob("seed*/first_attempt_*/result_*.json"):
            r = json.loads(f.read_text())
            res[(r["mode"], r["seed"])] = r
    return res


def point(r):
    return r["core_minutes"], 100 * r["slo_violation_rate"]


def dominance(res, seeds, chis):
    """(HPA runs dominated by some CHIMERA run of the same seed, CHIMERA runs dominated by
    some HPA run of the same seed, matched-cost gaps for CHIMERA runs inside the HPA range)."""
    hpa_dom, chi_dom, gaps = 0, 0, []
    for s in seeds:
        hp = sorted(point(res[(h, s)]) for h, _ in HPAS)
        cp = [point(res[(c, s)]) for c, _ in chis]
        hpa_dom += sum(any(cc <= hc and cv < hv for cc, cv in cp) for hc, hv in hp)
        chi_dom += sum(any(hc <= cc and hv < cv for hc, hv in hp) for cc, cv in cp)
        x, y = [p[0] for p in hp], [p[1] for p in hp]
        gaps += [float(np.interp(cc, x, y)) - cv for cc, cv in cp if x[0] <= cc <= x[-1]]
    return hpa_dom, chi_dom, gaps


def main():
    res = load()
    seeds = sorted({s for _, s in res})
    chis = [c for c in CHIS if all((c[0], s) in res for s in seeds)]
    seeds = [s for s in seeds if all((m, s) in res for m, _ in HPAS + chis)]
    n = len(seeds)
    if n == 0:
        raise SystemExit("no complete seed in results/replay3 yet")
    seedw = "seed" if n == 1 else "seeds"

    rows = []
    for m, label in HPAS + chis:
        v = [100 * res[(m, s)]["slo_violation_rate"] for s in seeds]
        c = [res[(m, s)]["core_minutes"] for s in seeds]
        v1 = 100 * float(np.mean([res[(m, s)]["violations_at"]["1.0"] for s in seeds]))
        rows.append(f"{label} & {np.mean(v):.1f} & {min(v):.1f}--{max(v):.1f} & {v1:.1f} & {np.mean(c):.1f} \\\\")
        if m == HPAS[-1][0]:
            rows.append("\\midrule")

    nh, nc = n * len(HPAS), n * len(chis)
    hpa_dom, chi_dom, gaps = dominance(res, seeds, chis)
    wins = sum(g > 0 for g in gaps)
    parts = [f"Of the {nh} HPA runs, {hpa_dom} were dominated by a CHIMERA run with the same seed: one that used no "
             f"more CPU and missed the objective less often. Of the {nc} CHIMERA runs, {chi_dom} "
             f"{'was' if chi_dom == 1 else 'were'} dominated by an HPA run."]
    if gaps:
        parts.append(f"For the {len(gaps)} CHIMERA runs whose cost lay inside the range spanned by the HPA settings, "
                     f"interpolating the HPA's violation rate at the same cost gave CHIMERA fewer violations in "
                     f"{wins}, with a mean gap of {np.mean(gaps):+.1f} points.")

    def m_(mode, key, scale=1.0):
        return scale * float(np.mean([res[(mode, s)][key] for s in seeds]))

    if len(chis) > 1:
        best = min(chis, key=lambda c: m_(c[0], "slo_violation_rate"))
        mid = chis[1][0]
        parts.append(f"At setting 0.{mid.rsplit('-', 1)[-1]} CHIMERA averaged "
                     f"{m_(mid, 'slo_violation_rate', 100):.1f}\\,\\% "
                     f"violations for {m_(mid, 'core_minutes'):.1f} core-minutes, against "
                     f"{m_('hpa-35', 'slo_violation_rate', 100):.1f}\\,\\% for {m_('hpa-35', 'core_minutes'):.1f} "
                     f"with the HPA at its most generous setting.")

    # Repeated runs: say so, and say what the first attempts would have given.
    firsts = sorted(D.glob("seed*/first_attempt_*/result_*.json"))
    if firsts:
        alt = load(first_attempts=True)
        a_h, a_c, a_g = dominance(alt, seeds, chis)
        f0 = json.loads(firsts[0].read_text())
        setting = f0["mode"].rsplit("-", 1)[-1]
        setting = f"0.{setting}" if setting.isdigit() else "0.46"
        parts.append(
            f"One CHIMERA run (seed {f0['seed']}, setting {setting}) was repeated. In the first attempt the "
            f"service was slow whatever it was given (median latency {f0['p50']:.2f}\\,s with four to ten cores "
            f"allocated for at most three cores of demand, and {f0['requests']} requests issued where the other "
            f"runs issued between 22\\,000 and 22\\,900), which points to interference on the host and not to a control "
            f"decision; it had {100 * f0['slo_violation_rate']:.1f}\\,\\% violations. Counting that attempt "
            f"instead of the repeat, {a_h} of {nh} HPA runs are dominated and {a_c} of {nc} CHIMERA runs. "
            f"Both attempts are in the repository.")
    verdict = " ".join(parts)

    if chi_dom == 0 and hpa_dom >= 0.6 * nh:
        short = (f"shows CHIMERA on or below the HPA's cost--violation frontier: {hpa_dom} of {nh} HPA runs were "
                 f"dominated by a CHIMERA run with the same seed, and no CHIMERA run was dominated")
    elif hpa_dom > chi_dom:
        short = (f"favours CHIMERA without being uniform: {hpa_dom} of {nh} HPA runs were dominated by a CHIMERA run "
                 f"with the same seed, and {chi_dom} of {nc} CHIMERA runs by an HPA run")
    elif hpa_dom == chi_dom:
        short = f"is mixed: each side dominated {hpa_dom} of the other's runs"
    else:
        short = (f"favours the HPA: {chi_dom} of {nc} CHIMERA runs were dominated by an HPA run with the same seed, "
                 f"against {hpa_dom} of {nh} the other way")

    body = (
        "An open-loop load generator replayed a 24-minute demand trace (12 minutes of an Azure trace, then a "
        "flash crowd to five times the mean) against a CPU-bound service costing 61\\,ms of CPU per request, with "
        "a 0.3\\,s latency objective (five times the service time, as in the simulator). Each controller started "
        "from the same allocation with three unscored lead-in minutes, and both sides could reach the same ten "
        f"cores. We ran the HPA at three utilisation targets and CHIMERA at {len(chis)} settings, so that each has a "
        f"cost frontier, with {n} {seedw} each (Table~\\ref{{tab:replay}}, Fig.~\\ref{{fig:replay}}). CHIMERA used "
        "a request-rate signal; no run restarted a container.\n\n"
        "\\begin{table}[t]\n"
        f"\\caption{{Live replay, mean of {n} {seedw}. Viol.\\ = requests over 0.3\\,s (\\%), with the range over "
        "seeds; $>$1\\,s = requests over one second (\\%); CPU in core-minutes.}\n"
        "\\label{tab:replay}\n\\centering\n\\begin{tabular}{@{}lrrrr@{}}\n\\toprule\n"
        "Controller & Viol. & Range & $>$1\\,s & CPU \\\\\n\\midrule\n" + "\n".join(rows) +
        "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n\n" + verdict + " "
        f"With {n} {seedw}, one service and one trace this is indicative, not a significance result, and the "
        "spread between seeds is large. Every controller missed the objective far more often than in simulation: "
        "a 61\\,ms request on a pod limited to half a core exceeds a single CFS quota period, so the simulator's "
        "latency law is kinder than this service."
    )
    abstract = (f"On a real cluster it resizes pods in place without restarts, and a small trace replay against "
                f"the HPA ({n} {seedw}) {short}.")
    concl = (f"On a real cluster a small trace-replay experiment {short}. A larger live study, with more seeds, "
             "more services and a simulator whose latency law is fitted to measurements, is the main work that "
             "remains.")
    out = ("% generated by paper/make_replay_tex.py -- do not edit\n"
           f"\\newcommand{{\\ReplayAbstract}}{{{abstract}}}\n"
           f"\\newcommand{{\\ReplayBody}}{{{body}}}\n"
           f"\\newcommand{{\\ReplayConclusion}}{{{concl}}}\n")
    (ROOT / "paper" / "replay_numbers.tex").write_text(out, encoding="utf-8", newline="\n")
    print(f"{n} seed(s), {len(chis)} CHIMERA setting(s): HPA runs dominated {hpa_dom}/{nh}, CHIMERA runs dominated "
          f"{chi_dom}/{nc}, matched-cost gaps {['%+.1f' % g for g in gaps]}")
    print(short)


if __name__ == "__main__":
    main()
