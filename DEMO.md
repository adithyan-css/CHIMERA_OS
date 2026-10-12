# Lab demo runbook

Three parts, about 15 minutes in total. Part 1 needs nothing but Python. Parts 2 and 3
need Docker Desktop running. Open **Git Bash** in the project folder for every command.

## Before the review (do this once, the evening before)

1. Start Docker Desktop and wait for "Engine running".
2. Check the cluster is up:

```bash
export PATH="$PWD/tools:$PATH" KUBECONFIG="$PWD/tools/kubeconfig-chimera"
```

```bash
kubectl get pods -A
```

   Every pod should say `Running`. If the cluster is missing, rebuild it (5-10 minutes,
   needs internet):

```bash
bash k8s/quickstart.sh
```

3. Run both test suites once, so you can say they pass:

```bash
python tests/test_chimera.py
```

```bash
python tests/test_operator.py
```

## Part 1 - The controller in simulation (2 minutes)

```bash
PYTHONPATH=. python demo_run.py
```

It runs CHIMERA, AAPA and the Kubernetes HPA on the same real trace with the same seed and
prints six sections. What to point at:

- **Section 3, results.** CHIMERA has fewer SLO violations than AAPA and HPA. The
  "H only" row is CHIMERA with CPU resizing switched off: it needs about 40 % more CPU.
  That is the argument for using both actuators.
- **Section 4, what the cluster did.** HPA can only change the replica line. CHIMERA
  barely moves replicas and does the work on the CPU line.
- **Section 5.** A step where pods were already ordered: the controller holds instead of
  ordering the same capacity twice.
- **Section 6.** The full evaluation read from disk (3,870 runs).

Other traces: add `--workload w_0002` or `--workload w_0005`.

## Part 2 - The operator on a real cluster (5 minutes)

Use three Git Bash windows. In each one, first run the `export` line from above.

Window A, the operator's decisions as they happen:

```bash
kubectl -n kube-system logs -f deploy/chimera-controller
```

Window B, the pods and their CPU:

```bash
kubectl get pods -l app=billing-service -w -o custom-columns=NAME:.metadata.name,CPU:.spec.containers[0].resources.limits.cpu,RESTARTS:.status.containerStatuses[0].restartCount
```

Window C, drive the load:

```bash
kubectl apply -f k8s/example-autoscaler.yaml
```

```bash
kubectl scale deploy/load-generator --replicas=5
```

Wait one to two minutes. In window A the operator prints `fast: cpu 0.50->...`; in window
B the CPU column rises on the **same pods** and `RESTARTS` stays 0. That is in-place
resize. If the load keeps rising it adds a pod.

Then remove the load:

```bash
kubectl scale deploy/load-generator --replicas=0
```

Within about two minutes the CPU goes back to 500m (`fast release` in window A).

To show the same thing as a pass/fail report instead (11 minutes, unattended):

```bash
python k8s/live_validation.py --out results/live/demo_run
```

## Part 3 - The evidence (5 minutes, no commands)

Open these and talk through them:

| File | What it shows |
|---|---|
| `paper/chimera.pdf` | The paper. Fig. 2: cost against violations on three real trace sets. Table VI and Fig. 4: the real-cluster comparison with the HPA. |
| `results/replay3/REPLAY_REPORT.md` | The real-cluster numbers: 7 of 9 HPA runs beaten at no more CPU. |
| `results/REPORT_hw.md`, `results/REPORT_ext.md` | Twenty real traces never used in design: 117 of 117 comparisons won at equal CPU. |
| `IMPROVEMENT_AUDIT.md` | What was checked, what was wrong, what was fixed. |

## Questions to expect, and the honest answers

- **"Is this only simulation?"** Most performance numbers are. The operator runs on real
  Kubernetes 1.33, and a three-seed comparison with the HPA on a real cluster points the
  same way. That study is small: one service, one trace, one laptop.
- **"What is new?"** Not the single ideas: resizing in place first (Themis), optimising
  both axes (Ship et al.), pricing risk (MagicScaler) and conformal prediction in an
  autoscaler (BACC) all exist. New is the combination, the model of replicas still
  booting, and the evaluation.
- **"Which baseline is closest?"** KOSMOS. At default settings it matches or beats
  CHIMERA's violation rate on three of four trace groups, using 43-84 % more CPU. CHIMERA
  wins once cost is equal.
- **"What did not work?"** The coupling penalty and the Lyapunov filter change no
  decision and were removed. Risk-priced sizing helps only when its latency model fits.
- **"How do I know the baselines are fair?"** KOSMOS was found mis-tuned and re-tuned on
  dev data, which cut the reported advantage from 19 points to 6. The first run on the web
  traces was discarded because it capped the baselines.

## If something goes wrong

- **`kubectl` cannot connect:** Docker Desktop is not running, or the `export` line was
  not run in that window.
- **Nothing happens under load:** check `kubectl get chas`. If it is empty, apply
  `k8s/example-autoscaler.yaml` again.
- **No internet in the lab:** Parts 1 and 3 need none. Part 2 needs none if the cluster
  was started beforehand.
- **Cluster broken:** skip Part 2 and show `results/live/LIVE_REPORT.md` and
  `results/replay3/REPLAY_REPORT.md` instead.
