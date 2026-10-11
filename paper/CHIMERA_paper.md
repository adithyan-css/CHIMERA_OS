# CHIMERA: Joint Horizontal and Vertical Pod Autoscaling with a Fast and a Slow Actuator

*Draft manuscript, 12 October 2026. Every number is taken from a file in `results/`; the
table at the end says which. Figures are in `results/figures/v6/`.*

## Abstract

Kubernetes can now change the CPU of a running pod in well under a second, while a new
replica still takes tens of seconds to serve. Existing autoscalers either use one of these
actuators or run a separate loop for each. We present CHIMERA, a controller that plans
replica count and CPU per pod together. It combines a linear-quadratic regulator whose
state includes the replicas that have been ordered but are not yet serving, a cascade in
which a five-second inner loop owns CPU and a fifteen-second outer loop owns replicas, and
a capacity rule that prices the risk of missing a latency objective against the cost of
CPU over conformal demand scenarios. Design choices were made on a development split and
judged on data they did not see. In simulation, at equal CPU cost, CHIMERA has fewer
objective violations than HPA, VPA, their combination, AAPA, a MagicScaler-style scaler and
KOSMOS on 47 of 48 trace-by-baseline comparisons on a held-out set, and on all 60 on ten
real web-server traces that no design step used (mean advantage 2.0 percentage points over
AAPA and 1.3 over KOSMOS). The advantage persists, with a smaller margin, under latency
laws the controller does not assume. An ablation shows that fast release of CPU, joint
control and single ownership of each actuator carry the benefit, that a coupling penalty
and a Lyapunov filter from the original design change no decision, and that the
risk-priced capacity rule helps only when its latency model is roughly right. A Kubernetes
operator runs the same control law; on a real cluster it resizes pods in place without
restarts and reacts to a flash crowd sooner than the HPA, but a single-run comparison does
not establish an advantage at matched cost.

## 1. Introduction

A service on Kubernetes can be given more capacity in two ways: more pod replicas, or more
CPU per pod. The two actuators differ by two orders of magnitude in speed. A new replica
typically needs tens of seconds to be scheduled, started and made ready. Since in-place pod
resize (KEP-1287; beta and on by default from Kubernetes 1.33, stable in 1.35) the CPU of a
running pod can be changed in well under a second without restarting the container.

The stock autoscalers do not exploit this. The Horizontal Pod Autoscaler (HPA) changes the
replica count from average utilisation; the Vertical Pod Autoscaler (VPA) changes pod size
from usage history. Run together on the same CPU signal they interfere, and neither models
the fact that a replica it has just ordered is not serving yet. Predictive horizontal
scalers such as AAPA and MagicScaler handle uncertainty well but have only the slow
actuator, so they must pay for forecast error with standing headroom. Systems that do use
both actuators, such as KOSMOS, run two separately designed loops.

This paper asks how one controller should use a slow and a fast actuator on the same
resource to hold a per-request latency objective at the lowest CPU cost. We present
CHIMERA, which combines three ideas:

1. **A delay-aware joint control law.** Replicas and CPU per pod are driven by one
   linear-quadratic regulator whose state includes the replicas that have been ordered but
   are not yet serving, so the same capacity is never ordered twice.
2. **A cascade that gives each actuator one owner.** A 5 s inner loop owns CPU: it raises
   CPU as soon as a surge is statistically visible and returns it shortly after the surge
   passes. A 15 s outer loop owns replicas and changes them only when CPU resizing cannot
   absorb the change.
3. **Risk-priced capacity.** The amount of capacity is chosen by minimising expected SLO
   violations plus a price per core over a set of demand scenarios drawn from conformal
   forecast errors.

We make no claim to be the first to resize in place before scaling out (Themis does this
for inference serving) or the first to optimise both axes jointly (Ship et al. pose it as a
fluid optimal-control problem). Our contribution is the combination above, an evaluation
protocol that separates design data from verdict data, and an honest account of which parts
of the design carry the benefit: in our ablation two mechanisms the design started from, a
cross-coupling penalty and a Lyapunov safety filter, turn out to change no decision, and
the risk-priced sizing helps only when its latency model is roughly right.

**Results in brief.** In simulation, at equal CPU cost, CHIMERA has fewer SLO violations than each of six baselines on 47 of 48 trace-by-baseline comparisons on a held-out set and on all 60 on ten real traces that no design step used; the mean advantage over the two strongest baselines there is 2.0 points (AAPA) and 1.3 points (KOSMOS). It makes about 31 in-place resizes and 0.5 replica changes per half hour. The advantage holds, reduced, under latency laws the controller does not assume. On a real cluster a single-run comparison with the HPA is inconclusive at matched cost.

## 2. Background and related work

**In-place resize.** KEP-1287 lets the kubelet change a running container's CPU and memory
through the `pods/resize` subresource. For CPU this is a cgroup update and takes effect
without a restart. Hsieh and Chou study the feature for serverless cold starts; Sponge
uses it to follow changing SLOs in inference serving.

**Joint horizontal and vertical scaling.** Autopilot scales both axes at Google from usage
history, with slack and out-of-memory rate as objectives. SHOWAR sizes pods from the
variance of historical usage and scales replicas with a controller on kernel metrics.
KOSMOS, the closest system to ours, resizes containers with a proportional-integral
controller on response time every few seconds and adds or removes replicas with heuristics
on a much slower period. Themis absorbs a surge with in-place vertical scaling and moves to
horizontal scaling once the workload settles. Ship et al. derive an optimal policy for
resources per replica and replica count from a fluid model. CHIMERA differs from KOSMOS and
Themis in planning both actuators from one target with an explicit model of in-flight
replicas, and from Ship et al. in using a feedback law with a fast inner loop instead of an
open-loop plan.

**Predictive and uncertainty-aware scaling.** MagicScaler forecasts demand with a
Gaussian-process model and scales with a loss that trades running cost against the risk of
violating quality of service; this is the nearest precedent for our risk-priced sizing.
AAPA classifies serverless workloads into archetypes and applies a strategy per archetype
with calibrated confidence. FIRM localises the microservice responsible for an SLO
violation and reprovisions it. All three act on one axis. We found conformal prediction
used for virtual-machine right-sizing and runtime prediction but not, in our search, in an
autoscaler's control loop.

## 3. Design

Every 15 s the outer loop runs steps 3.1 to 3.4; every 5 s
the inner loop runs step 3.5.

### 3.1 Forecast

A gradient-boosted quantile model, conformalised on a held-back window (CQR), gives a 90 %
interval for next-minute demand. Because a learned model has nothing to say about a regime
it has not seen, a second, model-free margin is kept alongside it: the last observed value
plus a split-conformal quantile of recent one-step changes. An ADWIN detector on the
forecast error discards that margin's history when the regime changes. The operator, which
has no training history for a newly attached service, uses the model-free construction
around an exponentially weighted mean.

### 3.2 Risk-priced capacity

Let `u` be utilisation and suppose a request misses the latency objective with probability
`p(u) = exp(-k (1 - u))` below saturation, where `k` is the ratio of the objective to the
mean service time, estimated online. Given demand scenarios `L_1 ... L_n` (the current
load, the current load plus each recent signed one-step change, and the forecast upper
bound), total capacity `c` is

    c* = argmin_c  (1/n) sum_i L_i p(L_i / c)  +  lambda c,     lambda = p'(u0) u0^2.

The price `lambda` is fixed by a single knob `u0`: with it, a demand known with certainty
gives `c* = L / u0`, so `u0` reads as an ordinary target utilisation. Under uncertainty the
convexity of `p` decides how much headroom is worth buying. The objective is not convex
(violation probability saturates near full utilisation), so it is minimised on a grid and
refined, over capacities that keep the expected load below saturation.

### 3.3 Target split and replica band

Capacity is split into a target `x* = [v*, h*]` (CPU per pod, replicas) with
`h* v_max >= c*`. Between `ceil(c* / (0.85 v_max))` and `floor(c* / v_min)` replicas, any
replica count can carry `c*` by resizing CPU alone, while every added replica spends its
boot time allocated but not serving. Inside this band `h*` is left at the current count.
`v*` is sized for the replicas that are serving now, so the fast actuator covers the gap
while ordered replicas boot and hands the capacity back as they arrive.

### 3.4 Delay-augmented regulator

The tracking error is augmented with the replicas in flight:

    e = [ v - v*,  h_serving - h*,  dh(t-1), ..., dh(t-d) ],     u = -K e,

with `K` from the discrete algebraic Riccati equation for weights `Q = diag(30, 10)`,
`R = diag(0.05, 1)`, and `d = floor(tau / dt)` slots for a boot time `tau` and control
period `dt` (one slot for 20 s and 15 s). Only scale-ups enter the buffer; a removed
replica stops serving at once. The action is clipped to the resource bounds and a total
capacity budget. Closed-loop stability follows from the Riccati solution `P`:
`V = e'Pe` decreases along `u = -Ke`.

On a real cluster boot time is not a constant. The operator therefore reconciles the
buffer with what the cluster reports, taking the replicas in flight to be the desired
count minus the ready count, before each step (Section 7 describes the failure this
prevents).

### 3.5 Inner loop

Between outer steps only CPU moves. Every 5 s the inner loop recomputes the capacity of
Section 3.2 for the serving replicas and

- raises CPU per pod at once if the need exceeds the allocation by a relative dead-band, or
  if a Poisson test on a short arrival window shows a significant surge;
- lowers it once the need has stayed 15 % below the allocation for two consecutive ticks.

Acquiring CPU takes about 0.1 s, so there is no reason to hold it through a long cooldown.
The dead-band bounds how often pods are resized.

### 3.6 Implementation

The controller is about 350 lines of Python; one control step takes about 0.3 ms. The
Kubernetes operator watches a `ChimeraAutoscaler` custom resource, reads CPU usage and
optionally request rate from Prometheus, resizes CPU through `pods/resize`, changes
replicas through the Deployment scale subresource, and writes its decision to the
resource's status.

## 4. Evaluation method

### 4.1 Simulator

We extend the discrete-event simulator released with AAPA to two dimensions: pods have a
CPU size, a replica takes 20 s to boot, and an in-place resize takes 0.1 s. Requests have
exponentially distributed CPU service times with mean 0.1 s and are slowed by a factor
`1/(1-u)` at utilisation `u`; the latency objective is 0.5 s. Section 6.4 replaces both the
distribution and the slowdown law.

### 4.2 Workloads and splits

| Set | Traces | Role |
|---|---|---|
| Dev | 5 Azure Functions 2019 excerpts (minutes 40-70) and 4 synthetic shapes; seeds 7, 11, 13 | every design choice |
| Held-out | the same 5 Azure excerpts (minutes 70-100) and 4 different synthetic shapes: flash crowd, diurnal, step-down, on/off; 10 new seeds | verdict |
| External | 6 windows of the NASA Kennedy Space Center web log (July 1995) and 4 of the ClarkNet log (August-September 1995), fixed by calendar rule | verdict on real traces no design round saw |

All sets use an unscored 20-minute warm-up so that no controller is scored on its cold
start. The Azure excerpts appear in both the dev and the held-out set, in different
windows; the external traces exist to remove that dependence.

### 4.3 Baselines

HPA; VPA with five fixed replicas; HPA and VPA together; AAPA; a scaler in the style of
MagicScaler (our implementation of the idea, not the authors' code); and KOSMOS,
reimplemented from the paper with its gains tuned on the dev set. Every controller has the
same resource bounds and starts from the same allocation.

### 4.4 Comparison at equal CPU

Controllers at their default settings sit at different points of the cost/SLO trade-off,
so a lower violation rate may only mean more CPU was spent. Every controller is therefore
swept over its own conservatism setting (target utilisation; for KOSMOS its response-time
setpoint), and each baseline's violation rate is interpolated on its own frontier at
CHIMERA's CPU cost, trace by trace, wherever the two cost ranges overlap. Significance is
tested on one gap per trace and seed (the same request stream) with a one-sample Wilcoxon
signed-rank test, Holm-corrected across baselines.

## 5. Results on the held-out set

The held-out set has 9 traces (5 Azure excerpts, 4 synthetic shapes). The main comparison
is 3,870 runs (43 configurations, 10 seeds); the frontier sweep is 4,095 runs (5 seeds).

### 5.1 Equal CPU

Table 1 gives, for each baseline, the number of traces on which CHIMERA has fewer SLO
violations at the same CPU cost, and the mean difference in violation rate.

**Table 1. Held-out set, equal CPU. Gap = baseline minus CHIMERA, percentage points.**

| Baseline | Azure traces won | Mean gap | Synthetic traces won | Mean gap | p, all traces |
|---|---:|---:|---:|---:|---:|
| AAPA | 5/5 | +1.68 | 4/4 | +5.28 | <0.001 |
| HPA | 5/5 | +4.71 | 4/4 | +8.04 | <0.001 |
| HPA+VPA | 5/5 | +4.26 | 4/4 | +6.27 | <0.001 |
| KOSMOS | 5/5 | +5.65 | 4/4 | +6.00 | <0.001 |
| MagicScaler-style | 4/5 | +4.72 | 4/4 | +7.31 | <0.001 |
| VPA | 3/3 | +2.81 | 3/3 | +9.69 | <0.001 |

Counting per-trace frontier comparisons, CHIMERA wins 47 of 48. The exception is the
MagicScaler-style scaler on one Azure trace (`w_0002`, a high, flat, noisy load), by
0.66 points. VPA is compared on fewer traces because its cost range, fixed by its five
replicas, does not always overlap CHIMERA's.

### 5.2 Default settings

**Table 2. Held-out set, each controller at its default setting.**

| Controller | Azure: SLO viol. | p99 (s) | CPU (core-min) | Synthetic: SLO viol. | p99 (s) | CPU | Actions/run |
|---|---:|---:|---:|---:|---:|---:|---:|
| CHIMERA | 5.93 % | 0.82 | 251.5 | 5.63 % | 0.81 | 71.8 | 32.5 |
| AAPA | 6.92 % | 0.91 | 236.8 | 7.02 % | 2.98 | 101.9 | 6.7 |
| KOSMOS | 8.71 % | 3.87 | 326.7 | 3.42 % | 0.81 | 132.1 | 274.8 |
| HPA+VPA | 14.06 % | 1.73 | 194.8 | 13.29 % | 3.22 | 65.2 | 19.2 |
| HPA | 15.60 % | 3.65 | 190.8 | 15.13 % | 8.54 | 63.7 | 14.5 |
| MagicScaler-style | 16.78 % | 2.69 | 181.1 | 14.41 % | 7.24 | 63.2 | 16.1 |
| VPA | 19.40 % | 4.79 | 169.2 | 15.16 % | 2.55 | 78.7 | 10.8 |

Against AAPA on the Azure traces CHIMERA has 0.99 points fewer violations (p = 0.003) for
a CPU difference of +14.7 core-minutes that is not significant (p = 0.086); on the
synthetic shapes it has 1.39 points fewer violations with 30 % less CPU.

Two things in Table 2 do not favour CHIMERA. At default settings KOSMOS has fewer
violations on the synthetic shapes (3.42 % against 5.63 %), at 1.8 times the CPU; the
equal-CPU comparison is what separates them. And at default settings CHIMERA has the lower
seed-mean violation rate than AAPA on only 3 of the 5 Azure traces.

### 5.3 Where the benefit comes from

**Table 3. Ablation on the held-out set. Each row removes one mechanism, or re-adds one.**

| Variant | Azure SLO | Azure CPU | Synthetic SLO | Synthetic CPU | Resizes/run | Pod changes/run |
|---|---:|---:|---:|---:|---:|---:|
| Full controller | 5.93 % | 251.5 | 5.63 % | 71.8 | 32.5 | 0.5 |
| without fast release | 2.24 % | 480.1 | 2.44 % | 185.6 | 1.0 | 0.5 |
| horizontal only | 4.80 % | 271.7 | 3.75 % | 114.8 | 12.0 | 12.0 |
| without the inner loop | 4.92 % | 274.6 | 5.01 % | 81.2 | 37.2 | 0.4 |
| inner loop does not own CPU | 5.12 % | 267.4 | 4.90 % | 75.7 | 79.7 | 0.5 |
| without scale-up dead-band | 4.96 % | 269.1 | 4.80 % | 75.8 | 84.1 | 0.5 |
| without replica band | 5.88 % | 256.2 | 5.61 % | 74.3 | 43.3 | 8.6 |
| without CQR | 6.21 % | 242.6 | 5.62 % | 69.8 | 37.8 | 0.4 |
| + coupling penalty | 5.93 % | 251.5 | 5.63 % | 71.8 | 32.5 | 0.5 |
| + Lyapunov filter | 5.93 % | 251.5 | 5.63 % | 71.8 | 32.5 | 0.5 |
| + anti-chattering guardrail | 5.93 % | 251.5 | 5.63 % | 71.8 | 32.5 | 0.5 |

Rows are single operating points: a row with fewer violations and more CPU is a different
point on the trade-off, not a better controller. Read that way:

- *Fast release* is the largest effect. Without it CPU nearly doubles on the Azure traces
  and is 2.6 times higher on the synthetic shapes.
- *Joint control* saves 7 % CPU on the Azure traces and 37 % on the synthetic shapes
  against horizontal-only control, with 0.5 pod changes per run instead of 12.
- *Single ownership of CPU* and the *dead-band* each remove more than half of the resizes
  that would otherwise occur.
- *The replica band* cuts pod changes from 8.6 to 0.5 per run.

The last three rows are negative results. The design began with a penalty on opposing
moves of the two actuators and a Lyapunov filter on the action; re-adding either, or the
guardrail against scaling in while replicas boot, leaves every one of the 90 runs
identical. Once each actuator has a single owner and replicas move rarely, opposing moves
do not arise, and the filter enforces a decrease the regulator already guarantees. The
reported controller does not run them.

### 5.4 Without in-place resize

If a CPU change requires a rolling restart (no KEP-1287), CHIMERA's violation rate on the
synthetic shapes rises from 5.63 % to 7.91 %. The design depends on the fast actuator.

## 6. Does the result generalise?

### 6.1 Real traces no design round saw

The Azure excerpts are few and were visible, in an earlier window, during design. We
therefore built ten further traces from two public web-server logs (NASA Kennedy Space
Center, July 1995; ClarkNet, August-September 1995), with windows fixed by calendar rule
before any controller ran on them, and ran the unchanged controllers (900 main runs, 3,150
sweep runs).

**Table 4. Ten external real traces, equal CPU.**

| Baseline | Traces won | Mean gap (pp) | p (Holm) |
|---|---:|---:|---:|
| AAPA | 10/10 | +2.00 | <0.001 |
| HPA | 10/10 | +4.53 | <0.001 |
| HPA+VPA | 10/10 | +3.89 | <0.001 |
| KOSMOS | 10/10 | +1.27 | <0.001 |
| MagicScaler-style | 10/10 | +4.66 | <0.001 |
| VPA | 10/10 | +2.20 | <0.001 |

CHIMERA wins all 60 comparisons. At default settings it has 6.18 % violations at 190
core-minutes against AAPA's 7.45 % at 194. KOSMOS is the closest competitor: 1.27 points
behind at equal CPU, and at its default setting it has fewer violations than CHIMERA
(4.22 %) for 43 % more CPU and ten times as many resizes.

A first version of this experiment scaled the request rates too high: the horizontal-only
baselines sat at their replica limit on every ClarkNet window and CHIMERA "won" 56 of 58
comparisons for the wrong reason. We discarded it, rescaled so that the busiest minute fits
the baselines' limit, and report only the re-run.

### 6.2 A latency model the controller does not assume

The capacity rule of Section 3.2 assumes the same latency law the simulator of Section 4.1
generates. To test whether the result depends on that match, we re-ran the held-out sweep
under three other service-time distributions (lognormal, gamma, bimodal; same mean) and two
other slowdown laws (`1 + u/(2(1-u))`, and a hockey-stick `1/(1-u^4)`), without re-tuning
anything.

**Table 5. Held-out sweep under other latency models. Last column: the controller with
risk-priced capacity against the same controller with a fixed utilisation target.**

| Service times | Slowdown | Comparisons won | Mean gap (pp) | Risk pricing: traces won, gap (pp) |
|---|---|---:|---:|---|
| exponential | 1/(1-u) (assumed) | 42/42 | +5.90 | 9/9, +1.12 |
| lognormal | 1/(1-u) | 42/42 | +5.41 | 9/9, +0.91 |
| gamma | 1/(1-u) | 41/41 | +7.27 | 9/9, +1.01 |
| exponential | 1 + u/(2(1-u)) | 42/42 | +4.49 | 9/9, +0.47 |
| exponential | 1/(1-u^4) | 42/42 | +3.30 | 9/9, +0.09 |
| lognormal | 1/(1-u^4) | 42/42 | +3.07 | 9/9, +0.09 |
| bimodal | 1/(1-u) | 38/42 | +4.40 | 3/9, -0.06 |
| bimodal | 1 + u/(2(1-u)) | 37/42 | +2.87 | 1/9, -0.17 |

(This table was produced with the previous dead-band setting; Section 5.3 shows the change
to the dead-band moves the frontier little.)

The advantage over the baselines survives in every scenario, on every trace in six of
eight. With bimodal service times CHIMERA loses to AAPA at equal CPU on four traces, by
0.1 to 0.7 points. The last column isolates the capacity rule: it is worth about one point
while the slowdown law matches its assumption, nothing under the hockey-stick law, and
slightly less than nothing with bimodal service times. On the external traces of Section
6.1 it is worth 0.24 points. The structural parts of the design carry the robust part of
the result; the capacity rule is a refinement that needs its latency model to be roughly
right.

## 7. On a real cluster

The same control step runs in a Kubernetes operator. We used it for two things: to check
that the controller's assumptions hold outside the simulator, and to measure latency
against the stock HPA.

### 7.1 Functional validation, and what it found

On Kubernetes 1.33 (a local kind cluster, and one EC2 instance running k3s) the operator
raised the CPU of running pods in place under load with no container restart, added a
replica once CPU alone could not cover the need, returned CPU when the new replica was
serving, and released everything when the load stopped.

The first run also removed a replica 15 s after adding it. The controller's in-flight
buffer assumed the configured boot time of 20 s; the real pod was ready within one control
period, so it was counted both as serving and as in flight. The simulator, whose boot time
is a constant, could not show this. The operator now reconciles the buffer with the
cluster before each step. We report this because it is the reason to run on a cluster at
all: it tests the assumption the simulator bakes in.

### 7.2 Trace replay against the HPA

An open-loop load generator replayed a 24-minute demand trace (12 minutes of an Azure
trace, then a flash crowd to five times the mean) against a CPU-bound service costing
61 ms of CPU per request, with a 0.3 s latency objective (five times the service time, as
in the simulator). Each controller started from the same allocation and was given three
unscored lead-in minutes.

**Table 6. Live replay, one run per controller.**

| Controller | SLO violations | CPU (core-min) | Steady phase: viol. / CPU | Spike phase: viol. / CPU |
|---|---:|---:|---|---|
| HPA, 70 % target | 51.1 % | 72.8 | 23.7 % / 38.8 | 66.8 % / 34.0 |
| HPA, 50 % target | 35.5 % | 77.1 | 18.4 % / 40.4 | 45.2 % / 36.8 |
| CHIMERA, CPU signal | 33.2 % | 83.4 | 19.9 % / 32.9 | 41.0 % / 50.5 |
| CHIMERA, request-rate signal | 28.6 % | 81.8 | 23.5 % / 34.2 | 31.6 % / 47.7 |

In steady load CHIMERA used 15 to 19 % less CPU than either HPA setting for a similar
violation rate. In the flash crowd it had fewer violations and spent more: with the
request-rate signal, 31.6 % against 45.2 % for the better HPA setting, with 30 % more CPU
in that phase. Overall it had the fewest violations at the highest cost, 6.9 points below
the HPA at a 50 % target for 6 % more CPU.

We do not claim an equal-cost win from this experiment. There is one run per controller
and two HPA points, which is neither a frontier nor a sample. Every controller violated the
objective far more often than in simulation, because a 61 ms request on a pod limited to
half a core exceeds a single CFS quota period; the simulator's latency law is kinder than
this service. The request-rate signal reached a given allocation 20 to 25 s sooner than the
default one-minute CPU-usage rate after the spike began.

## 8. Discussion and limitations

**What carries the result.** The fast actuator and single ownership. Returning CPU within
two ticks of a surge passing, instead of holding it through a cooldown, accounts for about
half of the CPU a controller with only slow release would spend. Giving CPU to one loop
and replicas to another, with a band in which replicas do not move, removes the
interference that the original design tried to penalise away.

**What does not.** Risk-priced capacity is worth about a point when the latency law
matches its assumption and nothing when it does not (Table 5). It should be learned from
measured latency, not derived from a model, before it is relied on.

**KOSMOS is closer than we first reported.** An earlier version of this evaluation used
KOSMOS gains tuned before a simulator fix, with which its vertical loop oscillated across
its whole range; we reported a nineteen-point advantage. With gains re-tuned on the dev set
the advantage is 5.8 points on the held-out set and 1.3 on the external traces, and at
default settings KOSMOS has fewer violations than CHIMERA on two of the three trace groups
at substantially higher CPU.

**Limitations.**

1. *Simulation carries the quantitative claims.* The live replay is a single run per
   controller on a laptop and is inconclusive on cost-matched performance.
2. *Real traces.* Five short Azure excerpts and ten web-server windows from 1995. The
   Azure excerpts were visible, in a different window, during design. The web-server
   traces were not, but they are not serverless workloads.
3. *The held-out set has been scored twice,* once for the previous controller version and
   once for the final one; the final change was chosen on the dev set by a rule fixed
   beforehand.
4. *Baselines are our implementations.* KOSMOS and the MagicScaler-style scaler were
   written from the papers. VPA is run with five fixed replicas.
5. *Simulator fidelity.* Boot time and resize latency are constants, and the slowdown law
   is analytic. Section 7 shows a real service behaving worse at the same nominal
   utilisation.
6. *Resize volume.* About 31 in-place resizes per half hour per workload, against about 7
   pod changes for AAPA. The load this places on the API server and kubelet at fleet scale
   is not measured.
7. *CPU only.* Memory is not scaled; shrinking memory in place can fail.

## 9. Conclusion

A controller that treats replica count and CPU per pod as one problem, gives each actuator
a single owner, and uses the fast one to cover the slow one's delay holds a per-request
latency objective with fewer violations than HPA, VPA, their combination, AAPA, a
MagicScaler-style scaler and KOSMOS at the same CPU cost: on 47 of 48 comparisons on a
held-out set and on all 60 on ten real traces no design step saw. The advantage persists
under latency laws the controller does not assume, with a smaller margin. On a real
cluster the operator works and reacts faster than the HPA to a flash crowd, but our single
live experiment does not establish a cost-matched advantage; doing so, with repeated runs
and a service whose latency the simulator models faithfully, is the main work that
remains.

## References

Bibliographic details as verified on 12 October 2026 (`final_research_package/literature.md`
has the source links). Entries marked * were not re-verified online in that pass; check
them, and all page numbers, before submission.

1. L. Baresi, D. Y. X. Hu, G. Quattrocchi, L. Terracciano. KOSMOS: Vertical and Horizontal
   Resource Autoscaling for Kubernetes. ICSOC 2021, LNCS, pp. 821-829.
2. K. Rzadca et al. Autopilot: Workload Autoscaling at Google. EuroSys 2020.
3. A. F. Baarzi, G. Kesidis. SHOWAR: Right-Sizing and Efficient Scheduling of
   Microservices. SoCC 2021, pp. 427-441.
4. H. Ship, E. Shindin, C. Wang, D. Arroyo, A. Tantawi. Optimizing Simultaneous Autoscaling
   for Serverless Cloud Computing. IEEE CLOUD 2024. arXiv:2310.19013.
5. K. Razavi, M. Salmani, M. Muhlhauser, B. Koldehofe, L. Wang. A Tale of Two Scales:
   Reconciling Horizontal and Vertical Scaling for Inference Serving Systems.
   arXiv:2407.14843.
6. K. Razavi, S. Ghafouri, M. Muhlhauser, P. Jamshidi, L. Wang. Sponge: Inference Serving
   with Dynamic SLOs Using In-Place Vertical Scaling. EuroMLSys 2024.
7. V. Hsieh, J. Chou. Towards Serverless Optimization with In-place Scaling.
   arXiv:2311.09526.
8. AAPA: An Archetype-Aware Predictive Autoscaler with Uncertainty Quantification for
   Serverless Workloads on Kubernetes. arXiv:2507.05653. (Take the author list from the
   arXiv record.)
9. Z. Pan et al. MagicScaler: Uncertainty-aware, Predictive Autoscaling. PVLDB 16(12),
   pp. 3808-3821, 2023.
10. H. Qiu, S. S. Banerjee, S. Jha, Z. T. Kalbarczyk, R. K. Iyer. FIRM: An Intelligent
    Fine-Grained Resource Management Framework for SLO-Oriented Microservices. OSDI 2020.
11. * M. Shahrad et al. Serverless in the Wild: Characterizing and Optimizing the
    Serverless Workload at a Large Cloud Provider. USENIX ATC 2020.
12. * Y. Romano, E. Patterson, E. Candes. Conformalized Quantile Regression. NeurIPS 2019.
13. * A. Bifet, R. Gavalda. Learning from Time-Changing Data with Adaptive Windowing.
    SDM 2007.
14. Kubernetes Enhancement Proposal 1287, In-Place Update of Pod Resources; stable in
    Kubernetes 1.35.
15. Internet Traffic Archive, NASA-HTTP and ClarkNet-HTTP traces.
    https://ita.ee.lbl.gov/html/traces.html

## Where each number comes from

| Section | Source |
|---|---|
| 5.1, 5.2, 5.3, 5.4 | `results/REPORT_v6.md` (`results/results_v6.csv`, `results/results_v6_sweep.csv`) |
| 6.1 | `results/REPORT_ext.md` |
| 6.2 | `results/robustness/ROBUSTNESS.md` |
| 7.1 | `results/live/LIVE_REPORT.md`, `results/live_ec2/README.md` |
| 7.2 | `results/replay/REPLAY_REPORT.md` and the phase breakdown in `IMPROVEMENT_AUDIT.md`, item 3 |
| KOSMOS re-tuning | `IMPROVEMENT_AUDIT.md`, item 4; `results/kosmos_retune_dev.csv` |

## Not yet done for a submission

- Venue formatting, figures placed in the text, and a figure for the architecture.
- A full-text literature pass (the related-work section is built from abstracts).
- Repeated live-replay runs.
