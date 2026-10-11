# Conclusion

CHIMERA scales replicas and CPU per pod from one delay-aware control law. A cascade gives
the 0.1 s in-place resize actuator to a 5 s inner loop and keeps the 20 s replica actuator
for changes the fast one cannot absorb, and capacity is sized by pricing SLO risk against
CPU over conformal demand scenarios.

On a held-out evaluation (five real Azure Functions traces, four unseen synthetic shapes,
ten fresh seeds, 3,510 runs plus a 3,780-run frontier sweep) CHIMERA has fewer SLO
violations than HPA, VPA, HPA+VPA, AAPA, a MagicScaler-inspired scaler and KOSMOS at equal
CPU cost on every trace: 44 of 44 comparisons, all significant. Against the strongest
baseline, AAPA, the advantage at equal CPU is 2.2 percentage points on the real traces and
5.4 on the held-out synthetic ones.

The ablation shows where the gain comes from (fast CPU release, joint control, single
ownership of each actuator, the replica band, risk sizing) and also that two mechanisms
the design started from, the coupling penalty and the Lyapunov filter, are inert in the
final controller.

The same control law runs as a Kubernetes operator and has been exercised on a live
Kubernetes 1.33 cluster, resizing CPU in place without restarting containers.

## Future work

1. Evaluate on the full Azure Functions dataset, or another public trace set, once
   accessible, to remove the dependence on five short real traces.
2. Measure latency and cost on a real cluster under replayed traces, against HPA and VPA
   running on the same cluster.
3. Learn `k` in the operator from live latency histograms.
4. Extend to memory, where shrinking in place can fail.
5. Measure API-server and kubelet load of the resize rate at fleet scale.
