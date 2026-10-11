# Experimental setup

## Simulator

Discrete-event SimPy simulator extended from the AAPA framework.

| Parameter | Value |
|---|---|
| Outer control period | 15 s |
| Inner (vertical-only) period | 5 s |
| Pod boot time | 20 s |
| In-place CPU resize | 0.1 s |
| CPU per pod | 0.5 to 4.0 cores |
| Replicas | 2 to 30 (VPA: 5 fixed) |
| Latency SLO | 0.5 s |
| Mean service time | 0.1 s CPU, exponential |
| Start state, all controllers | 0.5 core per pod, 2 replicas (VPA: its 5 pods) |

## Workloads

- **Real:** five Azure Functions 2019 traces (KSWD `w_0001` to `w_0005`), 100-minute
  excerpts, scaled by 15 to exceed minimum capacity.
- **Held-out synthetic:** flash crowd, diurnal, step-down, on/off. These shapes appear in
  neither the dev set nor the earlier test set and have their own generator seed.

## Splits

| Split | Scored window | Traces | Seeds | Used for |
|---|---|---|---|---|
| dev | minutes 40-70 | real + 4 dev-only synthetic | 7, 11, 13 | every design choice |
| held-out | minutes 70-100 | real + 4 held-out synthetic | 101 ... 1010 (ten) | the verdict, run once |

The forecaster is trained on minutes before each scored window. Minutes 50-70 are
simulated as an unscored warm-up for every controller, so no controller is scored on its
cold start.

The five real traces are the same in both splits (different windows). This is a stated
limitation: the full dataset's public URL now refuses access.

## Controllers compared

HPA, VPA, HPA+VPA, AAPA, a MagicScaler-inspired scaler, KOSMOS (ICSOC 2021,
reimplemented, PI gains tuned on held-out data: DC = 0.8, BC = 0.15, alpha = 0.70), and
CHIMERA v1 to v5. Baselines run at a 70 % utilisation target. CHIMERA v5 runs at risk
knob 0.5, chosen on dev as the cheapest setting whose dev SLO is no worse than v4's.

## Runs

- Main comparison: 39 configurations x 9 workloads x 10 seeds = 3,510 runs.
- Cost/SLO frontier: every controller swept over target utilisation 0.4 to 0.95
  (KOSMOS over its latency setpoint), 5 seeds = 3,780 runs.

## Metrics and statistics

SLO violation rate, p99 latency, CPU cost in core-minutes, pod-minutes, and actuation
counts (in-place resizes and pod changes).

Controllers at default settings sit at different points on the cost/SLO curve, so the
primary comparison is **at equal CPU**: each baseline's SLO is interpolated on its own
frontier at CHIMERA's CPU cost, per trace. Significance: one gap per (trace, seed) on the
same request stream, one-sample Wilcoxon signed-rank, Holm-corrected across baselines.

A minute-level clairvoyant reference (knows each minute's mean demand, resizes instantly)
is reported as a yardstick. It is not a bound: a controller that tracks within-minute
bursts can beat it.

## Reproduce

Commands are in the repository README ("Running it"). Results land in
`results/results_v5.csv` and `results/results_v5_sweep.csv`; `src/scripts/analyze_v2.py
--tag v5` regenerates `results/REPORT_v5.md` and the figures.
