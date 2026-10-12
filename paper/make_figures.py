"""
Figures for the paper, as vector PDFs in paper/figures/.

    python paper/make_figures.py

fig_architecture.pdf  the two loops and what each owns
fig_frontiers.pdf     cost/SLO frontiers on the three verdict sets
fig_robustness.pdf    equal-CPU advantage under other latency models
fig_replay.pdf        live replay: violations against CPU, per seed, with the HPA frontier
                      (only if results/replay3 exists)
"""

import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.scripts.analyze_v2 import C_AAPA, C_CHIMERA, C_GRID, C_KOSMOS, C_MUTED, C_OTHER, C_TEXT, C_VPA  # noqa: E402

OUT = ROOT / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.linewidth": 0.6, "pdf.fonttype": 42})
NAMED = {"CHIMERA": (C_CHIMERA, "o", 1.8), "AAPA": (C_AAPA, "s", 1.3), "KOSMOS": (C_KOSMOS, "^", 1.3),
         "HPA": (C_VPA, "D", 1.3)}
LABEL = {"MagicScaler-Inspired": "MagicScaler-style"}


def clean(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(color=C_GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.tick_params(length=2, colors=C_MUTED)


def frontiers():
    panels = [("results/results_v6_sweep.csv", "real_kswd", "(a) Azure Functions excerpts"),
              ("results/results_ext_sweep.csv", "real_ext", "(b) NASA and ClarkNet web logs"),
              ("results/results_hw_sweep.csv", "real_hw", "(c) Huawei serverless functions")]
    panels = [p for p in panels if (ROOT / p[0]).exists()]
    fig, axes = plt.subplots(1, len(panels), figsize=(7.1, 2.4), sharey=False)
    for ax, (path, prefix, title) in zip(np.atleast_1d(axes), panels):
        sw = pd.read_csv(ROOT / path)
        sw = sw[sw.workload_id.str.startswith(prefix)]
        g = sw.groupby(["family", "knob"])[["core_minutes", "slo_violation_rate"]].mean().reset_index()
        for fam in sorted(g.family.unique(), key=lambda f: (f in NAMED, f == "CHIMERA")):
            if re.match(r"CHIMERA_v\d", fam) or fam == "Oracle":
                continue
            c = g[g.family == fam].sort_values("core_minutes")
            col, mk, lw = NAMED.get(fam, (C_OTHER, ".", 0.9))
            ax.plot(c.core_minutes, 100 * c.slo_violation_rate, color=col, lw=lw, marker=mk, ms=3,
                    label=LABEL.get(fam, fam), zorder=3 if fam in NAMED else 2)
        clean(ax)
        ax.set_title(title, fontsize=8, loc="left", color=C_TEXT)
        ax.set_xlabel("CPU cost (core-minutes)", color=C_MUTED)
        ax.set_ylim(0, 30)
    np.atleast_1d(axes)[0].set_ylabel("SLO violations (%)", color=C_MUTED)
    h, l = np.atleast_1d(axes)[0].get_legend_handles_labels()
    order = sorted(range(len(l)), key=lambda i: (l[i] != "CHIMERA", l[i] not in NAMED, l[i]))
    fig.legend([h[i] for i in order], [l[i] for i in order], loc="lower center", ncol=7, frameon=False,
               fontsize=7, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(OUT / "fig_frontiers.pdf")
    plt.close(fig)


def robustness():
    md = (ROOT / "results/robustness/ROBUSTNESS.md").read_text(encoding="utf-8")
    rows = []
    for line in md.splitlines():
        m = re.match(r"\| (\w+) \| (\w+) \| (\w+) \| [^|]+\| (\d+)/(\d+) \| ([+-][\d.]+) \| [^|]+\| (\d+)/(\d+), ([+-][\d.]+) \|", line)
        if m:
            rows.append((m.group(2), m.group(3), int(m.group(4)), int(m.group(5)), float(m.group(6)), float(m.group(9))))
    dist = {"exponential": "exp", "lognormal": "logn", "gamma4": "gamma", "bimodal": "bimod"}
    law = {"inverse": "A", "kingman": "B", "steep": "C"}
    lab = [f"{dist[d]}, {law[s]}" for d, s, *_ in rows]
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(3.45, 2.3))
    ax.bar(x - 0.2, [r[4] for r in rows], 0.38, color=C_CHIMERA, label="against the six baselines (mean)")
    ax.bar(x + 0.2, [r[5] for r in rows], 0.38, color=C_KOSMOS, label="risk pricing against a fixed target")
    for i, r in enumerate(rows):
        ax.text(i - 0.2, r[4] + 0.12, f"{r[2]}/{r[3]}", ha="center", fontsize=5.5, color=C_TEXT)
    ax.axhline(0, color=C_TEXT, lw=0.5)
    clean(ax)
    ax.set_xticks(x)
    ax.set_xticklabels(lab, fontsize=6, rotation=35, ha="right")
    ax.set_ylabel("SLO advantage at equal CPU (pp)", color=C_MUTED)
    ax.legend(frameon=False, fontsize=6, loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT / "fig_robustness.pdf")
    plt.close(fig)


def replay():
    d = ROOT / "results/replay3"
    files = sorted(d.glob("seed*/result_*.json")) if d.exists() else []
    if not files:
        return
    res = [json.loads(f.read_text()) for f in files]
    seeds = sorted({r["seed"] for r in res})
    fig, ax = plt.subplots(figsize=(3.45, 2.4))
    for i, s in enumerate(seeds):
        h = sorted((r["core_minutes"], 100 * r["slo_violation_rate"]) for r in res
                   if r["seed"] == s and r["mode"].startswith("hpa"))
        ax.plot([p[0] for p in h], [p[1] for p in h], color=C_VPA, lw=1.0, marker="D", ms=3,
                label="HPA at 70, 50, 35 %" if i == 0 else None)
        c = sorted((r["core_minutes"], 100 * r["slo_violation_rate"]) for r in res
                   if r["seed"] == s and r["mode"].startswith("chimera"))
        ax.plot([p[0] for p in c], [p[1] for p in c], color=C_CHIMERA, lw=1.4, marker="o", ms=4,
                label="CHIMERA at 0.46, 0.35, 0.25" if i == 0 else None, zorder=4)
    clean(ax)
    ax.set_xlabel("CPU allocated (core-minutes)", color=C_MUTED)
    ax.set_ylabel("Requests over the objective (%)", color=C_MUTED)
    ax.legend(frameon=False, fontsize=6, loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT / "fig_replay.pdf")
    plt.close(fig)


def architecture():
    fig, ax = plt.subplots(figsize=(3.45, 2.75))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 8)
    ax.axis("off")

    def box(x, y, w, h, text, fc="#ffffff", fs=5.4):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.1", fc=fc, ec=C_TEXT, lw=0.6))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=C_TEXT, linespacing=1.15)

    def arrow(a, b):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=6, lw=0.6, color=C_TEXT,
                                     shrinkA=0, shrinkB=0))

    ax.text(0.05, 7.7, "Outer loop, every 15 s: owns replicas", fontsize=6.2, color=C_TEXT, fontweight="bold")
    xs, w = [0.05, 2.55, 5.05, 7.55], 2.2
    for x, t in zip(xs, ["Forecast and\nconformal\nscenarios", "Risk-priced\ncapacity", "Target split,\nreplica band",
                         "Regulator with\nin-flight state"]):
        box(x, 6.1, w, 1.3, t)
    for x in xs[:-1]:
        arrow((x + w, 6.75), (x + w + 0.3, 6.75))

    ax.text(0.05, 5.35, "Inner loop, every 5 s: owns CPU per pod", fontsize=6.2, color=C_TEXT, fontweight="bold")
    box(0.05, 3.75, 3.1, 1.3, "Capacity for\nthe replicas\nserving now")
    box(3.45, 3.75, 5.3, 1.3, "Raise at once past a dead-band\nor on a significant surge;\nrelease after two quiet ticks")
    arrow((3.15, 4.4), (3.45, 4.4))

    box(0.05, 0.4, 4.6, 1.6, "CPU per pod\nin-place resize, about 0.1 s", fc="#f1f0ec", fs=5.8)
    box(5.2, 0.4, 4.75, 1.6, "Replicas\nscale API, tens of seconds", fc="#f1f0ec", fs=5.8)
    arrow((4.0, 3.75), (4.0, 2.0))
    ax.text(4.15, 2.8, "CPU changes", fontsize=5.2, color=C_MUTED, va="center")
    arrow((9.35, 6.1), (9.35, 2.0))
    ax.text(9.2, 2.8, "replica\nchanges", fontsize=5.2, color=C_MUTED, va="center", ha="right")
    fig.savefig(OUT / "fig_architecture.pdf", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


if __name__ == "__main__":
    architecture()
    frontiers()
    robustness()
    replay()
    print("figures:", ", ".join(sorted(p.name for p in OUT.glob("*.pdf"))))
