"""
Resize-churn study on the dev split.

CHIMERA v5 makes about 60 in-place CPU resizes per 30-minute run. This sweeps the three
inner-loop hysteresis knobs (scale-up dead-band, release band, release ticks) and reports,
for each variant against v5 on the dev split only:
  - resizes per run at the default setting;
  - the SLO gap to v5 at equal CPU (each variant's own cost/SLO frontier, per trace).

    python src/scripts/churn_dev.py            # writes results/churn_dev.csv, prints the table
"""

import argparse
import logging
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.scripts import run_full_evaluation as ev   # noqa: E402

# (scale-up dead-band, release band, release ticks); the first is v5
VARIANTS = [(0.10, 0.15, 2), (0.15, 0.15, 2), (0.20, 0.15, 2), (0.10, 0.20, 2), (0.10, 0.25, 2), (0.10, 0.15, 3),
            (0.15, 0.20, 2), (0.15, 0.20, 3), (0.20, 0.25, 2), (0.20, 0.25, 3), (0.30, 0.30, 3),
            (0.25, 0.15, 2), (0.30, 0.15, 2), (0.40, 0.15, 2), (0.50, 0.15, 2)]
UTILS = [0.40, 0.50, 0.60, 0.70, 0.80]
SEEDS = [7, 11, 13]


def name(v):
    return f'up{v[0]:.2f}_rel{v[1]:.2f}_t{v[2]}'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default='results/churn_dev.csv')
    ap.add_argument('--workers', type=int, default=10)
    ap.add_argument('--analyse', action='store_true')
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)

    if not args.analyse:
        configs = {}
        for v in VARIANTS:
            for u in UTILS:
                configs[f'{name(v)}@u={u:.2f}'] = dict(ev.CHIMERA_V5, run_type='sweep', family=name(v), knob=u,
                                                       target_utilization=u, inner_up_band=v[0],
                                                       inner_release_band=v[1], inner_release_ticks=v[2])
        workloads = ev.get_dev_workloads()
        cqr = {w['id']: ev.train_cqr_for_trace(w['trace'], 'dev') for w in workloads}
        jobs = [(w['id'], n, c, s) for w in workloads for n, c in configs.items() for s in SEEDS]
        with Pool(args.workers, initializer=ev._init_worker, initargs=(workloads, cqr, 'dev', 20)) as pool:
            pd.DataFrame(pool.map(ev._run_one, jobs, chunksize=4)).to_csv(args.out, index=False)

    df = pd.read_csv(args.out)
    base = name(VARIANTS[0])
    rows = []
    for fam, g in df.groupby('family'):
        d = g[g.knob.round(2) == 0.50]
        gaps = []
        for wid, gw in df.groupby('workload_id'):
            c = gw[gw.family == fam].groupby('knob')[['core_minutes', 'slo_violation_rate']].mean()
            b = gw[gw.family == base].groupby('knob')[['core_minutes', 'slo_violation_rate']].mean().sort_values('core_minutes')
            x, y = b.core_minutes.to_numpy(), np.minimum.accumulate(b.slo_violation_rate.to_numpy())
            gp = [(np.interp(r.core_minutes, x, y) - r.slo_violation_rate) * 100
                  for _, r in c.iterrows() if x.min() <= r.core_minutes <= x.max()]
            if gp:
                gaps.append(float(np.mean(gp)))
        rows.append(dict(variant=fam, resizes=d.vertical_scaling_actions.mean(), pod_changes=d.horizontal_scaling_actions.mean(),
                         slo=100 * d.slo_violation_rate.mean(), cpu=d.core_minutes.mean(), p99=d.p99_response_time.mean(),
                         gap_vs_v5_equal_cpu=np.mean(gaps), traces_not_worse=int(sum(x >= -0.05 for x in gaps)),
                         traces=len(gaps)))
    t = pd.DataFrame(rows).sort_values('resizes')
    print(t.round(2).to_string(index=False))


if __name__ == '__main__':
    main()
