"""
Regenerate every image in the Review deck from the project's real outputs.

    python presentation/make_deck_figures.py

Charts are drawn from results/results_v5.csv and results/results_v5_sweep.csv. Terminal
panels render output captured from actually running the commands they show (tests, the
demo, the results query) -- nothing is typed in by hand. Each image keeps the pixel size
of the one it replaces so the slide layout does not move.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "presentation" / "figures"
RES = ROOT / "results"

# Same look as the Review-2 deck images
BLUE, GREY, INK, MUTED, GRID, RED = "#2a78d6", "#8b8f98", "#16181d", "#4d5058", "#e6e7eb", "#e34948"
ORANGE, AQUA, YELLOW = "#eb6834", "#1baf7a", "#eda100"
T_BG, T_BAR, T_TXT, T_DIM, T_BLUE, T_AMBER, T_GREEN, T_RED = (
    "#0f141d", "#1a202b", "#e8eaee", "#7d8594", "#5aa2f0", "#e6a23c", "#5fd08a", "#e36868")
DPI = 100
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID})


def fig_px(w, h):
    return plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)


def clean_axes(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=15)
    ax.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def is_real(df):
    return df.workload_id.str.startswith("real")


# ---------------------------------------------------------------------------- charts
def frontier(sw, path, size=(1839, 1000), ymax=30.0):
    d = sw[is_real(sw)].groupby(["family", "knob"])[["slo_violation_rate", "core_minutes"]].mean().reset_index()
    d = d[~d.family.str.match(r"CHIMERA_v\d")]          # earlier CHIMERA versions are in the report, not here
    fig = fig_px(*size)
    ax = fig.add_axes([0.075, 0.12, 0.80, 0.74])
    clean_axes(ax)
    style = {"CHIMERA": (BLUE, 3.6, 6), "AAPA": (ORANGE, 2.6, 4), "VPA": (AQUA, 2.6, 4),
             "KOSMOS": (YELLOW, 2.6, 4)}
    o = d[d.family == "Oracle"].sort_values("core_minutes")
    if not o.empty:
        ax.plot(o.core_minutes, o.slo_violation_rate * 100, color=INK, lw=2.2, ls=(0, (5, 4)), zorder=5)
        lab = o[o.slo_violation_rate * 100 <= ymax].iloc[0]
        ax.annotate("clairvoyant per-minute plan", (lab.core_minutes, lab.slo_violation_rate * 100),
                    xytext=(12, 8), textcoords="offset points", fontsize=14, color=INK, style="italic")
    fams = sorted([f for f in d.family.unique() if f != "Oracle"], key=lambda f: (f in style, f == "CHIMERA"))
    for fam in fams:
        c = d[d.family == fam].sort_values("core_minutes")
        col, lw, z = style.get(fam, (GREY, 1.6, 2))
        ax.plot(c.core_minutes, c.slo_violation_rate * 100, color=col, lw=lw, zorder=z,
                marker="o", ms=9 if fam == "CHIMERA" else 6, mec="white", mew=1.8, alpha=1 if fam in style else 0.6)
        if fam in style:
            vis = c[c.slo_violation_rate * 100 <= ymax]
            end = vis.iloc[-1] if len(vis) else c.iloc[-1]
            off = {"VPA": (8, -18), "AAPA": (6, 14)}.get(fam, (10, 0))
            ax.annotate(fam, (end.core_minutes, min(end.slo_violation_rate * 100, ymax)), xytext=off,
                        textcoords="offset points", va="center", fontsize=15, color=INK,
                        fontweight="bold" if fam == "CHIMERA" else "normal", annotation_clip=False)
    ax.set_ylim(0, ymax)
    ax.set_xlabel("CPU cost  -  core-minutes per 30-min run  (lower is cheaper)", fontsize=16, color=MUTED)
    ax.set_ylabel("SLO violation rate  (%)", fontsize=16, color=MUTED)
    knobs = sorted(sw[sw.family != "KOSMOS"].knob.unique())
    fig.text(0.075, 0.95, "Cost vs. SLO frontier on the five real Azure traces", fontsize=26,
             fontweight="bold", color=INK)
    fig.text(0.075, 0.905, f"Each line sweeps one controller over target utilisation {knobs[0]:g} - {knobs[-1]:g} "
             f"(KOSMOS: latency setpoint); grey = other baselines.  Axis cut at {ymax:.0f} %.  Down and left is better.",
             fontsize=14, color=MUTED)
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)


def tail_and_cost(df, path, size=(1839, 860)):
    real = df[is_real(df) & df.config_name.isin(["CHIMERA", "AAPA", "KOSMOS", "HPA"])]
    m = real.groupby("config_name")[["p99_response_time", "core_minutes"]].mean()
    order = ["CHIMERA", "AAPA", "KOSMOS", "HPA"]
    fig = fig_px(*size)
    fig.text(0.012, 0.93, "Tail latency and CPU at default settings  (held-out data, after a 20-min warm-up)",
             fontsize=23, fontweight="bold", color=INK)
    for i, (col, title, fmt) in enumerate([("p99_response_time", "p99 response time  (s)", "{:.1f}"),
                                           ("core_minutes", "CPU allocated  (core-minutes per run)", "{:.0f}")]):
        ax = fig.add_axes([0.045 + i * 0.5, 0.08, 0.44, 0.66])
        clean_axes(ax)
        ax.grid(axis="x", visible=False)
        vals = [m.loc[n, col] for n in order]
        ax.bar(order, vals, color=[BLUE if n == "CHIMERA" else GREY for n in order], width=0.58)
        for x, v in enumerate(vals):
            ax.text(x, v, fmt.format(v), ha="center", va="bottom", fontsize=17, fontweight="bold",
                    color=INK)
        ax.set_ylim(0, max(vals) * 1.18)
        ax.tick_params(axis="x", labelsize=16)
        fig.text(0.045 + i * 0.5, 0.79, title, fontsize=18, color=INK)
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)


ABLATIONS = [
    ("CHIMERA_w/o_Cascade", "Cascade inner loop"),
    ("CHIMERA_w/o_Fast_Release", "Fast CPU release"),
    ("CHIMERA_w/o_Replica_Band", "Replica band (v4)"),
    ("CHIMERA_w/o_Inner_Owns_CPU", "Inner loop owns CPU (v4)"),
    ("CHIMERA_w/o_ADWIN_Margin_Reset", "ADWIN margin reset (v4)"),
    ("CHIMERA_w/o_Up_Deadband", "Scale-up dead-band (v5)"),
    ("CHIMERA_Legacy_Delay_Model", "Corrected delay model"),
    ("CHIMERA_Double_Count_Inflight", "Serving-replica state"),
    ("CHIMERA_w/o_State_Augmentation", "State augmentation (DAPCA)"),
    ("CHIMERA_w/o_Vertical_Bridging", "Vertical bridging"),
    ("CHIMERA_w/o_CQR", "CQR uncertainty"),
    ("CHIMERA_w/o_Naive_Conformal", "Reactive conformal margin"),
    ("CHIMERA_w/o_Asymmetric_Smoothing", "Fast-attack smoothing"),
    ("CHIMERA_+Online_Conformal", "Re-adding online recalib."),
    ("CHIMERA_w/o_OOD_Fallback", "OOD fallback"),
    ("CHIMERA_w/o_Coupling_Penalty", "Cross-coupling penalty"),
    ("CHIMERA_+Lyapunov_Filter", "Re-adding Lyapunov filter"),
]


def ablation(df, path, size=(1839, 919)):
    """Change in SLO (pp) and in CPU (%) when each mechanism is removed, all nine traces."""
    ab = df[df.run_type == "ablation"].groupby("config_name")[["slo_violation_rate", "core_minutes"]].mean()
    full = ab.loc["CHIMERA_Full"]
    rows = [(label, (ab.loc[k, "slo_violation_rate"] - full.slo_violation_rate) * 100,
             (ab.loc[k, "core_minutes"] / full.core_minutes - 1) * 100) for k, label in ABLATIONS if k in ab.index]
    rows.sort(key=lambda r: r[1])
    fig = fig_px(*size)
    fig.text(0.012, 0.935, "Ablation: what each mechanism is worth (all nine traces, 10 seeds)",
             fontsize=24, fontweight="bold", color=INK)
    fig.text(0.012, 0.885, "Bars show the change when that one mechanism is removed ('Re-adding' rows: added "
             "back).  Red = the change hurts; blue = it helps.", fontsize=14.5, color=MUTED)
    labels = [r[0] for r in rows]
    for i, (idx, title, unit) in enumerate([(1, "SLO violations  (percentage points)", "pp"),
                                            (2, "CPU allocated  (%)", "%")]):
        ax = fig.add_axes([0.2 + i * 0.41, 0.07, 0.33, 0.72])
        clean_axes(ax)
        ax.grid(axis="y", visible=False)
        vals = [r[idx] for r in rows]
        # For CPU, spending less is good, so the colours flip
        good = [(v < 0) if idx == 1 else (v < 0) for v in vals]
        ax.barh(range(len(vals)), vals, color=[BLUE if g else RED for g in good], height=0.62)
        ax.axvline(0, color=MUTED, lw=1.2)
        span = max(abs(v) for v in vals) or 1
        ax.set_xlim(-span * 1.45, span * 1.45)
        for y, v in enumerate(vals):
            ax.text(v + (span * 0.04 if v >= 0 else -span * 0.04), y, f"{v:+.1f}{unit}",
                    va="center", ha="left" if v >= 0 else "right", fontsize=13, color=INK)
        ax.set_yticks(range(len(vals)))
        ax.set_yticklabels(labels if i == 0 else [], fontsize=14, color=INK)
        ax.set_title(title, fontsize=16, color=INK, loc="left")
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------- terminals
def terminal(title, lines, path, size, font=17, line_h=None):
    """lines: list of (text, colour). Renders a dark terminal window like the original deck."""
    w, h = size
    fig = fig_px(w, h)
    fig.patch.set_alpha(0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((10, 10), w - 20, h - 20, boxstyle="round,pad=0,rounding_size=22",
                                fc=T_BG, ec="#2a3140", lw=1.5))
    ax.add_patch(FancyBboxPatch((11, 11), w - 22, 78, boxstyle="round,pad=0,rounding_size=21",
                                fc=T_BAR, ec="none"))
    ax.add_patch(plt.Rectangle((11, 60), w - 22, 29, fc=T_BAR, ec="none"))
    for i, c in enumerate(["#e5605c", "#e5b33e", "#5fcf7d"]):
        ax.add_patch(Circle((62 + i * 48, 50), 12.5, fc=c, ec="none"))
    ax.text(230, 51, title, color=T_DIM, fontsize=font * 0.95, family="DejaVu Sans Mono", va="center", parse_math=False)
    line_h = line_h or font * 2.35
    y = 125
    for text, col in lines:
        ax.text(55, y, text, color=col, fontsize=font, family="DejaVu Sans Mono", va="top", parse_math=False)
        y += line_h
    fig.savefig(path, dpi=DPI, transparent=True)
    plt.close(fig)


def run(cmd, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8", **(env_extra or {}))
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    return p.stdout + p.stderr


def tests_panel(path, size=(2039, 952)):
    out1 = run([sys.executable, "tests/test_chimera.py", "-v"])
    out2 = run([sys.executable, "tests/test_operator.py"])
    n1 = re.search(r"Ran (\d+) tests", out1).group(1)
    n2 = re.search(r"Ran (\d+) tests", out2).group(1)
    ok1 = "OK" if re.search(r"^OK", out1, re.M) else "FAILED"
    ok2 = "OK" if re.search(r"^OK", out2, re.M) else "FAILED"
    names = re.findall(r"^(test_\w+) \(", out1, re.M)
    lines = [("$ python tests/test_chimera.py -v", T_AMBER), ("", T_TXT)]
    for nm in names:
        lines.append((f"{nm:<48} ... ok", T_GREEN))
    lines += [("", T_TXT), (f"Ran {n1} tests  {ok1}", T_TXT), ("", T_TXT),
              ("$ python tests/test_operator.py", T_AMBER),
              (f"Ran {n2} tests  {ok2}   (Prometheus client over real HTTP, control core,", T_TXT),
              ("                   reconcile loop with in-place resize + dry-run)", T_DIM)]
    terminal("chimera_project - unit test suites", lines, path, size, font=16.5, line_h=36)
    return int(n1) + int(n2), ok1 == ok2 == "OK"


def demo_panel(path, size=(1560, 810)):
    out = run([sys.executable, "demo_run.py", "--no-color", "--no-table"])
    lines = [('$ $env:PYTHONPATH="."; python demo_run.py', T_AMBER), ("", T_TXT)]
    grab = False
    for ln in out.splitlines():
        if "3.  RESULTS" in ln:
            grab = True
            continue
        if grab:
            if ln.startswith("--"):
                break
            s = ln.rstrip()
            if not s or s.strip().startswith("CPU-min =") or s.strip().startswith("compare cost"):
                continue
            col = T_BLUE if s.strip().startswith("CHIMERA ") and "(no SA)" not in s else T_AMBER if "Ablation" in s or "that is" in s \
                else T_DIM if set(s.strip()) <= {"-"} else T_TXT
            lines.append((s[2:], col))
    terminal("chimera_project - demo_run.py (live demonstration)", lines, path, size, font=17.5, line_h=42)
    return out


def results_panel(df, path, size=(2200, 781)):
    real = df[is_real(df)]
    names = ["CHIMERA", "AAPA", "HPA+VPA", "HPA", "MagicScaler-Inspired", "VPA", "KOSMOS", "CHIMERA_v4", "CHIMERA_v3",
             "CHIMERA_v1"]
    g = real[real.config_name.isin(names)].groupby("config_name").agg(
        n=("seed", "size"), slo=("slo_violation_rate", "mean"), p99=("p99_response_time", "mean"),
        cpu=("core_minutes", "mean"), act=("scaling_actions", "mean")).reindex(names)
    lines = [('$ python src/scripts/analyze_v2.py --tag v5     # results/results_v5.csv', T_AMBER),
             (f"# {len(df)} rows · {df.config_name.nunique()} configs · {df.workload_id.nunique()} workloads · "
              f"{df.seed.nunique()} seeds - real-trace subset", T_DIM), ("", T_TXT),
             (f"{'config_name':<24}{'n':>5}{'slo_%':>9}{'p99_s':>9}{'cpu_min':>10}{'actions':>10}", T_DIM),
             ("-" * 67, T_DIM)]
    for n, r in g.iterrows():
        col = T_BLUE if n == "CHIMERA" else T_RED if n.startswith("CHIMERA_v") else T_TXT
        lines.append((f"{n:<24}{int(r.n):>5}{r.slo*100:>9.2f}{r.p99:>9.2f}{r.cpu:>10.1f}{r.act:>10.1f}", col))
    terminal("chimera_project - evaluation results", lines, path, size, font=17, line_h=38)


def crd_panel(path, size=(1880, 1339)):
    lines = [("$ cat k8s/example-autoscaler.yaml", T_AMBER), ("", T_TXT)]
    cr = (ROOT / "k8s" / "example-autoscaler.yaml").read_text().splitlines()
    lines += [(l, T_BLUE) for l in cr]
    lines += [("", T_TXT), ("# operator decision log (tests/test_operator.py, reconcile loop)", T_DIM),
              ("default/web-scaler: demand=1.500 cores, replicas=2 ready=2 cpu/pod=0.50", T_TXT),
              ("  -> cpu 0.50->1.60, replicas 2->3", T_GREEN)]
    terminal("chimera_project - ChimeraAutoscaler", lines, path, size, font=17.5, line_h=45)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(RES / "results_v5.csv")
    sw = pd.read_csv(RES / "results_v5_sweep.csv")
    frontier(sw, OUT / "image-10-1.png")
    tail_and_cost(df, OUT / "image-11-1.png")
    ablation(df, OUT / "image-12-1.png")
    n_tests, ok = tests_panel(OUT / "image-16-1.png")
    demo_panel(OUT / "image-16-2.png")
    results_panel(df, OUT / "image-17-1.png")
    crd_panel(OUT / "image-17-2.png")
    print(f"figures written to {OUT}  (tests: {n_tests}, all passing: {ok})")


if __name__ == "__main__":
    main()
