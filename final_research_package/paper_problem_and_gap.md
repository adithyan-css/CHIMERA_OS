# Problem statement and research gap

## Problem

A Kubernetes service can be scaled along two axes: the number of pod replicas (`h`) and
the CPU given to each pod (`v`). The two actuators are very different. A new pod takes
about 20 s to boot; an in-place CPU resize (KEP-1287, on by default from Kubernetes 1.33)
takes about 0.1 s and does not restart the container.

The stock tools treat the axes separately. HPA changes replicas from average utilisation;
VPA changes pod size from usage history. Run together on one CPU signal they interfere:
an HPA scale-out lowers per-pod utilisation, VPA shrinks the pods, utilisation rises, and
HPA scales out again. The result is wasted capacity, SLO violations during the 20 s boot
gap, or both.

The question this work answers: given one slow actuator and one fast actuator on the same
resource, how should a single controller use both to hold a latency SLO at the lowest CPU
cost?

## Gap in prior work

1. **Horizontal-only predictive scalers** (AAPA, MagicScaler) use forecasts with
   uncertainty but have only the slow actuator, so they pay for uncertainty with standing
   headroom.
2. **Joint scalers with separate loops** (HPA+VPA, KOSMOS) coordinate by heuristics or by
   timescale separation. They do not model that an ordered pod is not yet serving, so they
   can order the same capacity twice or leave the boot gap uncovered.
3. **Sizing rules are fixed-utilisation.** A target of "70 %" is applied whatever the
   forecast uncertainty is, although the cost of being wrong is strongly convex in
   utilisation.

CHIMERA addresses these with one delay-aware control law for both actuators, a cascade
that lets the fast actuator cover the slow one's delay, and capacity sizing that prices
SLO risk against CPU explicitly.

## What is no longer claimed

The August draft presented a cross-coupling penalty and a Lyapunov safety filter as the
main contributions. The v5 ablation shows both are inert: removing either gives identical
runs (100 % of runs bit-identical). They are reported as negative results, not as
contributions. See `paper_ablation.md`.
