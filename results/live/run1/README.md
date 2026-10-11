# First live run (11 Oct 2026): kept as evidence of a bug

This is the first time the operator ran on a real cluster (kind, Kubernetes 1.33.1).

What worked: Prometheus metrics, in-place CPU resize from 0.5 to 1.61 cores on the same
two pods with zero container restarts, CPU release when load dropped, status write-back.

What did not: at t = 442 s the operator added a third pod and removed it 15 s later
(`operator.log`, 16:49:42 and 16:49:57).

Cause: the in-flight buffer assumes an ordered pod takes `podStartupSeconds` (20 s) to
serve. On this cluster the pod was Ready within one 15 s tick, so it was counted both as
serving and as still in flight; the controller saw four pods for a target of three and
removed one. The simulator never shows this because its boot time is exactly 20 s.

Fix: `TargetState._reconcile_inflight` in `k8s/operator_core.py` makes the buffer agree
with what the cluster reports (desired replicas minus Ready ones) before each joint step.
Replaying this run's logged observations through the fixed code gives no scale-in at that
tick. Regression tests: `test_pod_ready_sooner_than_assumed_is_not_removed` and
`test_inflight_buffer_adopts_pods_it_did_not_order` in `tests/test_operator.py`.

Check 3 in this run's report ("replicas added only near maximum CPU per pod") was also
mis-specified: the controller adds replicas when the required cores exceed what the
current pods can hold at 85 % of their maximum, which can happen while the per-pod
allocation is still climbing. It was replaced by a no-flapping check for the second run
(`../LIVE_REPORT.md`).
