# Discussion and limitations

## Discussion

**Why the fast actuator changes the problem.** A horizontal-only scaler can only buy
safety with standing replicas, because anything it orders arrives 20 s late. With a 0.1 s
actuator, headroom can be acquired when a surge is statistically visible and returned two
ticks after it passes. Most of CHIMERA's advantage comes from this: removing fast release
nearly doubles CPU, and removing in-place resize altogether raises the synthetic-trace
violation rate from 5.9 % to 10.2 %.

**Why sizing by risk beats a fixed utilisation target.** Under the queueing model most
violations happen at moderate utilisation, and the violation probability is strongly
convex in utilisation. A fixed target spends the same headroom on a flat minute and a
volatile one. Risk sizing spends it where the scenario spread makes it pay, which is why
v5 is both cheaper and safer than v4 on the bursty held-out shapes.

**Simpler than designed.** The first design relied on a coupling penalty and a Lyapunov
filter to stop the two loops fighting. Once each actuator had a single owner (inner loop
for CPU, replica band for pods), conflicts stopped arising and those mechanisms became
inert. The structural fix made the penalty-based fix unnecessary.

**What the evaluation protocol caught.** Three of our own earlier claims did not survive
it: the 19x p99 advantage over AAPA (cold-start artefact), the "unbeatable" clairvoyant
bound (CHIMERA beats it by tracking within-minute bursts), and the first risk-sizing
implementation (a bisection that assumed a monotone marginal; caught by a unit test before
any held-out result was read).

## Limitations

1. **Five real traces, short excerpts.** The real traces are 100-minute excerpts of five
   Azure Functions 2019 traces. The full dataset's public URL now refuses access, so no
   fresh real traces could be added. The held-out verdict uses unseen synthetic shapes and
   fresh seeds, but the same five real traces that earlier design rounds were scored on
   (in a different window). This is the weakest part of the evidence.
2. **Self-authored synthetic traces.** The held-out shapes are our own designs.
3. **Simulated performance numbers.** Every SLO, latency and cost figure comes from the
   simulator. The live-cluster run (`results/live/LIVE_REPORT.md`) validates that the
   operator actuates correctly on real Kubernetes; it is a functional test on one small
   cluster with a synthetic load generator, not a performance comparison.
4. **Service model.** Risk sizing assumes the simulator's queueing-style latency model. In
   the operator, `k` comes from configured SLO and service-time values, not from measured
   latency histograms.
5. **CPU only.** Memory is not scaled. In-place memory resize has different semantics
   (shrinking can fail) and is out of scope.
6. **Actuation volume.** About 62 in-place resizes per run, against about 7 pod changes
   for AAPA. Resizes are cheap and do not restart containers, but the API-server and
   kubelet load of this rate at fleet scale has not been measured.
7. **Operator forecaster differs from the simulator's.** The simulator uses a LightGBM CQR
   model trained per trace; the operator uses online split-conformal intervals around an
   EWMA because a new target has no training history.
8. **Inert components** remain in the code base for ablation and add complexity that a
   production version would remove.

## Threats to validity

- *Adaptive overfitting* on the real traces, mitigated but not removed by the held-out
  shapes and seeds (limitation 1).
- *Baseline strength.* Baselines were debugged and KOSMOS was tuned on held-out data, and
  every controller is compared along its whole frontier. Baselines are still our
  reimplementations.
- *Simulator fidelity.* Boot time and resize latency are constants; real clusters have
  distributions, scheduling delays and resize deferrals.
