# Contributions

Each contribution is listed with the evidence for it. All simulation numbers are from
`results/REPORT_v5.md` (held-out evaluation).

1. **A delay-augmented joint control law for replicas and CPU.** The state
   `e = [v - v*, h_serving - h*, dh(t-1)]` carries ordered-but-not-booted replicas in an
   in-flight slot, so the controller never orders the same capacity twice. One slot is
   exact for a 20 s boot on a 15 s loop.
   *Evidence:* horizontal-only control (`w/o Joint_H_V`) needs 8 % more CPU on the real
   traces and 55 % more on the synthetic ones, with 12 pod changes per run against 0.4.

2. **Cascade control that matches each actuator to its timescale.** A 5 s inner loop owns
   the 0.1 s CPU actuator (fast attack on a statistically significant surge, release
   after two ticks 15 % below the allocation); the 15 s outer loop owns replicas. A replica
   band keeps the pod count fixed while CPU resizing alone can absorb the change.
   *Evidence:* without fast release CPU nearly doubles (244 to 455 core-min); without the
   replica band pod changes rise from 0.4 to 7.6 per run; letting both loops move CPU
   raises resizes from 60 to 87.

3. **Risk-optimal capacity sizing.** Capacity minimises expected SLO violations plus a
   price per core under `p(u) = exp(-k(1-u))`, over conformal demand scenarios, with `k`
   learned online. With a certain forecast it reduces exactly to `demand / target`.
   *Evidence:* v5 beats v4 (same controller without risk sizing) at equal CPU on 9 of 9
   traces; on the held-out synthetic shapes it reaches 5.9 % violations at 68 core-min
   against v4's 6.7 % at 79.

4. **An evaluation protocol that separates design from verdict.** A dev split (earlier
   window, dev-only traces and seeds) for every design choice; a held-out set (four unseen
   synthetic shapes, ten fresh seeds) scored once; an unscored 20-minute warm-up; and an
   equal-CPU frontier comparison instead of comparing controllers at arbitrary operating
   points.
   *Evidence:* the protocol overturned one of our own earlier claims (a 19x p99 advantage
   over AAPA that was mostly AAPA's cold start; the real figure is 1.1x).

5. **A faithful KOSMOS baseline and corrected stock baselines.** KOSMOS (ICSOC 2021) is
   reimplemented with gains tuned on held-out data and reproduces the paper's own
   pod-minute reduction against HPA (49.5 %, inside the reported 44-57 %). Bugs that had
   handicapped MagicScaler (3x under-provisioning) and HPA+VPA (VPA loop never fired) were
   fixed before comparing.

6. **A working Kubernetes operator running the same control law.** CRD, Prometheus
   metrics, in-place resize through `pods/resize`, replicas through the scale subresource.
   *Evidence:* `results/live/LIVE_REPORT.md` (live run on Kubernetes 1.33).

## Headline result

At equal CPU cost CHIMERA has fewer SLO violations than every baseline on every trace:
44 of 44 trace-baseline comparisons, each significant (Wilcoxon, Holm-corrected,
p < 0.001).
