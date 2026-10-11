"""
KOSMOS recheck: why is its tail latency so bad in the v5 report?

KOSMOS regulates the MEAN response time to alpha x SLA. The evaluation scores the share of
individual requests above the SLA. With exponential service times a mean response time of
alpha x SLA leaves about exp(-1/alpha) of requests above the SLA whatever the controller
does (24 % at alpha = 0.7), and the v5 frontier sweep only went down to alpha = 0.5
(14 %). This script extends the sweep to the setpoints a per-request SLO actually needs
and reports what KOSMOS achieves there, next to AAPA and CHIMERA on the same runs.

    python src/scripts/kosmos_recheck.py            # writes results/kosmos_recheck.csv
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

ALPHAS = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70, 1.00]
SEEDS = [101, 202, 303]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default='results/kosmos_recheck.csv')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)

    base = ev.build_configs()
    configs = {'AAPA': base['AAPA'], 'CHIMERA': base['CHIMERA'], 'HPA': base['HPA']}
    for a in ALPHAS:
        configs[f'KOSMOS@a={a:.2f}'] = dict(base['KOSMOS'], run_type='sweep', family='KOSMOS', knob=a, alpha=a)

    workloads = ev.get_holdout_workloads()
    cqr = {w['id']: ev.train_cqr_for_trace(w['trace'], 'test') for w in workloads}
    jobs = [(w['id'], name, cfg, s) for w in workloads for name, cfg in configs.items() for s in SEEDS]
    with Pool(args.workers, initializer=ev._init_worker, initargs=(workloads, cqr, 'test', 20)) as pool:
        rows = pool.map(ev._run_one, jobs, chunksize=2)
    df = pd.DataFrame(rows)
    df['set'] = np.where(df.workload_id.str.startswith('real'), 'real', 'synthetic')
    df.to_csv(args.out, index=False)

    g = (df.groupby(['set', 'config_name'])
           .agg(slo=('slo_violation_rate', 'mean'), mean_rt=('mean_response_time', 'mean'),
                p99=('p99_response_time', 'mean'), cpu=('core_minutes', 'mean'),
                actions=('scaling_actions', 'mean'))
           .reset_index())
    g['slo'] = (100 * g.slo).round(2)
    # what a mean response time of alpha x SLA implies under exponential response times
    g['floor_at_setpoint'] = [round(100 * float(np.exp(-1.0 / float(n.split('=')[1]))), 1) if n.startswith('KOSMOS') else np.nan
                              for n in g.config_name]
    print(g.round(3).to_string(index=False))


if __name__ == '__main__':
    main()
