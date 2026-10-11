# CHIMERA build and completion plan

Status as of 11 Oct 2026. Each phase lists what "done" means and how to reproduce it.

| Phase | What | Status |
|---|---|---|
| 0 | Secure the work in git and publish to GitHub | in progress |
| 1 | Local build and tests | done |
| 2 | Live-cluster validation (kind, Kubernetes 1.33) | done |
| 3 | Paper package rewritten against v5 | done |
| 4 | Repository clean-up | done |
| 5 | AWS hosting (EKS) | prepared, needs your AWS account |

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

"Hosting" an autoscaler means running the operator on a managed cluster: Amazon EKS on
Kubernetes 1.33 or later (in-place resize is on by default), with the operator image in
Amazon ECR.

Prepared in `aws/`:

- `aws/cluster.yaml` - eksctl cluster definition (2 x t3.xlarge, Kubernetes 1.33);
- `aws/autoscaler-eks.yaml` - the demo autoscaler sized for those nodes;
- `aws/deploy.sh` - create the ECR repository, build and push the image, create the
  cluster, apply the same manifests as the kind run;
- `aws/teardown.sh` - delete the cluster and the repository so nothing keeps billing.

What only you can do:

1. Have an AWS account with billing enabled.
2. Install the AWS CLI and `eksctl`.
3. Run `aws configure` yourself with your own access key. Credentials are never typed
   into this session.
4. Choose the region and accept the spend. As a rough guide, the EKS control plane is
   about 0.10 USD/hour and two t3.xlarge nodes about 0.35 USD/hour together, so around
   11 USD per day while the cluster is up. Check current prices for your region first.

Then:

```bash
AWS_REGION=ap-south-1 ./aws/deploy.sh
```

```bash
python k8s/live_validation.py --out results/live_eks --high 3
```

```bash
AWS_REGION=ap-south-1 ./aws/teardown.sh
```

Done when `results/live_eks/LIVE_REPORT.md` passes the same checks as the kind run.
