# CHIMERA build and completion plan

Status as of 11 Oct 2026. Each phase lists what "done" means and how to reproduce it.

| Phase | What | Status |
|---|---|---|
| 0 | Secure the work in git and publish to GitHub | done |
| 1 | Local build and tests | done |
| 2 | Live-cluster validation (kind, Kubernetes 1.33) | done |
| 3 | Paper package rewritten against v5 | done |
| 4 | Repository clean-up | done |
| 5 | AWS hosting (EC2 + k3s, free tier) | done |

## Phase 0 - Secure the work

- Published to `github.com/adithyan-css/CHIMERA_OS` (remote `chimera`, branch `main`) as
  ten commits. The first imports the MIT-licensed AAPA simulator base and says so.
- `results/` is tracked: the reports and raw runs are the evidence for the paper.
- `origin` still points at the upstream AAPA repository and is never pushed to.

## Phase 1 - Local build

Python 3.10+ (tested on 3.13).

```bash
pip install -r requirements.txt
```

```bash
python tests/test_chimera.py
```

```bash
python tests/test_operator.py
```

Done when both suites pass (18 and 29 tests).

## Phase 2 - Live-cluster validation

Needs Docker Desktop running, `kubectl`, and `tools/kind.exe` on `PATH`. The cluster uses
its own kubeconfig so it cannot touch any other cluster.

```bash
export PATH="$PWD/tools:$PATH" KUBECONFIG="$PWD/tools/kubeconfig-chimera"
```

```bash
bash k8s/quickstart.sh
```

```bash
python k8s/live_validation.py --out results/live/my_run
```

`live_validation.py` drives the load generator through low / high / low phases and samples
the cluster every 5 s. It writes `timeline.csv`, `operator.log` and `LIVE_REPORT.md` into
the `--out` directory.

Done when the report shows all of:

1. the operator reads real CPU usage from Prometheus;
2. CPU per pod is raised in place under load, with **zero container restarts**;
3. replicas scale out under load and are not removed again straight afterwards;
4. CPU is released again when load drops;
5. the `ChimeraAutoscaler` status is written back.

Result (11 Oct 2026): three runs, summarised in `results/live/LIVE_REPORT.md`. The first
found a bug in the operator, now fixed; the third passes all five checks.

Tear down:

```bash
bash k8s/quickstart.sh --delete
```

## Phase 3 - Paper package

`final_research_package/` dated from 18 Aug and described the pre-fix system (CHIMERA at
25.8 % violations, mocked operator). It is rewritten against `results/REPORT_v5.md`; the
August files are kept under `final_research_package/archive_2026-08/`.

Done when every number in the package traces to `results/REPORT_v5.md` or
`results/live/LIVE_REPORT.md`.

## Phase 4 - Repository clean-up

- `CODE_FIXES_LOG.md` cites `CHIMERA_CODE_VERIFICATION_ADDENDUM.md`, which is not in the
  repository: the references are marked as such.
- `.gitattributes` keeps shell scripts and the Dockerfile on LF line endings, so they still
  run in Linux containers after a Windows checkout.
- README updated with the live-run result.

## Phase 5 - AWS hosting

Done on 12 Oct 2026: `results/live_ec2/README.md`.

The operator runs on one free-tier-eligible EC2 instance (`t3.small`, k3s 1.33) in
ap-south-1. This fits an AWS Free plan account, where usage is drawn from credits (about
22 USD a month for the instance, its disk and a public IPv4 address; an estimate, check the
Billing console). No inbound ports are opened; access is through Systems Manager.

Sign in first (`aws login`, or `aws configure`), then from the repository root:

```bash
AWS_REGION=ap-south-1 ./aws/ec2/deploy.sh
```

```bash
AWS_REGION=ap-south-1 ./aws/ec2/validate.sh
```

```bash
AWS_REGION=ap-south-1 ./aws/ec2/teardown.sh
```

`aws/eks/` holds an Amazon EKS variant for a paid account. EKS charges for its control
plane and needs instance types a Free plan account cannot launch; it has not been run to
completion.
