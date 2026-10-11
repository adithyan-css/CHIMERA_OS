# CHIMERA on AWS (EC2 + k3s)

12 Oct 2026, region ap-south-1. One `t3.small` instance (2 vCPU, 2 GB), Amazon Linux 2023,
k3s v1.33.13 (in-place pod resize on by default), deployed by `aws/ec2/deploy.sh`. The
instance builds the operator image from the public repository at first boot. No inbound
ports are open; all access is through AWS Systems Manager.

| Run | Load | Result |
|---|---|---|
| `run1_insufficient_load/` | 2 load generators | Stack healthy (metrics, status), but 0.37 cores of load on 0.5 allocated did not call for a resize, so checks 2 and 4 could not pass |
| this directory | 5 load generators | **all 5 checks pass** (`LIVE_REPORT.md`) |

Second run: CPU per pod raised in place 0.25 -> 0.30 -> 0.37 cores with 0 container
restarts; a third replica added at t = 341 s; CPU per pod handed back (0.37 -> 0.31) ten
seconds later; replica removed 165 s after the scale-out, once the load had stopped.

The demo is sized for the node (pods 250m-500m, 2-3 replicas), so the absolute numbers are
small. Like the kind runs this is a functional test, not a performance measurement.

The instance is left running with no load generator, in `standard` CPU-credit mode so it
cannot accrue burst charges. Remove everything with `aws/ec2/teardown.sh`.
