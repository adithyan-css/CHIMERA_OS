"""
Trace replay on a live cluster: CHIMERA against the Kubernetes HPA, with measured latency.

For each controller the same open-loop request trace (k8s/replay/loadgen.py) is replayed
against the demo service, starting from the same state (2 pods x 500m). Measured:
client-side latency and SLO violations, allocated CPU (core-minutes, sampled every 5 s),
resizes, replica changes and container restarts.

Controllers:
  hpa           Kubernetes HorizontalPodAutoscaler, 70 % CPU utilisation, 500m pods
  hpa-50        the same at a 50 % target (a more expensive setting, to bracket the cost of CHIMERA)
  chimera-cpu   CHIMERA operator on its default signal (1-minute CPU-usage rate)
  chimera-rate  CHIMERA operator on a request-rate signal (30 s rate from the load generator)

The trace is given in cores of demand and converted to requests/second with the measured
CPU cost of one request (--service-time, seconds), so the same demand curve is replayed
whatever the machine. The SLO is 5 x the service time, the ratio the simulator uses.

Needs: the quickstart cluster, metrics-server (for the HPA), the python:3.10-slim image on
the node, and the `loadgen` scrape job in k8s/prometheus.yaml.

    python k8s/replay/run_replay.py --service-time 0.12 --out results/replay
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
NS, DEPLOY, CR = "default", "billing-service", "billing-service-scaler"
RATE_QUERY = "sum(rate(loadgen_requests_sent_total[30s]))"


def kubectl(*args, check=True, stdin=None) -> str:
    res = subprocess.run(["kubectl", *args], capture_output=True, text=True, input=stdin)
    if check and res.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def apply(manifest: dict):
    kubectl("apply", "-f", "-", stdin=json.dumps(manifest))


def parse_cpu(q) -> float:
    s = str(q)
    return float(s[:-1]) / 1000.0 if s.endswith("m") else float(s)


def demand_trace():
    """24 minutes of demand in cores: 12 minutes of a real Azure Functions trace (w_0005,
    minutes 70-82, mean scaled to 0.6 cores so that the common starting allocation of
    2 x 500m is adequate at the start), then 12 minutes around the held-out flash crowd
    (calm at 0.3 cores, a spike to 3 cores, exponential decay)."""
    d = json.load(open(ROOT / "dataset/workloads/w_0005.json"))
    real = np.array(d["request_trace"]["requests_per_minute"][70:82], dtype=float)
    real = 0.6 * real / real.mean()
    t = np.arange(12)
    flash = np.full(12, 0.3)
    flash[4:6] = [1.35, 3.0]
    flash[6:] = 0.3 + 2.7 * 0.5 ** ((t[6:] - 5) / 4.0)
    return np.concatenate([real, flash])


def reset(args):
    kubectl("-n", NS, "delete", "pod", "loadgen", "--ignore-not-found", "--wait=true", check=False)
    kubectl("-n", NS, "delete", "hpa", DEPLOY, "--ignore-not-found", check=False)
    kubectl("-n", NS, "delete", "chimeraautoscaler", CR, "--ignore-not-found", check=False)
    kubectl("-n", NS, "scale", f"deploy/{DEPLOY}", "--replicas=2")
    kubectl("-n", NS, "rollout", "restart", f"deploy/{DEPLOY}")      # back to the 500m template
    kubectl("-n", NS, "rollout", "status", f"deploy/{DEPLOY}", "--timeout=180s")


def install_controller(mode: str, args):
    if mode.startswith("hpa"):
        target = int(mode.split("-")[1]) if "-" in mode else 70
        apply({"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
               "metadata": {"name": DEPLOY, "namespace": NS},
               "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": DEPLOY},
                        "minReplicas": 2, "maxReplicas": 10,
                        "metrics": [{"type": "Resource", "resource": {
                            "name": "cpu", "target": {"type": "Utilization", "averageUtilization": target}}}]}})
        return
    spec = {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": DEPLOY},
            "minReplicas": 2, "maxReplicas": 10, "minCPU": "500m", "maxCPU": "2000m",
            "targetUtilization": args.target_utilization, "podStartupSeconds": 20, "resizeMode": "InPlace",
            "targetSLOMilliseconds": 1000 * args.slo, "serviceTimeMilliseconds": 1000 * args.service_time}
    if mode == "chimera-rate":
        # The CPU cost of a request is given, not learned: learning it from a 1-minute CPU
        # rate and a 30-second request rate underestimates it while load is rising.
        spec["metrics"] = {"requestRateQuery": RATE_QUERY, "cpuPerRequest": args.service_time}
    apply({"apiVersion": "autoscaling.chimera.io/v1alpha1", "kind": "ChimeraAutoscaler",
           "metadata": {"name": CR, "namespace": NS}, "spec": spec})


def start_loadgen(rps, args):
    cm = kubectl("-n", NS, "create", "configmap", "loadgen-script",
                 f"--from-file={ROOT / 'k8s/replay/loadgen.py'}", "--dry-run=client", "-o", "json")
    kubectl("apply", "-f", "-", stdin=cm)
    apply({"apiVersion": "v1", "kind": "Service", "metadata": {"name": "loadgen", "namespace": NS},
           "spec": {"selector": {"app": "loadgen"}, "ports": [{"port": 8000, "targetPort": 8000}]}})
    apply({"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": "loadgen", "namespace": NS, "labels": {"app": "loadgen"}},
           "spec": {"restartPolicy": "Never",
                    "containers": [{"name": "loadgen", "image": "python:3.10-slim", "imagePullPolicy": "IfNotPresent",
                                    "command": ["python", "-u", "/scripts/loadgen.py"],
                                    "ports": [{"containerPort": 8000}],
                                    "env": [{"name": "TARGET_URL", "value": f"http://{DEPLOY}"},
                                            {"name": "TRACE", "value": json.dumps([round(float(r), 3) for r in rps])},
                                            {"name": "STEP", "value": str(args.step)},
                                            {"name": "SLO", "value": str(args.slo)},
                                            {"name": "SEED", "value": str(args.seed)},
                                            {"name": "START_DELAY", "value": str(args.start_delay)},
                                            {"name": "TIMEOUT", "value": str(args.timeout)},
                                            {"name": "SCORE_FROM", "value": str(60 * args.lead_in)}],
                                    "volumeMounts": [{"name": "s", "mountPath": "/scripts"}]}],
                    "volumes": [{"name": "s", "configMap": {"name": "loadgen-script"}}]}})
    kubectl("-n", NS, "wait", "--for=condition=Ready", "pod/loadgen", "--timeout=180s")


def sample():
    dep = json.loads(kubectl("-n", NS, "get", "deploy", DEPLOY, "-o", "json"))
    sel = ",".join(f"{k}={v}" for k, v in dep["spec"]["selector"]["matchLabels"].items())
    pods = [p for p in json.loads(kubectl("-n", NS, "get", "pods", "-l", sel, "-o", "json"))["items"]
            if "deletionTimestamp" not in p["metadata"]]
    cpus, restarts, ready = [], 0, 0
    for p in pods:
        cpus.append(parse_cpu(p["spec"]["containers"][0]["resources"]["limits"]["cpu"]))
        restarts += sum(c.get("restartCount", 0) for c in p.get("status", {}).get("containerStatuses", []))
        ready += any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))
    return dict(replicas=dep["spec"].get("replicas"), pods=len(pods), ready=ready,
                cpu_per_pod=max(cpus) if cpus else 0.0, allocated=round(sum(cpus), 3), restarts=restarts)


def run_mode(mode: str, rps, args, out: Path):
    print(f"=== {mode} ===", flush=True)
    reset(args)
    install_controller(mode, args)
    time.sleep(args.settle)
    start_loadgen(rps, args)
    t0 = time.time()
    duration = args.start_delay + len(rps) * args.step
    lead = 60.0 * args.lead_in
    rows, result = [], None
    while time.time() - t0 < duration + args.timeout + 120:
        tick = time.time()
        row = dict(t=round(tick - t0 - args.start_delay - lead, 1), **sample())
        rows.append(row)
        if len(rows) % 12 == 0:
            print(f"  [{row['t']:6.0f}s] replicas={row['replicas']} cpu/pod={row['cpu_per_pod']} "
                  f"allocated={row['allocated']}", flush=True)
            if tick - t0 > duration:
                log = kubectl("-n", NS, "logs", "loadgen", check=False)
                hit = [ln for ln in log.splitlines() if ln.startswith("RESULT ")]
                if hit:
                    result = json.loads(hit[-1][7:])
                    break
        time.sleep(max(0.0, 5.0 - (time.time() - tick)))
    if result is None:
        raise RuntimeError(f"{mode}: the load generator produced no result")

    scored = [r for r in rows if 0 <= r["t"] <= len(rps) * args.step - lead]
    core_min = sum(r["allocated"] for r in scored) * 5.0 / 60.0
    resizes = sum(1 for a, b in zip(scored, scored[1:]) if a["cpu_per_pod"] != b["cpu_per_pod"])
    rep_changes = sum(1 for a, b in zip(scored, scored[1:]) if a["replicas"] != b["replicas"])
    with open(out / f"timeline_{mode}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    if not mode.startswith("hpa"):
        (out / f"operator_{mode}.log").write_text(
            kubectl("-n", "kube-system", "logs", "deploy/chimera-controller",
                    f"--since={int(time.time() - t0) + 60}s", check=False), encoding="utf-8")
    summary = dict(mode=mode, core_minutes=round(core_min, 2), cpu_resizes=resizes, replica_changes=rep_changes,
                   max_replicas=max(r["replicas"] for r in scored), max_cpu_per_pod=max(r["cpu_per_pod"] for r in scored),
                   restarts=rows[-1]["restarts"], **result)
    (out / f"result_{mode}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"  -> SLO violations {100 * summary['slo_violation_rate']:.2f} %, p99 {summary['p99']:.2f} s, "
          f"{summary['core_minutes']} core-min", flush=True)
    return summary


def report(results, cores, args, out: Path):
    lines = ["# Live trace replay: CHIMERA against the Kubernetes HPA", "",
             f"Generated by `k8s/replay/run_replay.py`. Kubernetes "
             f"{json.loads(kubectl('version', '-o', 'json'))['serverVersion']['gitVersion']}, one kind node. "
             f"Service: `registry.k8s.io/hpa-example` (CPU-bound), measured cost {args.service_time * 1000:.0f} ms of "
             f"CPU per request; SLO {args.slo:.2f} s (5 x the service time). CHIMERA at its default setting "
             f"({args.target_utilization}); HPA at 70 % and, as a more expensive point, at 50 %. Open-loop Poisson arrivals, "
             f"seed {args.seed}. Every run starts from 2 pods x 500m and gets {args.lead_in} unscored lead-in minutes "
             f"(demand ramping from 0.3 cores to the trace's first value); client timeout {args.timeout:.0f} s, "
             f"counted as a violation.", "",
             f"Demand trace: {len(cores)} minutes; 12 from the real Azure trace w_0005 (minutes 70-82, mean scaled "
             f"to 0.6 cores), then a flash crowd (0.3 cores, spike to 3 cores, decay). "
             f"Mean {cores.mean():.2f} cores, peak {cores.max():.2f}.", "",
             "| Controller | SLO violations | mean (s) | p95 (s) | p99 (s) | CPU (core-min) | CPU resizes | Replica changes | Max replicas | Max CPU/pod | Restarts | Requests |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['mode']} | {100 * r['slo_violation_rate']:.2f} % | {r['mean']:.3f} | {r['p95']:.2f} | "
                     f"{r['p99']:.2f} | {r['core_minutes']:.1f} | {r['cpu_resizes']} | {r['replica_changes']} | "
                     f"{r['max_replicas']} | {r['max_cpu_per_pod']} | {r['restarts']} | {r['requests']} |")
    lines += ["", "Violations per minute (violating / total requests):", "",
              "| Minute | Demand (cores) | " + " | ".join(r["mode"] for r in results) + " |",
              "|---:|---:|" + "---:|" * len(results)]
    for m in range(len(cores)):
        lines.append(f"| {m} | {cores[m]:.2f} | " +
                     " | ".join(f"{r['per_minute'][m][1]}/{r['per_minute'][m][0]}" for r in results) + " |")
    (out / "REPLAY_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="results/replay")
    ap.add_argument("--modes", nargs="+", default=["hpa", "hpa-50", "chimera-cpu", "chimera-rate"])
    ap.add_argument("--service-time", type=float, required=True, help="measured CPU seconds per request")
    ap.add_argument("--slo", type=float, help="latency objective, seconds (default 5 x service time)")
    ap.add_argument("--target-utilization", type=float, default=0.46,
                    help="CHIMERA setting; 0.46 is its default in the evaluation (v6)")
    ap.add_argument("--step", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--settle", type=float, default=45.0)
    ap.add_argument("--start-delay", type=float, default=30.0)
    ap.add_argument("--minutes", type=int, help="replay only the first N minutes (smoke test)")
    ap.add_argument("--lead-in", type=int, default=3,
                    help="unscored minutes before the trace, ramping from 0.3 cores to its first value, so "
                         "that every controller enters the scored window settled instead of from the "
                         "common 2 x 500m starting allocation")
    ap.add_argument("--timeout", type=float, default=10.0, help="client timeout, seconds")
    args = ap.parse_args()
    args.slo = args.slo or 5 * args.service_time

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cores = demand_trace()[:args.minutes] if args.minutes else demand_trace()
    per_min = int(round(60 / args.step))
    lead = np.linspace(0.3, cores[0], args.lead_in * per_min) if args.lead_in else np.array([])
    rps = np.concatenate([lead, np.repeat(cores, per_min)]) / args.service_time
    results = []
    for mode in args.modes:
        results.append(run_mode(mode, rps, args, out))
    reset(args)
    report(results, cores, args, out)
    print(f"report: {out / 'REPLAY_REPORT.md'}")


if __name__ == "__main__":
    main()
