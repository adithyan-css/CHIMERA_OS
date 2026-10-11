#!/usr/bin/env python3
"""
CHIMERA — live demonstration script
===================================

Runs four controllers over the SAME workload trace with the SAME random seed, through
the SAME code path as the full evaluation (src/scripts/run_full_evaluation.py:
run_simulation), so every difference in the output comes from the control law:

    CHIMERA          joint horizontal + vertical control, delay-aware
    CHIMERA (no SA)  the same controller with delay-state augmentation switched off
    AAPA             the predictive, horizontal-only autoscaler this project builds on
    HPA              Kubernetes' standard reactive horizontal autoscaler

Usage (PowerShell, from the project root):

    $env:PYTHONPATH="."; python demo_run.py
    $env:PYTHONPATH="."; python demo_run.py --workload w_0002 --seed 123
    $env:PYTHONPATH="."; python demo_run.py --table        # comparison table only
    $env:PYTHONPATH="."; python demo_run.py --ascii        # no unicode blocks
    $env:PYTHONPATH="."; python demo_run.py --no-color

Runtime is roughly 10-20 seconds (it trains the trace's forecaster, then runs four
simulations). The final section reads the saved evaluation from results/results_v5.csv
-- it does not re-run it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# ----------------------------------------------------------------------------
# Dependency check -- fail with something readable instead of a stack trace
# ----------------------------------------------------------------------------
try:
    import numpy as np
    import simpy  # noqa: F401
except ImportError as exc:                                    # pragma: no cover
    sys.exit(f"Missing dependency: {exc.name}\n"
             f"Fix with:  pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)   # the harness resolves dataset/ relative to the project root

logging.disable(logging.CRITICAL)
import warnings                                                # noqa: E402
warnings.filterwarnings("ignore")

try:
    from src.core.chimera_controller import ChimeraController
    from src.scripts import run_full_evaluation as harness
except ImportError as exc:                                    # pragma: no cover
    sys.exit(f"Could not import the project modules ({exc}).\n"
             f"Run from the project root with PYTHONPATH set:\n"
             f'    $env:PYTHONPATH="."; python demo_run.py')

logging.disable(logging.CRITICAL)

# ----------------------------------------------------------------------------
# Experiment settings -- taken from the evaluation harness, not duplicated
# ----------------------------------------------------------------------------
CONFIGS = harness.build_configs()
SLO_SECONDS = harness.SLO_SECONDS
TEST_WINDOW = (70, 100)                 # only these minutes are scored
WARMUP = 20                             # unscored minutes before the window (same as the final protocol)
BOUNDS = {k: CONFIGS['CHIMERA'][k] for k in ('v_min', 'v_max', 'h_min', 'h_max')}
RUNS = [("CHIMERA", "CHIMERA", "CHIMERA"),
        ("CHIMERA_NO_SA", "CHIMERA (no SA)", "CHIMERA_w/o_State_Augmentation"),
        ("AAPA", "AAPA", "AAPA"),
        ("HPA", "HPA", "HPA")]

# ----------------------------------------------------------------------------
# Terminal helpers
# ----------------------------------------------------------------------------
class Style:
    def __init__(self, enabled: bool):
        if enabled and os.name == "nt":
            os.system("")   # switches on ANSI handling in Windows consoles
        self.on = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s):  return self._w("1", s)
    def dim(self, s):   return self._w("2", s)
    def cyan(self, s):  return self._w("36", s)
    def green(self, s): return self._w("32", s)
    def amber(self, s): return self._w("33", s)
    def red(self, s):   return self._w("31", s)


def rule(st: Style, title: str = "", width: int = 78) -> None:
    if title:
        pad = "-" * max(0, width - len(title) - 3)
        print(f"\n{st.cyan('--')} {st.bold(title)} {st.dim(pad)}")
    else:
        print(st.dim("-" * width))


def sparkline(values, width: int = 46, ascii_only: bool = False) -> str:
    """Compress a series into one line of block characters."""
    if not len(values):
        return ""
    ramp = " .:-=+*#%@" if ascii_only else " ▁▂▃▄▅▆▇█"
    v = np.asarray(values, dtype=float)
    idx = np.linspace(0, len(v) - 1, width)
    v = np.interp(idx, np.arange(len(v)), v)
    lo, hi = v.min(), v.max()
    if hi - lo < 1e-9:
        return ramp[len(ramp) // 2] * width
    scaled = (v - lo) / (hi - lo) * (len(ramp) - 1)
    return "".join(ramp[int(round(x))] for x in scaled)


# ----------------------------------------------------------------------------
# Workload + simulation (through the evaluation harness)
# ----------------------------------------------------------------------------
def load_workload(name: str):
    wid = "real_kswd_w" + name.split("_")[-1].lstrip("0")
    items = {w['id']: w for w in harness.get_remediation_workloads()}
    if wid not in items:
        sys.exit(f"No such workload: {name}\nAvailable: w_0001 .. w_0005")
    return items[wid]


def simulate(item, config_name: str, cqr, seed: int):
    t0 = time.perf_counter()
    metrics, sim = harness.run_simulation(item['trace'], config_name, CONFIGS[config_name],
                                          item['type'], cqr, seed, return_sim=True, warmup=WARMUP)
    wall = time.perf_counter() - t0
    rt = np.asarray(metrics.response_times) if metrics.response_times else np.zeros(1)
    return dict(
        slo=metrics.get_slo_violation_rate() * 100,
        mean_rt=rt.mean(),
        p99=float(np.percentile(rt, 99)),
        cpu_min=metrics.total_cpu_allocated,
        pod_min=metrics.pod_minutes,
        actions=metrics.scaling_actions,
        wall=wall,
        timeline=sim.timeline,
    )


# ----------------------------------------------------------------------------
# Demo sections
# ----------------------------------------------------------------------------
def show_control_law(st: Style) -> None:
    rule(st, "1.  THE CONTROL LAW")
    c = ChimeraController(**BOUNDS)
    c._solve_riccati(tau=20.0)

    print(f"  Horizontal delay 20.0 s over a 15.0 s control loop "
          f"-> {len(c.dh_history)} in-flight command slots\n")
    print(f"  {st.dim('Augmented state transition matrix A_aug')}   "
          f"{st.dim(f'{c.A_aug.shape[0]}x{c.A_aug.shape[1]}')}")
    for row in c.A_aug:
        print("      [ " + "  ".join(f"{x:4.1f}" for x in row) + " ]")

    eig = np.linalg.eigvalsh(c.P)
    ok = bool((eig > 0).all())
    print(f"\n  {st.dim('Riccati solution P')}     eigenvalues "
          f"{np.array2string(eig, precision=2, suppress_small=True)}")
    print(f"  {st.dim('Positive definite')}      "
          f"{st.green('yes -- V = e^T P e is a valid Lyapunov function') if ok else st.red('NO')}")
    print(f"  {st.dim('Feedback gain K')}        shape {c.K.shape}, solved once and cached, "
          f"applied as u = -K @ e")

    reps = 200
    x, xs = np.array([1.0, 4.0]), np.array([1.6, 6.0])
    t0 = time.perf_counter()
    for _ in range(reps):
        c.get_desired_allocation(x_t=x, x_star=xs, uncertainty_width=1.4, tau=20.0)
    per = (time.perf_counter() - t0) / reps * 1000
    print(f"  {st.dim('Cost per control step')}  {per:.2f} ms "
          f"{st.dim(f'(mean of {reps}; includes the safety-filter search)')}")


def show_results(st: Style, runs: dict) -> None:
    rule(st, "3.  RESULTS  (same trace, seed and SLO; each at its default setting)")
    head = (f"  {'controller':<18}{'SLO viol':>10}{'mean RT':>9}{'p99 RT':>9}"
            f"{'CPU-min':>9}{'pod-min':>9}{'actions':>9}")
    print(st.dim(head))
    print(st.dim("  " + "-" * (len(head) - 2)))
    for key, label, _ in RUNS:
        r = runs[key]
        line = (f"  {label:<18}{r['slo']:>9.2f}%{r['mean_rt']:>9.3f}{r['p99']:>9.2f}"
                f"{r['cpu_min']:>9.1f}{r['pod_min']:>9.1f}{r['actions']:>9d}")
        print(st.bold(line) if key == "CHIMERA" else line)

    ch, no_sa = runs["CHIMERA"], runs["CHIMERA_NO_SA"]
    d_slo = no_sa["slo"] - ch["slo"]
    d_cpu = (no_sa["cpu_min"] / ch["cpu_min"] - 1) * 100
    print()
    print(f"  {st.amber('Ablation:')} with delay-state augmentation switched off the controller "
          f"re-orders capacity")
    print(f"  that is already booting: {d_slo:+.2f} points of SLO and "
          f"{st.bold(f'{d_cpu:+.0f}% CPU')} on this trace.")
    print(st.dim("  CPU-min = cores x minutes actually allocated. pod-min ignores pod size, so"))
    print(st.dim("  compare cost on CPU-min, not pod-min."))


def show_timelines(st: Style, runs: dict, ascii_only: bool) -> None:
    rule(st, "4.  WHAT THE CLUSTER DID")
    for key, label in (("CHIMERA", "CHIMERA"), ("HPA", "HPA")):
        tl = runs[key]["timeline"]
        if not tl:
            continue
        reps = [r for _, r, _, _ in tl]
        cpus = [c for _, _, c, _ in tl]
        print(f"  {st.bold(label)}")
        print(f"    replicas  {sparkline(reps, ascii_only=ascii_only)}  "
              f"{st.dim(f'{min(reps)}-{max(reps)} pods')}")
        print(f"    cpu/pod   {sparkline(cpus, ascii_only=ascii_only)}  "
              f"{st.dim(f'{min(cpus):.2f}-{max(cpus):.2f} cores')}")
        print()
    print(f"  {st.dim('HPA can only move the top line. CHIMERA moves both, and moves')}")
    print(f"  {st.dim('the fast one first while replicas are still booting.')}")


def show_dapca(st: Style, runs: dict, limit: int = 8) -> None:
    rule(st, "5.  THE CONTRIBUTION, STEP BY STEP")
    print("  Control steps where commands were still in flight. The buffer holds")
    print("  replica changes already ordered but not yet booted, so the controller")
    print("  does not re-order capacity that is already on its way.\n")

    # The timeline stores the buffer AFTER each decision; what the controller saw when it
    # decided is the previous row's buffer, and what it ordered is the new head slot.
    tl = runs["CHIMERA"]["timeline"]
    rows = []
    for i, (t, reps, cpu, buf) in enumerate(tl):
        seen = tl[i - 1][3] if i else [0.0] * len(buf)
        ordered = reps - (tl[i - 1][1] if i else CONFIGS['CHIMERA']['h_min'])
        if any(abs(x) > 1e-9 for x in seen) or abs(ordered) > 1e-9:
            rows.append((t, reps, cpu, seen, ordered))
    if not rows:
        print(st.dim("  No replica orders on this trace -- try --workload w_0002 or w_0005."))
        return

    print(st.dim(f"  {'t (s)':>7}{'replicas':>10}{'cpu/pod':>10}   {'in flight when deciding':<26}decision"))
    print(st.dim("  " + "-" * 70))
    for t, reps, cpu, seen, ordered in rows[:limit]:
        buf = "[" + ", ".join(f"{x:+.0f}" for x in seen) + "]"
        if abs(ordered) > 1e-9:
            what = f"order {ordered:+.0f} pods" if ordered > 0 else f"remove {-ordered:.0f} pod(s)"
        else:
            what = st.green("hold -- capacity already on its way")
        print(f"  {t:>7.0f}{reps:>10d}{cpu:>10.2f}   {buf:<26}{what}")
    if len(rows) > limit:
        print(st.dim(f"  ... {len(rows) - limit} more steps"))


# ----------------------------------------------------------------------------
# Section 6 -- the full evaluation, read straight off disk
# ----------------------------------------------------------------------------
RESULTS_CSV = ROOT / "results" / "results_v5.csv"

# What each system is *capable* of. Descriptive, not measured.
CAPABILITY = {
    "HPA":                  ("Kubernetes HPA",       "y", "-", "-", "-", "-"),
    "VPA":                  ("Kubernetes VPA",       "-", "y", "-", "-", "-"),
    "HPA+VPA":              ("HPA + VPA together",   "y", "y", "-", "-", "-"),
    "AAPA":                 ("AAPA (base paper)",    "y", "-", "-", "-", "~"),
    "MagicScaler-Inspired": ("MagicScaler-inspired", "y", "-", "-", "-", "~"),
    "KOSMOS":               ("KOSMOS (ICSOC 2021)",  "y", "y", "~", "-", "-"),
    "CHIMERA_v1":           ("CHIMERA v1 (Review 2)", "y", "y", "y", "y", "y"),
    "CHIMERA_v2":           ("CHIMERA v2",           "y", "y", "y", "y", "y"),
    "CHIMERA_v3":           ("CHIMERA v3",           "y", "y", "y", "y", "y"),
    "CHIMERA_v4":           ("CHIMERA v4",           "y", "y", "y", "y", "y"),
    "CHIMERA":              ("CHIMERA  (ours)",      "y", "y", "y", "y", "y"),
}
ROW_ORDER = ["HPA", "VPA", "HPA+VPA", "AAPA", "MagicScaler-Inspired", "KOSMOS", "CHIMERA_v1", "CHIMERA_v2",
             "CHIMERA_v3", "CHIMERA_v4", "CHIMERA"]


def read_results():
    """Per-configuration means over the five REAL traces, plus run/seed counts."""
    import csv
    if not RESULTS_CSV.exists():
        return {}, 0, 0
    acc, n_rows, seeds = {}, 0, set()
    with RESULTS_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            n_rows += 1
            seeds.add(row["seed"])
            if not row.get("workload_id", "").startswith("real_"):
                continue
            b = acc.setdefault(row["config_name"], {"n": 0, "slo": 0.0, "p99": 0.0, "cpu": 0.0, "act": 0.0})
            b["slo"] += float(row["slo_violation_rate"])
            b["p99"] += float(row["p99_response_time"])
            b["cpu"] += float(row["core_minutes"])
            b["act"] += float(row["scaling_actions"])
            b["n"] += 1
    data = {c: {k: (v / b["n"] if k != "n" else v) for k, v in b.items()} for c, b in acc.items() if b["n"]}
    return data, n_rows, len(seeds)


def show_comparison(st: Style) -> None:
    data, n_rows, n_seeds = read_results()
    rule(st, f"6.  THE FULL EVALUATION  ({n_rows} runs, read from disk)")
    if not data:
        print(st.dim(f"  {RESULTS_CSV.relative_to(ROOT)} not found -- run"))
        print(st.dim("  python src/scripts/run_full_evaluation.py  first."))
        return

    print(f"  Every controller, averaged over the five real Azure traces x {n_seeds} seeds.")
    print(st.dim("  H/V = can it scale horizontally / vertically.  coord = one coordinated"))
    print(st.dim("  decision.  delay = accounts for the 20 s pod boot.  unc = knows how"))
    print(st.dim("  sure its own forecast is.   y = yes,  ~ = partly,  - = no.\n"))

    head = (f"  {'system':<23}{'H':>2}{'V':>3}{'coord':>7}{'delay':>7}{'unc':>5}"
            f"{'SLO %':>8}{'p99 s':>8}{'CPU-min':>9}{'actions':>9}")
    print(st.dim(head))
    print(st.dim("  " + "-" * (len(head) - 2)))

    rows = [c for c in ROW_ORDER if c in data]
    best = {k: min(data[c][k] for c in rows) for k in ("slo", "p99", "cpu", "act")}
    for cfg in rows:
        label, h, v, co, de, un = CAPABILITY[cfg]
        d = data[cfg]
        cells = []
        for k, fmt in (("slo", "{:>7.2f}%"), ("p99", "{:>8.2f}"), ("cpu", "{:>9.1f}"), ("act", "{:>9.1f}")):
            val = d[k] * (100 if k == "slo" else 1)
            txt = fmt.format(val)
            cells.append(st.green(txt) if abs(d[k] - best[k]) < 1e-12 else txt)
        line = f"  {label:<23}{h:>2}{v:>3}{co:>7}{de:>7}{un:>5}" + "".join(cells)
        print(st.bold(line) if cfg == "CHIMERA" else line)

    print()
    print(f"  {st.green('green')} = best in that column.")
    ch = data.get("CHIMERA")
    if ch:
        rivals = [c for c in rows if not c.startswith("CHIMERA")]
        beaten_slo = [c for c in rivals if ch["slo"] < data[c]["slo"]]
        beaten_p99 = [c for c in rivals if ch["p99"] < data[c]["p99"]]
        print(f"  CHIMERA has lower SLO violations than {len(beaten_slo)}/{len(rivals)} baselines "
              f"and a lower p99 than {len(beaten_p99)}/{len(rivals)}.")
        for c in rivals:
            if c not in beaten_slo:
                d = data[c]
                print(f"  {CAPABILITY[c][0]}: {d['slo']*100:.2f}% vs our {ch['slo']*100:.2f}% SLO, "
                      f"at {d['cpu']:.0f} vs {ch['cpu']:.0f} CPU-min, p99 {d['p99']:.1f} s vs {ch['p99']:.1f} s.")
    print(st.dim("  Cost at equal SLO is compared properly by the frontier sweep:"))
    print(st.dim("  results/REPORT_v5.md and results/figures/v5/frontier_real.png"))


# ----------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="CHIMERA live demonstration")
    ap.add_argument("--workload", default="w_0004",
                    help="trace: w_0001..w_0005 (default: w_0004)")
    ap.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    ap.add_argument("--ascii", action="store_true", help="plain ASCII sparklines")
    ap.add_argument("--no-color", action="store_true", help="disable colour output")
    ap.add_argument("--table", action="store_true",
                    help="print only the full comparison table and exit (instant)")
    ap.add_argument("--no-table", action="store_true",
                    help="skip the comparison table at the end")
    args = ap.parse_args()

    st = Style(enabled=not args.no_color)

    if args.table:                       # table only, no simulation
        print()
        print(st.bold("  CHIMERA -- comparison against existing systems"))
        show_comparison(st)
        print()
        return

    item = load_workload(args.workload)
    trace = item['trace'][TEST_WINDOW[0]:TEST_WINDOW[1]]

    print()
    print(st.bold("  CHIMERA -- Delay-Aware Predictive Coupled Autoscaling"))
    print(st.dim("  Joint horizontal + vertical Kubernetes autoscaling under "
                 "asymmetric actuator delay"))

    rule(st, "0.  SETUP")
    print(f"  {st.dim('workload')}          {args.workload}  "
          f"({item['type']}, real Azure Functions trace)")
    print(f"  {st.dim('warm-up')}           minutes {TEST_WINDOW[0] - WARMUP}-{TEST_WINDOW[0]} simulated, not scored")
    print(f"  {st.dim('scored window')}     minutes {TEST_WINDOW[0]}-{TEST_WINDOW[1]}  "
          f"({len(trace)} min, peak {trace.max():.0f} req/min)")
    print(f"  {st.dim('SLO')}               {SLO_SECONDS*1000:.0f} ms per request")
    print(f"  {st.dim('actuator delays')}   horizontal {st.red('20.0 s')}  |  "
          f"vertical {st.green('0.1 s')}   -- a 200x asymmetry")
    print(f"  {st.dim('bounds')}            CPU {BOUNDS['v_min']}-{BOUNDS['v_max']} cores/pod,  "
          f"{BOUNDS['h_min']}-{BOUNDS['h_max']} replicas")
    print(f"  {st.dim('seed')}              {args.seed}  (identical for every controller)")

    show_control_law(st)

    rule(st, "2.  RUNNING FOUR CONTROLLERS")
    print(f"  {'forecaster':<18} ", end="", flush=True)
    t0 = time.perf_counter()
    cqr = harness.train_cqr_for_trace(item['trace'])
    print(st.green(f"trained on minutes 10-50, calibrated on 50-70  {time.perf_counter()-t0:.1f} s"))
    runs = {}
    for key, label, cfg in RUNS:
        print(f"  {label:<18} ", end="", flush=True)
        runs[key] = simulate(item, cfg, cqr, args.seed)
        print(st.green(f"done  {runs[key]['wall']:.1f} s"))

    show_results(st, runs)
    show_timelines(st, runs, args.ascii)
    show_dapca(st, runs)
    if not args.no_table:
        show_comparison(st)

    rule(st)
    print(f"  Full evaluation, statistics and cost/SLO frontier: "
          f"{st.cyan('results/REPORT_v5.md')}")
    print()


if __name__ == "__main__":
    main()
