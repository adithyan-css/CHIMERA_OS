# CHIMERA: Delay-Aware Predictive Coupled Autoscaling for Kubernetes

CHIMERA is a joint horizontal + vertical pod autoscaler. It drives both replica count
(`h`, slow: a new pod takes ~20 s to boot) and CPU per pod (`v`, fast: an in-place resize
under KEP-1287 takes ~0.1 s) from **one** delay-augmented LQR control law, instead of
two loops that fight. Demand comes from conformal forecasts: a CQR interval plus a
conformal margin on the reactive fallback.

It extends the AAPA baseline (arXiv:2507.05653) from horizontal-only to a coordinated
2-D controller.

## Results (CHIMERA v6)

Full tables: `results/REPORT_v6.md` (held-out set), `results/REPORT_hw.md` (ten serverless
traces), `results/REPORT_ext.md` (ten web-server traces), `results/robustness/ROBUSTNESS.md` (other latency models),
`results/replay/REPLAY_REPORT.md` (live cluster). What changed since v5 and why:
`IMPROVEMENT_AUDIT.md`. Paper: `paper/chimera.pdf` (source `paper/chimera.tex`).

**Equal CPU, the fair comparison.** Each baseline's SLO violation rate is interpolated on
its own cost/SLO frontier at CHIMERA's CPU cost, trace by trace.

| Evaluation set | Trace-baseline comparisons won | Mean advantage over AAPA | over KOSMOS |
|---|---:|---:|---:|
| Ten production serverless functions (Huawei 2023; used in no design step) | 57 of 57 | +2.0 pp | +2.4 pp |
| Ten web-server windows (NASA, ClarkNet; used in no design step) | 60 of 60 | +2.0 pp | +1.3 pp |
| Held-out set (5 Azure Functions traces, 4 unseen synthetic shapes) | 47 of 48 | +3.3 pp | +5.8 pp |

All baselines are significantly behind on both sets (Wilcoxon, Holm-corrected,
p < 0.001). The one loss is to the MagicScaler-style scaler on one Azure trace, by 0.66 pp.

**Default settings, ten external real traces:**

| Controller | SLO violations | p99 latency | CPU (core-min) | actions/run |
|---|---:|---:|---:|---:|
| **CHIMERA v6** | 6.18% | 0.83 s | 190 | 26 |
| AAPA (base paper) | 7.45% | 1.18 s | 194 | 8 |
| KOSMOS (ICSOC 2021, re-tuned) | **4.22%** | 0.83 s | 272 | 271 |
| HPA + VPA | 14.84% | 1.74 s | 151 | 24 |
| HPA | 16.19% | 2.93 s | 149 | 19 |
| MagicScaler-inspired | 18.25% | 3.44 s | 143 | 20 |
| VPA | 29.43% | 6.20 s | 116 | 23 |

**What does not favour CHIMERA.**

- At default settings KOSMOS has fewer violations on the external traces and on the
  synthetic shapes, for 43-84 % more CPU and about ten times the resizes.
- Under bimodal service times CHIMERA loses to AAPA at equal CPU on 4 of 9 traces.
- The risk-priced capacity rule adds about 1 pp when the latency law matches its
  assumption and nothing when it does not.
- On a real cluster the comparison is small (three seeds, one service, one trace, against
  the Kubernetes HPA only) and is indicative, not a significance result.

**On a real cluster** (`results/replay3/REPLAY_REPORT.md`; Kubernetes 1.33, measured
latency, three seeds, both sides limited to ten cores). Mean share of requests over the
0.3 s objective, and CPU in core-minutes:

| HPA target | Violations | CPU | CHIMERA setting | Violations | CPU |
|---|---:|---:|---|---:|---:|
| 70 % | 48.9 % | 85.3 | 0.46 (default) | 32.1 % | 80.9 |
| 50 % | 23.5 % | 102.4 | 0.35 | 14.6 % | 93.6 |
| 35 % | 16.3 % | 124.3 | 0.25 | 7.0 % | 108.1 |

7 of the 9 HPA runs were dominated by a CHIMERA run with the same seed (no more CPU, fewer
violations); none of the 9 CHIMERA runs was dominated by an HPA run. One CHIMERA run was
repeated after host interference; both attempts are kept and both counts are reported in
`IMPROVEMENT_AUDIT.md` (W1).

**Corrections to earlier versions of this README.**

- "26x better p99 than KOSMOS" and "+19 pp over KOSMOS" came from a mis-tuned KOSMOS
  baseline. With gains re-tuned on the dev split the figures are 4.7x and +5.8 pp on the
  held-out set.
- "19x better p99 than AAPA" (v2-v4) was mostly AAPA starting cold; with a warm-up it is
  1.1x.
- "Wins 44 of 44" (v5) is now 47 of 48 on the same set for v6, which makes half as many
  resizes.

**What each component does** (ablation, `results/REPORT_v6.md`):
- Fast CPU release: without it, CPU nearly doubles.
- Joint control: 7-37 % less CPU than horizontal-only, 0.5 pod changes per run against 12.
- Inner loop owns CPU, and the 30 % scale-up dead-band: each removes more than half of
  the resizes (80-84 -> 32 per run).
- Replica band: pod changes 8.6 -> 0.5 per run.
- Removed from the decision path because they change nothing (all 90 check runs
  identical): the coupling penalty, the Lyapunov filter and its action grid, and the
  anti-chattering guardrail.

**How it was chosen.** Every design choice was made on a separate dev split (an earlier
time window, dev-only synthetic traces, dev-only seeds). The held-out set has been scored
twice (v5, then v6). The external traces had not been used for anything when v6 ran on
them.

---

## How it works (one joint control tick every 15 s, plus a 5 s inner loop)

1. **Forecast.** A LightGBM CQR model gives a 90% interval for next-minute demand. It is
   recalibrated online, and the reactive fallback carries its own split-conformal
   margin, so a model with nothing to learn from cannot leave CHIMERA with zero headroom.
2. **Risk-optimal capacity.** Cores are chosen to minimise expected SLO violations plus a
   price per core under the queueing model p(u) = exp(-k(1-u)). k = SLO / service time is
   learned online, and the scenarios come from conformal demand changes. With a certain
   forecast this is exactly demand / target. Capacity is split into `x* = [v*, h*]`, with
   `h* x v_max >= cores` always.
3. **Residual vertical bridging.** `v*` is sized for the replicas that are *serving
   now*. While ordered pods boot, the fast actuator covers the gap; when they arrive it
   hands the capacity back.
4. **Delay-augmented LQR.** State `e = [v - v*, h_serving - h*, dh(t-1)]`. The in-flight
   slot carries replicas that were ordered but have not booted, so the controller never
   orders the same capacity twice. A 20 s boot on a 15 s loop needs one slot.
5. **Safety projection.** Physical bounds and the joint capacity budget. Stability is
   certified by the Riccati solution: V = e'Pe decreases under u = -Ke, so v5 runs no
   separate Lyapunov filter (`sigma` re-enables it; it never binds).
6. **Actuate.** CPU in place; replicas via the scale subresource.
7. **Cascade inner loop (every 5 s).** Between the 15 s joint steps, a fast loop is the only
   thing that moves the 0.1 s CPU actuator. It scales up on a statistically significant
   surge and releases CPU once the need has stayed 15 % below the allocation for two ticks.
8. **Replica band.** Replicas change only when CPU resizing cannot absorb the change:
   between ceil(C / (0.85 v_max)) and floor(C / v_min) the pod count does not change the CPU
   bill, but every new pod spends 20 s booting.
9. **ADWIN.** On a detected regime change, the reactive margin drops its pre-change history
   and re-learns the new regime.

## Project structure

```
src/core/chimera_controller.py   delay-augmented LQR, CLF filter, coupling penalty
src/core/chimera_simulator.py    SimPy 2-D cluster simulator (20 s boot, 0.1 s resize)
src/core/kosmos_simulator.py     KOSMOS (ICSOC 2021) baseline, tuned PI gains
src/models/                      CQR, ADWIN
src/scripts/run_full_evaluation.py   all controllers x 9 workloads x seeds (+ --sweep)
src/scripts/analyze_v2.py        statistics, cost/SLO frontier, figures, REPORT_<tag>.md
demo_run.py                      ~10 s live demo through the same harness code
k8s/                             operator, CRD, RBAC, Dockerfile, Prometheus, quickstart,
                                 live_validation.py
aws/ec2/                         AWS deployment on one free-tier instance (deploy, validate, teardown)
aws/eks/                         Amazon EKS variant for a paid account (not run to completion)
results/live/                    live-cluster validation runs and summary
final_research_package/          paper sections written against the v5 results
tests/                           controller/simulator tests + operator tests
presentation/                    figure and deck generators, updated review deck
```

## Running it

Python 3.10+.

```bash
pip install -r requirements.txt
```

Run the tests (both suites, 18 and 32 tests; `kubernetes` is needed only for the operator tests):

```bash
python tests/test_chimera.py
```

```bash
python tests/test_operator.py
```

Live demo (PowerShell: `$env:PYTHONPATH="."; python demo_run.py`):

```bash
PYTHONPATH=. python demo_run.py
```

Full evaluation plus the cost/SLO frontier sweep (together roughly 10-30 min on a
12-core laptop, depending on load), then the statistics, report and figures:

```bash
python src/scripts/run_full_evaluation.py --holdout --warmup 20 --seeds 101 202 303 404 505 606 707 808 909 1010 --out results/results_v5.csv
```

```bash
python src/scripts/run_full_evaluation.py --holdout --warmup 20 --sweep-only --seeds 101 202 303 404 505 --out results/results_v5_sweep.csv
```

```bash
python src/scripts/analyze_v2.py --tag v5
```

The harness writes to the file given by `--out` (the v5 held-out results are `results/results_v5.csv` and `results/results_v5_sweep.csv`) and refuses to overwrite the Review-2 dataset
`results/results_FINAL_verified.csv`.

Design work uses the dev split, never the test window:

```bash
python src/scripts/run_full_evaluation.py --split dev --warmup 20 --sweep-only --seeds 7 11 13 --out results/dev_sweep.csv
```

Rebuild the review deck from the results:

```bash
python presentation/make_deck_figures.py
```

```bash
python presentation/update_deck.py
```

## Kubernetes

`k8s/quickstart.sh` creates a kind cluster on Kubernetes 1.33, where in-place pod resize
is on by default. It installs a minimal Prometheus, builds and deploys the operator, and
attaches a `ChimeraAutoscaler` to a CPU-bound demo service with a load generator.
`--dry-run` makes the operator log decisions without patching anything.

```bash
./k8s/quickstart.sh
```

The operator (`k8s/manager.py`) runs every 15 s over all `ChimeraAutoscaler` objects:

- **Metrics.** Reads CPU usage and, optionally, request rate from Prometheus
  (`k8s/prom_query.py`, a real HTTP API client).
- **Decision.** Runs the same control step as the simulator (`k8s/operator_core.py`).
- **Actuation.** Resizes CPU through the `pods/resize` subresource and scales replicas
  through `deployments/scale`.
- **Status.** Writes the decision back to the object's status.

**Status:** validated on a live Kubernetes 1.33.1 cluster (kind) on 11 Oct 2026:
`results/live/LIVE_REPORT.md`.

- CPU per pod was resized in place under load on the same pods, with zero container
  restarts, and released again when the load stopped.
- A replica was added once CPU alone could not cover the need, held, and removed after the
  load stopped.
- The first run found a real bug (a pod that booted faster than assumed was counted twice
  and removed 15 s after being added). It is fixed and has regression tests.

These runs are a functional test, not a performance comparison: there is no latency
measurement and no baseline on the cluster.

Validate a running cluster yourself:

```bash
python k8s/live_validation.py --out results/live/my_run
```

AWS: the operator also runs on one free-tier EC2 instance with k3s 1.33
(`aws/ec2/deploy.sh`), where the same validation passes all five checks
(`results/live_ec2/README.md`). See `BUILD_PLAN.md`, Phase 5.

## Limitations

- **Data.** The real traces are 100-minute excerpts of five Azure Functions 2019 traces.
  The full dataset's public URL now refuses access, so no fresh real traces could be added.
  The held-out synthetic shapes are our own.
- **Performance numbers are simulated.** Every SLO, latency and cost figure comes from the
  simulator. The operator (same control law) has been run on a live cluster as a
  functional test only.
- **Operator signal lag.** With the default CPU-usage metric the live allocation climbs
  over minutes rather than seconds; a request-rate metric is supported but untested live.
- **Inert components.** The coupling penalty, the anti-chattering guardrail and the
  bridging flag change no decision in the v5 cascade.
- **Resizes.** 62 in-place resizes per run, against v4's 51. AAPA changes pods about 7
  times.
- **Service model.** Risk sizing assumes the simulator's queueing-style latency model. In
  the operator, k comes from configured SLO and service-time values, not yet from live
  latency histograms.

See `CODE_FIXES_LOG.md` for every bug found and fixed, including in the baselines, and
`results/REPORT_v5.md` for the full statistics.

## License

MIT. Builds on the open-source AAPA simulator framework.
