# CHIMERA: Delay-Aware Predictive Coupled Autoscaling for Kubernetes

CHIMERA is a joint horizontal + vertical pod autoscaler. It drives both replica count
(`h`, slow: a new pod takes ~20 s to boot) and CPU per pod (`v`, fast: an in-place resize
under KEP-1287 takes ~0.1 s) from **one** delay-augmented LQR control law, instead of
two loops that fight. Demand comes from conformal forecasts: a CQR interval plus a
conformal margin on the reactive fallback.

It extends the AAPA baseline (arXiv:2507.05653) from horizontal-only to a coordinated
2-D controller.

## Results (simulation; CHIMERA v5, held-out evaluation)

The final verdict uses data no design decision saw:
- the five real Azure Functions traces;
- four held-out synthetic shapes (flash crowd, diurnal, step-down, on/off) that appear in
  neither the dev nor the earlier test set;
- ten fresh seeds;
- a 20-minute unscored warm-up for every controller.

There are 3,510 main runs (39 configurations x 9 workloads x 10 seeds) plus a 3,780-run
cost/SLO frontier sweep, in which every controller is run at target utilisation 0.4-0.95.
Full tables are in `results/REPORT_v5.md`.

**Equal CPU, the fair comparison.** On each trace, each baseline's SLO is interpolated at
CHIMERA's CPU cost:
- **CHIMERA has fewer violations in 44 of 44 trace-baseline comparisons**, against every
  baseline, on every trace.
- The advantage is significant against every baseline on the real traces, the synthetic
  traces and both combined (per trace x seed gaps, Wilcoxon, Holm-corrected, all
  p <= 0.0002).
- Mean advantage at equal CPU: +2.2 pp over AAPA on the real traces, +5.4 pp on the
  held-out synthetic traces.
- v5 also beats v4 and v2 at equal CPU on all 9 traces.

Five real traces, each controller at its default setting:

| Controller | SLO violations | p99 latency | CPU (core-min) | resizes/run |
|---|---:|---:|---:|---:|
| **CHIMERA v5** | **6.1%** | **0.83 s** | 244 | 62 |
| AAPA (base paper) | 6.9% | 0.91 s | 237 | 7 |
| HPA + VPA | 14.1% | 1.73 s | 195 | 23 |
| HPA | 15.6% | 3.65 s | 191 | 18 |
| MagicScaler-inspired | 16.8% | 2.69 s | 181 | 19 |
| VPA | 19.4% | 4.79 s | 169 | 16 |
| KOSMOS (ICSOC 2021) | 19.8% | 21.8 s | 325 | 253 |
| CHIMERA v4 | 7.4% | 0.89 s | 230 | 51 |
| CHIMERA v1 (Review 2) | 11.8% | 1.33 s | 227 | 10 |

- **Default settings.** Baselines run at their 70 % target; CHIMERA at its default risk
  price (knob 0.5, chosen on dev). Against AAPA: 0.8 pp fewer violations (p = 0.012) for a
  CPU difference that is not significant (+3 %).
- **Held-out synthetic shapes.** CHIMERA 5.9 % at 68 core-min, against AAPA 7.0 % at 102:
  fewer violations with a third less CPU.
- **Correction to earlier versions.** v2-v4 reported a ~19x better p99 than AAPA. With a
  warm-up, most of that turned out to be AAPA starting cold. The real tail gap is 1.1x
  against AAPA, 4.4x against HPA and 26x against KOSMOS.

**What each v5 component does** (ablation, `results/REPORT_v5.md`):
- Fast CPU release: without it, CPU doubles.
- Cascade inner loop: worse cost/SLO without it.
- Inner loop owns CPU: resizes 87 → 60.
- 10 % scale-up dead-band: resizes 82 → 60.
- Replica band: pod changes 7.6 → 0.4 per run.
- CQR: changes the cost/SLO trade-off.
- ADWIN margin reset: small effect.
- Superseded by risk sizing (almost no effect now): the reactive margin and the OOD
  fallback.
- Inert (identical runs without them): the coupling penalty, the anti-chattering guardrail,
  the bridging flag, and the Lyapunov runtime filter (off; the Riccati solution certifies
  stability).

**How v5 was chosen.** All design choices were made on a separate dev split: an earlier
time window, dev-only synthetic traces and dev-only seeds. See `CODE_FIXES_LOG.md` for
every round, including the bisection bug in the first risk-sizing implementation, which
was caught by a test and fixed before the held-out run.

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

Run the tests (both suites, 18 and 29 tests; `kubernetes` is needed only for the operator tests):

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
