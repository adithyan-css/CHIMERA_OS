# CHIMERA live-cluster validation: summary

11 Oct 2026. kind cluster, one node, Kubernetes v1.33.1 (in-place pod resize on by
default), Prometheus scraping cAdvisor every 10 s, operator image built from this
repository. Target: `billing-service` (`registry.k8s.io/hpa-example`, CPU-bound), driven by
closed-loop `wget` load generators. Each run: 120 s baseline, 300 s surge, 240 s with no
load, sampled every 5 s by `k8s/live_validation.py`.

Each run directory holds the generated report, `timeline.csv` (raw samples) and
`operator.log` (every decision).

| Run | Setup | Result |
|---|---|---|
| `run1/` | max 2 cores/pod, 4 generators | **Bug found**: a pod was added and removed 15 s later |
| `run2/` | fix deployed; max 2 cores/pod, 6 generators | 4 checks pass; replicas not needed, so check 3 not exercised |
| `run3/` | fix deployed; max 1 core/pod, 6 generators | **all 5 checks pass** |

## What the runs establish

| # | Check | Evidence |
|---|---|---|
| 1 | The operator works from real metrics | Prometheus returned CPU usage in 132 of 132 samples in every run |
| 2 | CPU is resized in place, with no restart | run 1: 0.5 -> 1.61 cores; run 2: 0.5 -> 1.07; run 3: 0.5 -> 0.82. Same pod UIDs before and after, 0 container restarts in all runs, and the kubelet-applied limit matched the requested one |
| 3 | Replicas scale out when CPU alone cannot cover the need, and do not flap | run 3: 2 -> 3 at t = 266 s, held for 225 s, 3 -> 2 at t = 491 s after load stopped |
| 4 | CPU is released when load drops | back to 0.5 cores/pod within about 80 s of the load stopping in every run |
| 5 | Status is written back | `kubectl get chas` shows replicas, CPU per pod and the last decision |

The fast actuator is used first: in run 3 CPU per pod was raised four times
(0.50 -> 0.58 -> 0.64 -> 0.72 -> 0.82) before a pod was added, and CPU per pod was handed
back (0.82 -> 0.62) ten seconds after the new pod was serving.

## The bug run 1 found

The in-flight buffer assumed a new pod takes 20 s to serve. On this cluster it was Ready
within one 15 s tick, so it was counted as serving and as in flight, and the controller
removed it. Fixed in `k8s/operator_core.py` (`_reconcile_inflight`), with two regression
tests; see `run1/README.md` and `CODE_FIXES_LOG.md` item 19. Runs 2 and 3 use the fix.

## What these runs do not show

- **No performance comparison.** There is no latency measurement and no baseline (HPA,
  VPA) on the same cluster. SLO and cost figures in the paper come from the simulator.
- **One small cluster, one synthetic service.** A single kind node on a laptop.
- **Slow ramp.** Allocation climbed in steps over 2-5 minutes rather than jumping. The
  demand signal is a 1-minute rate of CPU usage, which is capped by the limit while pods
  throttle, and the load generator is closed-loop, so offered load only grows as capacity
  does. A request-rate metric was not configured.
- **Run-to-run variation.** Six generators produced less CPU load in runs 2 and 3
  (peak 1.7 and 1.4 cores) than four did in run 1 (peak 2.4), for the reason above.
