# Live-cluster validation

Source: `results/live/LIVE_REPORT.md` and the three run directories beside it.

## Setup

The operator (`k8s/manager.py`, running the control step in `k8s/operator_core.py`) was
deployed to a single-node kind cluster on Kubernetes v1.33.1, where in-place pod resize is
on by default. Prometheus scraped cAdvisor every 10 s. The target was a CPU-bound demo
service starting at 2 pods of 0.5 cores, driven by closed-loop load generators through
120 s of baseline, 300 s of surge and 240 s with no load. A separate script sampled the
cluster every 5 s.

## Results

| Property | Observed |
|---|---|
| Real metrics | Prometheus returned CPU usage in 132 of 132 samples in each run |
| In-place resize | CPU per pod raised from 0.5 up to 1.61 cores on the same pods; 0 container restarts in all runs; kubelet-applied limits matched the requested ones |
| Fast actuator first | Run 3: four CPU increases (0.50 to 0.82 cores) before a replica was added |
| Scale-out | Run 3: 2 to 3 replicas at t = 266 s, when the need exceeded what two pods could hold |
| Hand-back | Run 3: CPU per pod reduced from 0.82 to 0.62 ten seconds after the new pod was serving |
| Release | CPU per pod back to 0.5 within about 80 s of the load stopping, in every run |
| Scale-in | Run 3: 3 to 2 replicas 225 s after the scale-out, after the load stopped |

## A bug the simulator could not show

In the first run the operator added a pod and removed it 15 s later. The in-flight buffer
assumed a 20 s boot; the real pod was Ready within one tick and was counted both as
serving and as in flight. The simulator's boot time is exactly 20 s, so the mismatch never
arises there. The operator now reconciles the buffer with the cluster (desired minus Ready
replicas) before each step. Replaying the failing run's logged observations through the
fixed code removes the scale-in, and the two later runs show no flapping.

This is the argument for the live run: it tests the assumption the simulator bakes in.

## Scope

These runs show that the operator actuates correctly on a real cluster. They do not
measure latency or cost, do not include a baseline on the same cluster, and use one small
node and one synthetic service. Allocation climbed over 2-5 minutes rather than seconds
because the demand signal was a 1-minute CPU-usage rate under a closed-loop load
generator. Performance claims in this paper rest on the simulation.
