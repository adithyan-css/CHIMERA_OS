# Code Fixes Log

Changes made to `src/core/chimera_controller.py`, `src/core/chimera_simulator.py`, and
`src/scripts/run_full_evaluation.py` during the novelty-audit / verification pass, with
rationale. Git commit `3b999c5` contains the actual diff.

Note: items 1-8 cite sections of `CHIMERA_CODE_VERIFICATION_ADDENDUM.md`. That document
was written during the August pass and is not in this repository; the "addendum Section"
references below are kept for traceability but cannot be followed from here. Everything
from the v2 pass onward is self-contained in this file and in `results/REPORT_v*.md`.

1. **target_utilization asymmetry (0.60 -> 0.70).** `ChimeraSimulator` was constructed
   with `target_utilization=0.60` while every baseline used `0.70`, giving CHIMERA an
   unearned headroom advantage. Fixed to match.

2. **v_thresh quantization (0.15 -> 0.05).** The Lyapunov safety filter's corrections to
   the vertical actuator were smaller than the actuation-quantization threshold, so the
   filter's effect on applied actions was almost always dropped. Loosened the threshold
   so genuine corrections survive quantization.

3. **vertical_scale_cooldown (15.0 -> 60.0, now configurable).** Tuned alongside (2).

4. **use_scale_down_guardrail wiring bug.** This flag was read from the ablation config
   dict everywhere except the one place it mattered: the `ChimeraController(...)`
   constructor call inside `run_simulation()` never passed it through, so
   `CHIMERA_w/o_Anti_Chattering` silently ran identically to `CHIMERA_Full` regardless of
   the ablation config. Fixed by adding
   `use_scale_down_guardrail=config.get('use_scale_down_guardrail', True)` to the
   constructor call.

5. **New `decoupled_safety` flag on `ChimeraController`.** Implements a true structural
   decoupling (independent per-axis Lyapunov certificates, no joint capacity constraint,
   kappa/eta auto-zeroed) for a KOSMOS-style baseline, used to directly test whether the
   joint LQR/DARE solve provides coordination benefit beyond the safety-projection
   penalty term. Adds the `CHIMERA_Decoupled` config. Result: negative (see addendum
   Section 8) - reported honestly, not hidden.

6. **CHIMERA_DEBUG_LYAP instrumentation hook (env-gated, no-op by default).** Added to
   `chimera_simulator.py` to trace the Lyapunov filter's raw pre-quantization action
   whenever its activation counter fires, used to resolve why `CHIMERA` and
   `CHIMERA_w/o_Lyapunov` appeared bit-identical on an earlier (later discarded)
   incremental dataset. Does not affect behavior unless the env var is set.

All results reported in the final novelty audit and paper draft come from a clean,
single-pass 504-row rerun (21 configs x 8 workloads x 3 seeds) generated entirely under
this code state, replacing an earlier incrementally-checkpointed dataset that mixed rows
from pre- and post-fix code (a real methodological issue found and corrected during this
pass - see addendum Section 6).

7. **KOSMOS baseline (`src/core/kosmos_simulator.py`, NEW).** Faithful reimplementation
   of Baresi et al. (ICSOC 2021): vertical PI controller in inverse-response-time space,
   KOSMOS's own 5s vertical / 300s horizontal periods, KHPA saturation + UTIL heuristics,
   proportional Contention Manager. PI gains tuned by grid sweep on a held-out workload
   subset and a disjoint seed (DC=0.8, BC=0.15, alpha=0.70) so the baseline competes at
   its best. Fidelity check: reproduces 49.5% pod-minute reduction vs HPA, inside the
   paper's own reported 44-57% range. Also supports `use_inplace_resize=False` to test
   the actuation-cost sensitivity hypothesis (result: hypothesis rejected, see addendum
   Section 10d).

Headline outcome: CHIMERA loses to KOSMOS in aggregate (-6.6pp) but wins on the four
externally-sourced real Azure traces (+4.9pp, p=0.007, d=-1.45) with a 6.4x better p99
tail and 19x less actuation churn, while collapsing on the four self-authored synthetic
stress traces (-18.1pp). See addendum Section 10d.

8. **Restored `w_0003` to the real-trace set.** `get_remediation_workloads()` silently
   excluded it with no documented reason; it is a legitimate non-duplicate trace
   (corr with w_0001 = -0.03). The headline real-trace win survives its restoration
   (CHIMERA vs KOSMOS: -3.81pp, p=0.014/0.035, d=-1.07 at n=15). Use the 5-trace
   numbers; the 4-trace numbers should not appear in the paper.

KNOWN BLOCKING LIMITATION: all five real traces are 100-minute EXCERPTS of 14-day
traces (dataset/EXAMPLE_DATA_NOTE.md). The full KSWD dataset is 10,148 workloads and
requires downloading Azure Functions 2019 + running dataset/tools/azure_to_k8s_converter.py
on a machine with open internet access (the Azure blob host is not reachable from the
dev environments used here). See addendum Section 10e.

---

# v2 pass (30 Sep 2026)

All v2 numbers come from one clean run of `src/scripts/run_full_evaluation.py` on the
code state described below (`results/results_v2.csv`, `results/results_v2_sweep.csv`).
`results/results_FINAL_verified.csv` (Review 2) is left untouched; the harness refuses
to overwrite it.

## Bugs that affected every controller

9. **Load sensor capped at 6.7 req/s.** `ServerlessSimulator.recent_arrivals` was
   `deque(maxlen=100)`, so `calculate_current_load()` (15 s window) could never report
   more than 100/15 req/s -- below the peak of most traces. It feeds both every
   autoscaler and the service-time model, so overload was hidden from all controllers
   alike. Now pruned by age (60 s) and counted by binary search.

10. **Shared CQR state leaked between runs.** One `ConformalQuantileRegressor` per
    workload was reused across all configs and seeds; ADWIN flushes (and now online
    conformal updates) mutated its calibration buffer, so later runs saw earlier runs'
    state. Each run now gets a private copy of the buffer.

## Baseline fairness

11. **MagicScaler under-provisioned 3x.** Its capacity model hard-coded 1.5-core pods
    while the baseline runs 0.5-core pods; it now uses the real pod size. (Review 2's
    74% violation rate for this row was this bug, not the method.)

12. **HPA+VPA never fired its VPA loop.** It started at 1.5 cores and spread usage over
    all pods including booting ones, so per-pod demand never exceeded the floor and the
    row reduced to plain HPA. It now starts from the shared v_min/h_min, and VPA sizes
    pods from usage on serving pods -- two uncoordinated loops on one CPU signal.

13. **KOSMOS configs were missing from `main()`.** Restored with the tuned gains from
    item 7 (DC=0.8, BC=0.15, alpha=0.70).

## CHIMERA correctness

14. **Delay buffer one step too long.** With a 20 s boot and 15 s loop, a pod ordered at
    tick k is first observed at tick k+2, so the augmented model needs one in-flight
    slot. It had ceil(20/15) = 2, i.e. modelled a 45 s delay: at tick k+2 booted pods
    were still "in flight", counted twice, and the controller scaled in immediately
    after scaling out (visible in the demo's step table). Now floor(tau/dt) slots
    (3x3 state for 20 s). Old model kept as `legacy_delay_model` for CHIMERA_v1.

15. **In-flight pods counted in h as well as in the buffer.** The model is
    h(k+1) = h(k) + oldest slot, so h must be *serving* replicas; the simulator fed
    `len(pods)`, which includes pods the moment they are ordered. `serving_replica_state`.

16. **Coupling penalty contradicted its spec.** `kappa*|dv*dh|` also penalised same-
    direction moves -- blocking "resize up while pods boot". Now penalises only opposing
    moves (`coupling_mode="opposing"`); legacy form kept for ablation.

17. **Target split could under-plan capacity.** `h* = ceil(sqrt(C/gamma))` never checked
    `h* x v_max >= C`; once v* clipped at v_max the plan was short. Now
    `h* >= ceil(C / v_max)`. Applies to every CHIMERA variant, v1 included.

18. **Scale-in recorded as "in flight".** Removing a pod takes effect immediately (it
    leaves the serving set), but the removal was also pushed into the delay buffer, so the
    next tick saw h already reduced *and* a further -1 pending, and re-ordered the pod it
    had just removed (visible as "-1 then +1" pairs in the demo's step table). Only
    scale-ups are delayed, so only positive orders enter the buffer (simulator and
    operator). Part of `serving_replica_state`.

## CHIMERA mechanisms added (each has an ablation)

- **Residual vertical bridging** -- v* sized for the pods that are serving now, so the
  0.1 s actuator covers the 20 s boot and hands capacity back as pods arrive.
- **Fast-attack / slow-release target smoothing** -- EMA only on the way down.
- **Online conformal recalibration** -- realised load fed back into the CQR buffer.
- **Conformal margin on the reactive fallback** -- last-value forecast + split-conformal
  quantile of recent one-step changes. Motivated by the synthetic-trace failure
  analysis: those traces are flat during CQR training (minutes 10-70), so the learned
  interval collapses and CHIMERA ran with zero margin. Stated here because the
  mechanism was designed after looking at those traces.

## Engineering

- Riccati gain solved once per delay and cached (was re-solved every step).
- Safety filter vectorised; Lyapunov activation measured in the same pass (was a second
  full controller solve with the filter disabled). Control step 5 ms -> ~0.3 ms.
- Harness: multiprocessing, `core_minutes` (real CPU cost) column, frontier sweep,
  10 seeds by default for the main comparison. Full run ~10 min on 12 cores.

## Kubernetes operator

- `k8s/prom_query.py`: real Prometheus HTTP API client (stdlib only), replacing the
  random-number telemetry mock.
- `k8s/operator_core.py`: the control step, free of client libraries, mirroring the
  simulator path; online split-conformal forecaster (no offline training needed).
- `k8s/manager.py`: periodic 15 s loop over all ChimeraAutoscalers (was: acted once per
  CR edit); in-place CPU resize through `pods/resize` (KEP-1287), not a template patch
  that triggers a rolling restart; replica changes via the scale subresource; status
  written back; dry-run mode.
- CRD gains `targetUtilization`, `podStartupSeconds`, `resizeMode`, `metrics.*` and a
  status subresource; RBAC gains `pods/resize`; `k8s/prometheus.yaml` + quickstart on
  kind v1.33 with a CPU-bound demo service and load generator.
- Not yet validated on a live cluster (no cluster was available in this environment).

---

# v3 pass (1 Oct 2026): cascade control, clairvoyant bound, dev/test discipline

## Why v3

v2 tied the best baselines at equal CPU. The diagnosis (per-request violations bucketed
by utilisation at arrival) showed most violations happen at moderate utilisation: with
exponential service times (mean 0.1 s) and a 0.5 s SLO the simulator has a hard floor
(about 5 % violations at 40 % utilisation, 0.7 % even when idle). Under that convex cost,
the best possible policy keeps utilisation as constant as possible, i.e. capacity
tracking demand. That makes the question *how close is each controller to the best
possible?*, so v3 adds a way to measure it.

## Minute-level clairvoyant reference (`kind='oracle'`)

`OracleSimulator` knows each minute's true average demand in advance and resizes instantly
with no boot delay. It is the best *per-minute* plan, not a strict bound: arrivals within a
minute are Poisson and the service model reacts to 15 s bursts, so a controller that tracks
within-minute bursts can land below it. (An earlier draft of this note called it
"unbeatable"; the test run showed CHIMERA v3 below it on the real traces, which is how the
mistake was caught.) Reports give each controller's SLO gap to it at equal CPU.

## Dev/test discipline

`--split dev` scores minutes 40-70 (forecaster trained on 10-30 and calibrated on 30-40),
adds four dev-only synthetic traces (random spikes, square wave, ramp-down, heavy tail)
and uses dev-only seeds 7/11/13. Every v3 design choice was made on dev. The test window
(70-100), the test traces and the test seeds were run once, after the design was frozen.

## Cascade control (`inner_vertical_period`, `inner_release_band`, `inner_release_ticks`)

A 5 s inner loop drives only the 0.1 s vertical actuator; the 15 s outer loop keeps the
joint LQR, replicas and in-flight buffer:

- **Fast attack.** Scale up as soon as a Poisson test flags a significant surge in a short
  arrival window, or the need exceeds the allocation.
- **Fast release.** Release once the need has stayed 15 % below the allocation for two
  inner ticks. Re-acquiring CPU takes 0.1 s, so holding it through the 60 s cooldown is
  waste.
- **Cost accounting.** Because CPU now changes between outer ticks, cost is integrated
  every second for configs with an inner loop. This was checked against per-tick
  charging: identical within 0.06 % when the inner loop is idle.

Dev results, as SLO gap to the per-minute reference at equal CPU (all / real / synthetic):

| Variant | all | real | synthetic | Note |
|---|---:|---:|---:|---|
| v2 | 2.21 | 1.77 | 3.96 | |
| inner loop, no release | 1.77 | | | |
| + release 0.15 | 0.78 | | | |
| + 2-tick release (**chosen**) | 0.81 | | | 22 % fewer resizes than release 0.15 |
| VPA, best baseline on dev | 1.85 | | | |

Rejected on dev:
- a conformal margin measured over the 5 s horizon (2.08: under-provisions);
- release-to-need+headroom (0.96-1.20).

Cost of the design: more in-place resizes than v2 (still far fewer than KOSMOS).

## Other changes

- Sweep extended to target utilisation 0.95 for every controller, so the cheap end of each
  frontier is measured instead of left out.
- The operator runs the same cascade: `TargetState.fast_step` every 5 s
  (`CHIMERA_FAST_INTERVAL`), with a full joint step every 15 s.

## v3 test-window results (frozen design, single run)

Full tables: `results/REPORT_v3.md`. Main comparison: 2,970 runs, 10 seeds. Frontier sweep:
3,150 runs, 5 seeds, utilisation 0.4-0.95.

- **Equal CPU, trace by trace.** CHIMERA has fewer violations in 39 of 43 comparisons,
  including all 17 on the synthetic traces. All four losses are on w_0002 (periodic),
  against AAPA, HPA, MagicScaler and VPA, by 0.7-1.8 pp.
- **Real traces, 70 % target.**

  | | SLO | p99 | CPU (core-min) |
  |---|---:|---:|---:|
  | CHIMERA v3 | 7.08 % | 0.91 s | 241 |
  | CHIMERA v2 | 6.86 % | | 250 |
  | AAPA | 8.29 % | 17.8 s | 234 |

  The 3 % premium over AAPA was 7 % in v2.
- **Synthetic traces, 70 % target.** CHIMERA 7.38 % at 54.6 core-min, against VPA 9.77 % at
  78.6 and AAPA 9.97 % at 82.7. The per-trace paired test against VPA and AAPA at this fixed
  target is still not significant.
- **Cost of v3.** 103 in-place resizes per run on the real traces, against 40 for v2 and 256
  for KOSMOS.

---

# v4 pass (1 Oct 2026): cascade done properly, every component accounted for

Targets: v3's remaining weak points (w_0002 losses, resize churn, synthetic significance,
three components with no effect). All design work was done on the dev split; the test
window was run once after freezing.

## Diagnoses (dev split)

- **w_0002 is high, flat and noisy, not periodic in these windows.** CHIMERA lost to VPA
  because about 3 % of its CPU sat on booting pods: its replica count kept changing on a
  flat load. VPA, with 5 fixed pods, pays none.
- **Two loops on one actuator.** In v3 both the outer LQR (~30-40 per run) and the inner
  loop (~50) resized CPU, partly undoing each other.
- **Tried and rejected on dev:**
  - a Poisson-gated rate estimator (worse, including on w_0002);
  - a one-sided conformal margin (mixed);
  - moving replicas only to the nearest band edge (stopped by the user before running).

## Changes (CHIMERA_V4)

- `inner_owns_vertical`: only the inner loop moves CPU; the outer loop decides replicas
  and the joint target. On dev, resizes went from 77 to 52 with the same wins.
- `replica_band = 0.85`: between ceil(C / (0.85 v_max)) and floor(C / v_min), the replica
  count does not change the CPU bill, but each change costs 20 s of booting CPU. h* stays
  put inside that band. On dev: wins 38 → 40 of 44, square-wave losses gone.
- `adwin_margin_reset`: ADWIN now has a job. On a detected regime change, the reactive
  margin drops its pre-change history and re-learns the new regime. On dev, the
  random-spike losses vanished (40 → 43 of 44).
- `online_conformal = False`: removing it improved dev efficiency (gap to the per-minute
  reference 0.50 → 0.35).
- `sigma = 100` (Lyapunov runtime filter off): bit-identical with or without it. V = e'Pe
  from the DARE solution satisfies V(e+) - V(e) = -e'(Q + K'RK)e <= 0 under u = -Ke, so a
  filter enforcing a decrease cannot bind for the nominal action. Stability is certified
  by P > 0.
- **Operator.** It runs the same v4 law: the full step leaves CPU to `fast_step`, applies
  the replica band and resets the margin on drift.
- **Analysis.** Added a significance test for the equal-CPU comparison, which has been the
  primary comparison since v2: per (trace, seed) gaps on the same request stream, Wilcoxon,
  Holm-corrected. The fixed-target test is unchanged and still reported.

## Test-window results (frozen, single run; `results/REPORT_v4.md`)

- **Equal CPU, trace by trace.** 39 of 43 wins, the same count as v3. All four losses are
  still on w_0002 but smaller: -0.45 to -1.62 pp, against -0.73 to -1.76 in v3. On dev,
  v4 had beaten AAPA, HPA and MagicScaler on w_0002; on test it does not. Not tuned
  further.
- **Equal-CPU significance.**
  - Every baseline on all nine traces: significant.
  - Every baseline on the synthetic traces: significant (VPA p = 0.031, AAPA p = 0.0001).
  - Real traces: significant against all except VPA (p = 0.14).
- **Resizes.** 57 per run on the real traces (v3: 103, -45 %); 49 on the synthetic traces
  (v3: 76).
- **Fixed 70 % target, real traces.** 7.23 % SLO at 240.8 core-min.
  - Against AAPA: -1.06 pp (p = 0.006), with a CPU difference of +6.9 core-min that is
    not significant (p = 0.30).
  - Against v3: +0.15 pp at the same CPU, in exchange for 45 % fewer resizes.
- **Inert in v4.** Bit-identical runs without the coupling penalty (100 %) or the
  anti-chattering guardrail (100 %); 98.9 % identical without the bridging flag. The
  cascade and the replica band already guarantee what these enforced. Reported in the
  ablation, not hidden.
- **ADWIN margin reset on test.** Small effect (63 % of runs differ, about 0.05 pp). Its
  large effect was on the dev random-spike trace.

---

# v5 pass (1 Oct 2026): risk-optimal sizing, warm-up protocol, held-out evaluation

## Why the evaluation changed

The real-trace test window had been scored three times (v2-v4), with design rounds in
between: a mild but real adaptive-overfitting risk. The v5 verdict therefore uses data no
design decision has seen:

- four synthetic shapes that appear in neither the dev nor the earlier test set (flash crowd,
  diurnal, step-down, on/off), with their own generator seed;
- ten fresh seeds (101 ... 1010);
- the five real traces, which are unavoidably the same five.

Everything was declared in code (`get_holdout_workloads`, `HOLDOUT_SEEDS`) before any v5
result existed. Fresh real traces were not obtainable: the Azure Functions 2019 archive URL
now returns "public access is not permitted".

## Warm-up protocol (`--warmup 20`)

v2-v4 started every run cold at minute 70 with 2 pods, except VPA, which by definition
starts with its 5 fixed pods already warm. That start-up transient was part of every
w_0002 loss. Standard simulation practice is an unscored warm-up:

- minutes 50-70 are simulated but not scored, for every controller identically;
- requests arriving before the scoring start are not counted;
- cost and action counters are measured from a snapshot at the scoring start.

`--warmup 0` reproduces the v4 numbers exactly (checked on 5 configs).

## Risk-optimal sizing (`risk_sizing`)

A request misses the SLO with p(u) = exp(-k (1 - u)) below u = 0.95 (saturating above),
k = SLO / mean CPU service time, learned online from completed requests.

- **Objective.** Capacity c minimises the expected number of violating requests over demand
  scenarios, plus lambda * c. The scenarios are current load + recent signed one-step
  changes, plus the CQR upper bound.
- **Price.** lambda = p'(u0) u0^2 / cpp, so with a certain load the minimiser is exactly
  L * cpp / u0: the knob keeps its meaning, and under uncertainty the convex risk decides
  where headroom pays.
- **Churn.** A 10 % relative dead-band on inner-loop scale-ups (`inner_up_band`) cut
  resizes by 26 % on dev with no loss of efficiency.
- **Default knob 0.5.** Chosen on dev as the cheapest setting whose dev SLO is no worse
  than v4's default.

**Bug found before the held-out run.** The first implementation found c by bisection on
the marginal, assuming it is monotone in c. It is not: above u = 0.95 the violation
probability saturates and the marginal falls, so a scenario crossing 0.95 can mislead the
bisection (an operator test returned *less* capacity for uncertain demand). The held-out
run had started; it was stopped after 200 of 3,510 runs with no result written or read.

- **Fix.** Global grid minimisation of the objective, refined with bounded Brent, over
  capacities keeping the *expected* load at or below 95 % utilisation. Below that floor,
  "allocate almost nothing and accept the violations" can be the true minimiser.
- **Re-check on dev** (warm-up protocol), as equal-CPU wins / gap to the per-minute
  reference / resizes per run:

  | Variant | Wins | Gap | Resizes |
  |---|---:|---:|---:|
  | v4 | 42/43 | -1.92 | 37 |
  | v5, bisection | 41/41 | -4.99 | 52 |
  | **v5, fixed** | **42/42** | **-5.14** | **53** |

**Property of the objective, not a bug.** For extreme volatility (e.g. +-120 % swings) the
optimiser may decline to cover the rare high scenario, when covering it costs more than
the violations it prevents at the configured price.

**Operator.** It uses the same `risk_cores` (demand in cores), with k from the new CRD
fields `targetSLOMilliseconds` / `serviceTimeMilliseconds`.

## v5 held-out results (frozen design, single run; `results/REPORT_v5.md`)

- **Equal CPU, trace by trace.** 44 of 44 comparisons won, against every baseline on every
  trace (w_0002 included: smallest margin +1.41 pp, against MagicScaler). Significant
  against every baseline on the real, synthetic and combined sets (all p <= 0.0002).
  Mean advantage over AAPA: +2.2 pp (real), +5.4 pp (held-out synthetic).
- **v5 vs earlier versions at equal CPU.** Beats v4 on 9 of 9 traces (+0.09 to +3.2 pp;
  w_0002 essentially a tie) and v2 on 9 of 9.
- **Default settings, real traces.** 6.13 % SLO at 244 core-min, against AAPA 6.92 % at
  237: -0.79 pp (p = 0.012), CPU difference not significant.
- **Held-out synthetic, default settings.** 5.93 % at 68.0 core-min, against AAPA 7.02 % at
  101.9.
- **Correction.** With the warm-up protocol, AAPA's p99 is 0.91 s, not 17.8 s.
  CHIMERA's 0.83 s is still the lowest, but the gap is 1.1x, not the ~19x reported in
  v2-v4. That figure came mostly from AAPA's cold start; HPA (4.4x) and KOSMOS (26x)
  remain well behind. All documents and the deck were corrected.
- **Ablation.** Risk sizing supersedes the reactive margin and the OOD fallback (removing
  either now changes almost nothing). The inert set in v5 (identical runs) is: the coupling
  penalty, the anti-chattering guardrail, the Lyapunov filter, and the bridging flag (97 %
  identical).
- **Resizes.** 62 per run (v4: 51 on the same held-out data).

---

# Live-cluster pass (11 Oct 2026): first runs on real Kubernetes

The operator ran on a real cluster for the first time (kind, Kubernetes 1.33.1, Prometheus
scraping cAdvisor, a CPU-bound demo service and a closed-loop load generator). Evidence:
`results/live/`.

## Bug found by the first run

19. **A pod that boots faster than assumed was counted twice.** The in-flight buffer
    assumes every ordered pod takes `podStartupSeconds` (20 s) to serve. On the cluster a
    new pod was Ready within one 15 s tick, so at the next step it was counted as serving
    *and* as in flight: four pods against a target of three, and the controller removed
    the pod it had added 15 s earlier. The simulator cannot show this because its boot
    time is exactly 20 s. Fix: `TargetState._reconcile_inflight` (operator only) makes the
    buffer agree with the cluster before each joint step: in flight = desired replicas
    minus Ready replicas. Replaying the logged observations of the failing run through the
    fixed code gives no scale-in at that tick; two regression tests added.

## Other changes

- `k8s/live_validation.py`: drives baseline / surge / relax load phases, samples the
  cluster every 5 s and writes a pass/fail report.
- `k8s/demo-service.yaml`: the demo service and load generator, shared by the kind
  quickstart and the EKS deployment (was inline in `quickstart.sh`).
- `k8s/Dockerfile`: no compiler install (all dependencies ship wheels); `.dockerignore`
  keeps results, decks and git history out of the build context.
- `aws/`: eksctl cluster definition, deploy and teardown scripts for Amazon EKS.

## Observed, not changed

- **Demand signal lag.** The default metric is a 1-minute rate of CPU usage, and CPU usage
  is capped by the limit while pods throttle. Under the closed-loop load generator the
  allocation therefore climbs in steps over a few minutes instead of jumping. A request
  rate metric (`spec.metrics.requestRateQuery`) removes the cap; it was not configured in
  these runs.
