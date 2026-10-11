"""
Statistics, cost/SLO frontier and figures for the v2 evaluation.

    python src/scripts/analyze_v2.py

    python src/scripts/analyze_v2.py --tag v3

Reads  results/results_<tag>.csv (main comparison) and results/results_<tag>_sweep.csv (frontier)
Writes results/REPORT_<tag>.md, results/stats_<tag>.csv, results/frontier_<tag>.csv,
       results/matched_cost_<tag>.csv, results/oracle_gap_<tag>.csv, results/figures/<tag>/*.png

Statistics
  * Unit of analysis: one (workload, seed) run. CHIMERA and each baseline ran the same
    trace with the same seed, so runs are paired. Two-sided Wilcoxon signed-rank test,
    Holm-corrected across all comparisons in a table; effect size = Cohen's d_z on the
    paired differences. Workload-level win counts are reported alongside, because runs
    on the same trace are not independent of each other.
  * Frontier: every controller is swept over its own conservatism knob (target
    utilisation; KOSMOS's latency setpoint alpha). At each CHIMERA sweep point, each
    baseline's SLO is linearly interpolated at the same core-minute cost (only inside
    the range that baseline actually covered -- no extrapolation).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = Path("results")
FIG = RES / "figures"
BASELINES = ['HPA', 'VPA', 'HPA+VPA', 'AAPA', 'MagicScaler-Inspired', 'KOSMOS', 'CHIMERA_v1', 'CHIMERA_v2',
             'CHIMERA_v3', 'CHIMERA_v4']
ORACLE = 'Oracle'   # minute-level clairvoyant reference, never a competitor
METRICS = [('slo_violation_rate', 'SLO violations', 100, '%'),
           ('p99_response_time', 'p99 latency', 1, 's'),
           ('core_minutes', 'CPU cost', 1, 'core-min'),
           ('scaling_actions', 'scaling actions', 1, '')]
SETS = {'real': 'five real Azure Functions traces', 'synthetic': 'four synthetic stress traces',
        'all': 'all nine traces'}

# Reference palette (dataviz skill, light mode): slots 1-4 for the four named series; neutral for the rest
C_CHIMERA, C_AAPA, C_VPA, C_KOSMOS = '#2a78d6', '#eb6834', '#1baf7a', '#eda100'
C_OTHER, C_TEXT, C_MUTED, C_GRID, C_SURFACE = '#9a9992', '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
FAMILY_COLOR = {'CHIMERA': C_CHIMERA, 'AAPA': C_AAPA, 'VPA': C_VPA, 'KOSMOS': C_KOSMOS}


def subset(df, which):
    real = df.workload_id.str.startswith('real')
    return df[real] if which == 'real' else df[~real] if which == 'synthetic' else df


def holm(pvals):
    p = np.asarray(pvals, dtype=float)
    order = np.argsort(p)
    adj = np.empty_like(p)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p) - rank) * p[i]))
        adj[i] = running
    return adj


def paired_tests(df):
    rows = []
    for which in SETS:
        d = subset(df, which)
        ours = d[d.config_name == 'CHIMERA'].set_index(['workload_id', 'seed'])
        for base in BASELINES:
            theirs = d[d.config_name == base].set_index(['workload_id', 'seed'])
            idx = ours.index.intersection(theirs.index)
            for col, label, scale, unit in METRICS:
                a, b = ours.loc[idx, col].to_numpy(), theirs.loc[idx, col].to_numpy()
                diff = a - b
                try:
                    p = wilcoxon(a, b).pvalue if np.any(diff != 0) else 1.0
                except ValueError:
                    p = 1.0
                sd = diff.std(ddof=1)
                wl = (ours.loc[idx, col].groupby(level=0).mean()
                      - theirs.loc[idx, col].groupby(level=0).mean())
                rows.append(dict(set=which, baseline=base, metric=col, label=label, n=len(idx),
                                 chimera=a.mean() * scale, baseline_mean=b.mean() * scale,
                                 diff=diff.mean() * scale, d_z=diff.mean() / sd if sd > 0 else 0.0,
                                 p=p, workload_wins=int((wl < 0).sum()), workloads=len(wl)))
    st = pd.DataFrame(rows)
    st['p_holm'] = np.nan
    for (which, metric), g in st.groupby(['set', 'metric']):
        st.loc[g.index, 'p_holm'] = holm(g.p.to_numpy())
    return st


def frontier_table(sw):
    out = []
    for which in SETS:
        d = subset(sw, which)
        g = d.groupby(['family', 'knob'])[['slo_violation_rate', 'core_minutes', 'p99_response_time']].mean()
        out.append(g.reset_index().assign(set=which))
    return pd.concat(out, ignore_index=True)


def matched_cost(fr):
    """At each CHIMERA sweep point: every baseline's SLO interpolated at the same cost."""
    rows = []
    for which in SETS:
        f = fr[fr.set == which]
        ours = f[f.family == 'CHIMERA'].sort_values('core_minutes')
        for fam in [b for b in f.family.unique() if b not in ('CHIMERA', ORACLE)]:
            c = f[f.family == fam].sort_values('core_minutes')
            x, y = c.core_minutes.to_numpy(), c.slo_violation_rate.to_numpy()
            p99 = c.p99_response_time.to_numpy()
            # a frontier is monotone: keep the lower envelope so interpolation is well defined
            y = np.minimum.accumulate(y)
            for _, r in ours.iterrows():
                if x.min() <= r.core_minutes <= x.max():
                    their = float(np.interp(r.core_minutes, x, y))
                    rows.append(dict(set=which, baseline=fam, chimera_knob=r.knob, cost=r.core_minutes,
                                     chimera_slo=r.slo_violation_rate * 100, baseline_slo=their * 100,
                                     gap_pp=(their - r.slo_violation_rate) * 100,
                                     chimera_p99=r.p99_response_time,
                                     baseline_p99=float(np.interp(r.core_minutes, x, p99))))
    return pd.DataFrame(rows)


def per_trace_matched(sw):
    """Equal-cost comparison trace by trace: at each CHIMERA sweep point on a trace, every
    baseline's SLO on THAT trace interpolated at the same CPU (inside its own range).
    One row per (trace, baseline): mean gap, positive = CHIMERA better."""
    rows = []
    for wid, g in sw.groupby('workload_id'):
        c = g[g.family == 'CHIMERA'].groupby('knob')[['core_minutes', 'slo_violation_rate']].mean()
        for fam in BASELINES:
            if fam.startswith('CHIMERA'):
                continue
            b = g[g.family == fam].groupby('knob')[['core_minutes', 'slo_violation_rate']].mean()
            if b.empty:
                continue
            b = b.sort_values('core_minutes')
            x, y = b.core_minutes.to_numpy(), np.minimum.accumulate(b.slo_violation_rate.to_numpy())
            gaps = [(np.interp(r.core_minutes, x, y) - r.slo_violation_rate) * 100
                    for _, r in c.iterrows() if x.min() <= r.core_minutes <= x.max()]
            if gaps:
                rows.append(dict(workload_id=wid, baseline=fam, points=len(gaps), gap_pp=float(np.mean(gaps)),
                                 set='real' if wid.startswith('real') else 'synthetic'))
    return pd.DataFrame(rows)


def equal_cpu_tests(sw):
    """Significance of the equal-CPU comparison. For every (trace, seed): CHIMERA's sweep
    points for that seed, each baseline's SLO for the same seed interpolated at CHIMERA's
    CPU (inside the baseline's own range), averaged over the points. Runs with the same seed
    see the same request stream, so the gaps are paired. One-sample Wilcoxon signed-rank on
    the gaps, Holm-corrected across baselines within each set."""
    rows = []
    for (wid, seed), g in sw.groupby(['workload_id', 'seed']):
        c = g[g.family == 'CHIMERA'].set_index('knob')[['core_minutes', 'slo_violation_rate']]
        for fam in BASELINES:
            if fam.startswith('CHIMERA'):
                continue
            b = g[g.family == fam].sort_values('core_minutes')
            if b.empty:
                continue
            x, y = b.core_minutes.to_numpy(), b.slo_violation_rate.to_numpy()
            gaps = [(np.interp(r.core_minutes, x, y) - r.slo_violation_rate) * 100
                    for _, r in c.iterrows() if x.min() <= r.core_minutes <= x.max()]
            if gaps:
                rows.append(dict(workload_id=wid, seed=seed, baseline=fam, gap_pp=float(np.mean(gaps)),
                                 set='real' if wid.startswith('real') else 'synthetic'))
    d = pd.DataFrame(rows)
    out = []
    for which in ('real', 'synthetic', 'all'):
        dd = d if which == 'all' else d[d.set == which]
        for fam, g in dd.groupby('baseline'):
            gp = g.gap_pp.to_numpy()
            try:
                p = wilcoxon(gp).pvalue if np.any(gp != 0) and len(gp) > 1 else 1.0
            except ValueError:
                p = 1.0
            out.append(dict(set=which, baseline=fam, n=len(gp), traces=g.workload_id.nunique(),
                            mean_gap=gp.mean(), p=p,
                            trace_wins=int((g.groupby('workload_id').gap_pp.mean() > 0).sum())))
    t = pd.DataFrame(out)
    t['p_holm'] = np.nan
    for which, g in t.groupby('set'):
        t.loc[g.index, 'p_holm'] = holm(g.p.to_numpy())
    return t


def oracle_gap(fr):
    """Each controller's SLO minus the minute-level clairvoyant reference's SLO at the same
    CPU cost, averaged over the controller's sweep points inside the reference's cost range."""
    rows = []
    for which in SETS:
        f = fr[fr.set == which]
        o = f[f.family == ORACLE].sort_values('core_minutes')
        if o.empty:
            continue
        ox, oy = o.core_minutes.to_numpy(), o.slo_violation_rate.to_numpy()
        for fam in f.family.unique():
            if fam == ORACLE:
                continue
            c = f[f.family == fam]
            inside = c[(c.core_minutes >= ox.min()) & (c.core_minutes <= ox.max())]
            if inside.empty:
                continue
            gaps = (inside.slo_violation_rate.to_numpy() - np.interp(inside.core_minutes, ox, oy)) * 100
            rows.append(dict(set=which, family=fam, points=len(gaps), gap_pp=gaps.mean()))
    return pd.DataFrame(rows)


def style_axes(ax):
    ax.set_facecolor(C_SURFACE)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(C_GRID)
    ax.tick_params(colors=C_MUTED, labelsize=9)
    ax.grid(True, color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_frontier(fr, which, path):
    f = fr[fr.set == which]
    fig, ax = plt.subplots(figsize=(8, 5.2), dpi=160)
    fig.patch.set_facecolor(C_SURFACE)
    style_axes(ax)
    o = f[f.family == ORACLE].sort_values('core_minutes')
    if not o.empty:
        ax.plot(o.core_minutes, o.slo_violation_rate * 100, color=C_TEXT, lw=1.6, ls=(0, (4, 3)), zorder=4,
                label='Clairvoyant per-minute plan')
    order = sorted([x for x in f.family.unique() if x != ORACLE],
                   key=lambda s: (s in FAMILY_COLOR, s == 'CHIMERA'))
    for fam in order:
        c = f[f.family == fam].sort_values('core_minutes')
        col = FAMILY_COLOR.get(fam, C_OTHER)
        lw, z = (2.4, 5) if fam == 'CHIMERA' else (2.0, 3) if fam in FAMILY_COLOR else (1.4, 2)
        ax.plot(c.core_minutes, c.slo_violation_rate * 100, color=col, lw=lw, zorder=z,
                marker='o', ms=5, mec=C_SURFACE, mew=1.5, label=fam)
        if fam in FAMILY_COLOR:   # direct labels for the highlighted series; legend names all
            last = c.iloc[-1]
            ax.annotate(fam, (last.core_minutes, last.slo_violation_rate * 100),
                        xytext=(6, 0), textcoords='offset points', va='center', fontsize=9,
                        color=C_TEXT, fontweight='bold' if fam == 'CHIMERA' else 'normal')
    ax.set_xlabel('CPU cost (core-minutes per 30-min run, lower is cheaper)', color=C_MUTED, fontsize=9.5)
    ax.set_ylabel('SLO violations (%) - lower is better', color=C_MUTED, fontsize=9.5)
    ax.set_title(f'Cost vs SLO frontier, {SETS[which]}', loc='left', color=C_TEXT, fontsize=12,
                 fontweight='bold', pad=22)
    ax.text(0, 1.015, 'Each line sweeps one controller over its conservatism knob; down-and-left is better',
            transform=ax.transAxes, color=C_MUTED, fontsize=8.5, va='bottom')
    ax.set_ylim(bottom=0)
    h, l = ax.get_legend_handles_labels()
    order_idx = sorted(range(len(l)), key=lambda i: (l[i] != 'CHIMERA', l[i] != 'Clairvoyant per-minute plan',
                                                    l[i] not in FAMILY_COLOR, l[i]))
    ax.legend([h[i] for i in order_idx], [l[i] for i in order_idx], frameon=False, fontsize=8,
              loc='upper right', labelcolor=C_MUTED, ncol=2)
    fig.tight_layout()
    fig.savefig(path, facecolor=C_SURFACE)
    plt.close(fig)


def plot_bars(tab, col, title, unit, path, scale=1.0, log=False):
    t = tab.sort_values(col)
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=160)
    fig.patch.set_facecolor(C_SURFACE)
    style_axes(ax)
    ax.grid(axis='y', visible=False)
    cols = [FAMILY_COLOR.get(n, C_OTHER) for n in t.index]
    ax.barh(t.index, t[col] * scale, color=cols, height=0.62, edgecolor=C_SURFACE, linewidth=2)
    for i, v in enumerate(t[col] * scale):
        ax.text(v, i, f'  {v:.2f}{unit}' if v < 10 else f'  {v:.1f}{unit}', va='center', fontsize=8.5, color=C_TEXT)
    if log:
        ax.set_xscale('log')
    ax.set_title(title, loc='left', color=C_TEXT, fontsize=12, fontweight='bold')
    ax.tick_params(axis='y', colors=C_TEXT)
    fig.tight_layout()
    fig.savefig(path, facecolor=C_SURFACE)
    plt.close(fig)


def fmt_p(p):
    return '<0.001' if p < 0.001 else f'{p:.3f}'


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='v3', help='results/results_<tag>.csv')
    tag = ap.parse_args().tag
    global FIG, BASELINES
    FIG = RES / 'figures' / tag
    FIG.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(RES / f'results_{tag}.csv')
    sw_path = RES / f'results_{tag}_sweep.csv'
    sw = pd.read_csv(sw_path) if sw_path.exists() else None
    BASELINES = [b for b in BASELINES if b in set(df.config_name)]

    names = BASELINES + ['CHIMERA']
    cols = ['slo_violation_rate', 'p99_response_time', 'core_minutes', 'pod_minutes', 'scaling_actions']
    summary = {w: subset(df, w)[subset(df, w).config_name.isin(names)].groupby('config_name')[cols].mean()
               for w in SETS}
    stats = paired_tests(df)
    stats.to_csv(RES / f'stats_{tag}.csv', index=False)

    L = [f"# CHIMERA {tag} - evaluation report", "",
         f"Generated by `src/scripts/analyze_v2.py --tag {tag}` from `results/results_{tag}.csv` "
         f"({len(df)} runs: {df.config_name.nunique()} configurations x {df.workload_id.nunique()} workloads x "
         f"{df.seed.nunique()} seeds)" + (f" and `results/results_{tag}_sweep.csv` ({len(sw)} runs)." if sw is not None else "."),
         "", "All controllers share the same resource bounds (0.5 core/pod start, >= 2 replicas), the same "
         "traces and the same seeds. In the headline tables each runs at its own default setting (baselines: "
         "70 % target utilisation; CHIMERA: its configured default), so the like-for-like comparison is the "
         "equal-CPU frontier analysis further down. Scored window: minutes 70-100.", ""]

    for w in SETS:
        s = summary[w].sort_values('slo_violation_rate')
        L += [f"## Headline comparison - {SETS[w]}", "",
              "| Controller | SLO viol. | p99 (s) | CPU (core-min) | pod-min | actions |",
              "|---|---:|---:|---:|---:|---:|"]
        for n, r in s.iterrows():
            b = '**' if n == 'CHIMERA' else ''
            L.append(f"| {b}{n}{b} | {r.slo_violation_rate*100:.2f}% | {r.p99_response_time:.2f} | "
                     f"{r.core_minutes:.1f} | {r.pod_minutes:.1f} | {r.scaling_actions:.1f} |")
        L.append("")
        L += [f"Paired tests, CHIMERA minus baseline ({SETS[w]}). Negative = CHIMERA lower. "
              "p = Wilcoxon signed-rank, Holm-corrected within each metric. "
              "'wins' = traces where CHIMERA's seed-mean is lower.", "",
              "| vs | SLO diff (pp) | p | p99 diff (s) | p | CPU diff | p | SLO wins |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        sw_ = stats[stats.set == w]
        for base in BASELINES:
            g = sw_[sw_.baseline == base].set_index('metric')
            L.append(f"| {base} | {g.loc['slo_violation_rate','diff']:+.2f} | {fmt_p(g.loc['slo_violation_rate','p_holm'])} | "
                     f"{g.loc['p99_response_time','diff']:+.2f} | {fmt_p(g.loc['p99_response_time','p_holm'])} | "
                     f"{g.loc['core_minutes','diff']:+.1f} | {fmt_p(g.loc['core_minutes','p_holm'])} | "
                     f"{g.loc['slo_violation_rate','workload_wins']}/{g.loc['slo_violation_rate','workloads']} |")
        L.append("")

    # Ablation
    ab = df[df.run_type == 'ablation']
    L += ["## Ablation (each row removes (w/o) or re-adds (+) one mechanism of CHIMERA)", "",
          "'identical' = share of runs whose SLO, CPU, p99 and action counts match CHIMERA_Full "
          "exactly; 100 % means the mechanism is inert in this design.", "",
          "| Variant | real SLO | real p99 | real CPU | synthetic SLO | synthetic CPU | resizes/run | "
          "pod changes/run | identical |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    ar, asy = subset(ab, 'real').groupby('config_name').mean(numeric_only=True), \
        subset(ab, 'synthetic').groupby('config_name').mean(numeric_only=True)
    allm = ab.groupby('config_name').mean(numeric_only=True)
    key = ['slo_violation_rate', 'core_minutes', 'p99_response_time', 'scaling_actions', 'horizontal_scaling_actions']
    full = ab[ab.config_name == 'CHIMERA_Full'].set_index(['workload_id', 'seed'])[key].round(9)
    for n in ar.sort_values('slo_violation_rate').index:
        other = ab[ab.config_name == n].set_index(['workload_id', 'seed'])[key].round(9).reindex(full.index)
        same = (other == full).all(axis=1).mean() * 100
        L.append(f"| {n} | {ar.loc[n,'slo_violation_rate']*100:.2f}% | {ar.loc[n,'p99_response_time']:.2f} | "
                 f"{ar.loc[n,'core_minutes']:.1f} | {asy.loc[n,'slo_violation_rate']*100:.2f}% | {asy.loc[n,'core_minutes']:.1f} | "
                 f"{allm.loc[n,'scaling_actions']:.1f} | {allm.loc[n,'horizontal_scaling_actions']:.1f} | {same:.0f} % |")
    L.append("")
    for name in ('CHIMERA_Full', 'CHIMERA_+Lyapunov_Filter'):
        if name in set(ab.config_name):
            lyap = ab[ab.config_name == name].lyapunov_filter_activation.mean()
            L.append(f"Lyapunov filter activations per run ({name}): {lyap:.2f}.")
    L.append("")

    # Sensitivity
    sens = df[df.run_type == 'sensitivity']
    if len(sens):
        L += ["## Sensitivity runs (not part of the headline)", "",
              "| Config | real SLO | real CPU | synthetic SLO | synthetic CPU |", "|---|---:|---:|---:|---:|"]
        sr, ss = subset(sens, 'real').groupby('config_name').mean(numeric_only=True), \
            subset(sens, 'synthetic').groupby('config_name').mean(numeric_only=True)
        for n in sr.index:
            L.append(f"| {n} | {sr.loc[n,'slo_violation_rate']*100:.2f}% | {sr.loc[n,'core_minutes']:.1f} | "
                     f"{ss.loc[n,'slo_violation_rate']*100:.2f}% | {ss.loc[n,'core_minutes']:.1f} |")
        L.append("")

    # Frontier
    if sw is not None:
        fr = frontier_table(sw)
        fr.to_csv(RES / f'frontier_{tag}.csv', index=False)
        mc = matched_cost(fr)
        mc.to_csv(RES / f'matched_cost_{tag}.csv', index=False)
        og = oracle_gap(fr)
        og.to_csv(RES / f'oracle_gap_{tag}.csv', index=False)
        pt = per_trace_matched(sw)
        pt.to_csv(RES / f'per_trace_matched_{tag}.csv', index=False)
        eq = equal_cpu_tests(sw)
        eq.to_csv(RES / f'equal_cpu_tests_{tag}.csv', index=False)
        for w in SETS:
            plot_frontier(fr, w, FIG / f'frontier_{w}.png')
        knobs = ", ".join(f"{k:g}" for k in sorted(sw[sw.family != 'KOSMOS'].knob.unique()))
        L += ["## Cost vs SLO frontier", "",
              f"Each controller swept over target utilisation [{knobs}] "
              f"(KOSMOS: its latency setpoint alpha). Figures: `results/figures/{tag}/frontier_*.png`.", ""]
        if not og.empty:
            L += ["Distance from a minute-level clairvoyant reference (knows each minute's average demand "
                  "in advance and resizes instantly with no boot delay, but cannot react within a minute): "
                  "SLO points above the reference at the same CPU, averaged over each controller's sweep "
                  "points. Lower is better. Negative means the controller beats the best per-minute plan, "
                  "which is possible only by tracking within-minute bursts.", "",
                  "| Controller | real | synthetic | all |", "|---|---:|---:|---:|"]
            piv = og.pivot_table(index='family', columns='set', values='gap_pp')
            for fam in piv.sort_values('all').index:
                b = '**' if fam == 'CHIMERA' else ''
                cells = [f"{piv.loc[fam, k]:+.2f}" if k in piv.columns and pd.notna(piv.loc[fam, k]) else "-"
                         for k in ('real', 'synthetic', 'all')]
                L.append(f"| {b}{fam}{b} | " + " | ".join(cells) + " |")
            L.append("")
        L += ["",
              "SLO gap at equal CPU cost: baseline SLO minus CHIMERA SLO, interpolated at each CHIMERA "
              "sweep point that falls inside the baseline's own cost range (positive = CHIMERA better).", "",
              "| Set | vs | points compared | mean SLO gap (pp) | min | max | CHIMERA better at | p99 CHIMERA / baseline (s) |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
        for (w, b), g in mc.groupby(['set', 'baseline']):
            L.append(f"| {w} | {b} | {len(g)} | {g.gap_pp.mean():+.2f} | {g.gap_pp.min():+.2f} | "
                     f"{g.gap_pp.max():+.2f} | {(g.gap_pp > 0).sum()}/{len(g)} | "
                     f"{g.chimera_p99.mean():.2f} / {g.baseline_p99.mean():.2f} |")
        L.append("")
        if not pt.empty:
            L += ["Equal-cost comparison trace by trace (each trace's own frontiers; averages across traces "
                  "can hide a trace where CHIMERA loses). 'wins' = traces where CHIMERA has fewer "
                  "violations at the same CPU; only traces where the cost ranges overlap count.", "",
                  "| Set | vs | traces compared | CHIMERA wins | mean gap (pp) | worst trace (pp) |",
                  "|---|---|---:|---:|---:|---:|"]
            for (st, b), g in pt.groupby(['set', 'baseline']):
                worst = g.loc[g.gap_pp.idxmin()]
                L.append(f"| {st} | {b} | {len(g)} | {(g.gap_pp > 0).sum()}/{len(g)} | {g.gap_pp.mean():+.2f} | "
                         f"{worst.gap_pp:+.2f} ({worst.workload_id}) |")
            L += ["", f"Overall: CHIMERA wins {(pt.gap_pp > 0).sum()} of {len(pt)} trace-baseline comparisons "
                  f"at equal CPU.", ""]
        if not eq.empty:
            L += ["Significance of the equal-CPU comparison: one gap per (trace, seed), CHIMERA vs the "
                  "baseline at the same CPU on the same request stream (positive = CHIMERA better). "
                  "One-sample Wilcoxon signed-rank, Holm-corrected across baselines within each set.", "",
                  "| Set | vs | gaps (trace x seed) | traces | mean gap (pp) | p (Holm) | traces won |",
                  "|---|---|---:|---:|---:|---:|---:|"]
            for _, r in eq.sort_values(['set', 'baseline']).iterrows():
                L.append(f"| {r.set} | {r.baseline} | {r.n} | {r.traces} | {r.mean_gap:+.2f} | "
                         f"{fmt_p(r.p_holm)} | {r.trace_wins}/{r.traces} |")
            L.append("")
        # Reach: lowest violation rate each family attains anywhere on its sweep
        L += ["Lowest SLO violation rate each controller reaches anywhere on its sweep (and the CPU it costs):", "",
              "| Set | " + " | ".join(sorted(fr.family.unique())) + " |",
              "|---|" + "---:|" * fr.family.nunique()]
        for w in SETS:
            f = fr[fr.set == w]
            cells = []
            for fam in sorted(fr.family.unique()):
                c = f[f.family == fam]
                r = c.loc[c.slo_violation_rate.idxmin()]
                cells.append(f"{r.slo_violation_rate*100:.1f}% @ {r.core_minutes:.0f}")
            L.append(f"| {w} | " + " | ".join(cells) + " |")
        L.append("")

    # Bars for the deck
    s = summary['real']
    plot_bars(s, 'p99_response_time', 'p99 latency, five real traces (seconds, lower is better)', ' s',
              FIG / 'p99_real.png', log=True)
    plot_bars(s, 'slo_violation_rate', 'SLO violations, five real traces (%, lower is better)', '%',
              FIG / 'slo_real.png', scale=100)
    plot_bars(s, 'scaling_actions', 'Scaling actions per run, five real traces', '',
              FIG / 'actions_real.png', log=True)

    (RES / f'REPORT_{tag}.md').write_text("\n".join(L), encoding='utf8')
    print("\n".join(L))


if __name__ == '__main__':
    main()
