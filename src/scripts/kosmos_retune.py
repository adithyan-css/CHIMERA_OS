"""
Re-tune the KOSMOS PI gains on the dev split under the current simulator.

The gains in the v5 report (DC = 0.8, BC = 0.15) were tuned in August, before the load
sensor fix (CODE_FIXES_LOG.md item 9). With them the vertical loop changes CPU on about
250 of 360 ticks per run and the mean response time sits 3-8x above its own setpoint: the
proportional term alone (DC x error, error between -7 and +2.9) swings the allocation
across its whole range every tick.

Grid over DC, BC and the setpoint alpha on the dev split only (dev window, dev-only
synthetic traces, dev seeds). Writes results/kosmos_retune_dev.csv.

    python src/scripts/kosmos_retune.py
"""

import argparse
import itertools
import logging
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.scripts import run_full_evaluation as ev   # noqa: E402

DCS = [0.01, 0.03, 0.1, 0.3, 0.8]
BCS = [0.002, 0.005, 0.015, 0.05, 0.15]
ALPHAS = [0.25, 0.40, 0.70]
SEEDS = [7, 11]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default='results/kosmos_retune_dev.csv')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)

    base = ev.build_configs()
    configs = {'AAPA': base['AAPA'], 'HPA': base['HPA'], 'HPA+VPA': base['HPA+VPA']}
    for dc, bc, a in itertools.product(DCS, BCS, ALPHAS):
        configs[f'KOSMOS|{dc}|{bc}|{a}'] = dict(base['KOSMOS'], run_type='sweep', family='KOSMOS',
                                                 knob=a, alpha=a, DC=dc, BC=bc)
    workloads = ev.get_dev_workloads()
    cqr = {w['id']: ev.train_cqr_for_trace(w['trace'], 'dev') for w in workloads}
    jobs = [(w['id'], name, cfg, s) for w in workloads for name, cfg in configs.items() for s in SEEDS]
    with Pool(args.workers, initializer=ev._init_worker, initargs=(workloads, cqr, 'dev', 20)) as pool:
        rows = pool.map(ev._run_one, jobs, chunksize=4)
    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)

    g = (df.groupby('config_name')
           .agg(slo=('slo_violation_rate', 'mean'), mean_rt=('mean_response_time', 'mean'),
                p99=('p99_response_time', 'mean'), cpu=('core_minutes', 'mean'),
                actions=('scaling_actions', 'mean')).reset_index())
    g['slo'] = 100 * g.slo
    print(g.sort_values('slo').head(40).round(3).to_string(index=False))


if __name__ == '__main__':
    main()
