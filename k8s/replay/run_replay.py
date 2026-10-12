"""
Trace replay on a live cluster: CHIMERA against the Kubernetes HPA, with measured latency.

For each controller and each seed the same open-loop request trace (k8s/replay/loadgen.py)
is replayed against the demo service from the same starting state (2 pods x 500m).
Measured: client-side latency and SLO violations, allocated CPU (core-minutes, from the
CPU limits the kubelet has actually applied, sampled every 5 s), resizes, replica changes
and container restarts.

Controllers:
  hpa-NN        Kubernetes HorizontalPodAutoscaler at NN % CPU utilisation, 500m pods,
                up to 20 replicas (hpa = hpa-70). Several targets give the HPA a
                cost/SLO frontier to compare on.
  chimera-rate  CHIMERA operator on a request-rate signal (30 s rate from the load
                generator), pods 500m-1000m, up to 10 replicas
  chimera-cpu   the same on its default signal (1-minute CPU-usage rate)

Both sides can reach the same 10 cores at most. The trace is given in cores of demand and
converted to requests/second with the measured CPU cost of one request (--service-time),
so the same demand curve is replayed whatever the machine. The primary SLO is 5 x the
service time, the ratio the simulator uses; violation rates at looser thresholds are
recorded as well.

Needs: the quickstart cluster, metrics-server (for the HPA), the python:3.10-slim image on
the node, and the `loadgen` scrape job in k8s/prometheus.yaml.

    python k8s/replay/run_replay.py --service-time 0.061 --slo 0.3 --seeds 1 2 3 --out results/replay3
    python k8s/replay/run_replay.py --report-only --service-time 0.061 --slo 0.3 --seeds 1 2 3 --out results/replay3
"""

import argparse
import csv
import json
import subprocess
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
NS, DEPLOY, CR = "default", "billing-service", "billing-service-scaler"
RATE_QUERY = "sum(rate(loadgen_requests_sent_total[30s]))"
HPA_MAX_REPLICAS = 20
CHIMERA_MAX_CPU, CHIMERA_MAX_REPLICAS = "1000m", 10


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


def hpa_target(mode: str) -> int:
    return int(mode.split("-")[1]) if "-" in mode else 70


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
    kubectl("-n", NS, "rollout", "status", f"deploy/{DEPLOY}", "--timeout=300s")


def install_controller(mode: str, args):
    if mode.startswith("hpa"):
        apply({"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
               "metadata": {"name": DEPLOY, "namespace": NS},
               "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": DEPLOY},
                        "minReplicas": 2, "maxReplicas": HPA_MAX_REPLICAS,
                        "metrics": [{"type": "Resource", "resource": {
                            "name": "cpu", "target": {"type": "Utilization",
                                                      "averageUtilization": hpa_target(mode)}}}]}})
        return
    spec = {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": DEPLOY},
            "minReplicas": 2, "maxReplicas": CHIMERA_MAX_REPLICAS, "minCPU": "500m", "maxCPU": CHIMERA_MAX_CPU,
            "targetUtilization": args.target_utilization, "podStartupSeconds": 20, "resizeMode": "InPlace",
            "targetSLOMilliseconds": 1000 * args.slo, "serviceTimeMilliseconds": 1000 * args.service_time}
    if mode == "chimera-rate":
        # The CPU cost of a request is given, not learned: learning it from a 1-minute CPU
        # rate and a 30-second request rate underestimates it while load is rising.
        spec["metrics"] = {"requestRateQuery": RATE_QUERY, "cpuPerRequest": args.service_time}
    apply({"apiVersion": "autoscaling.chimera.io/v1alpha1", "kind": "ChimeraAutoscaler",
           "metadata": {"name": CR, "namespace": NS}, "spec": spec})


def start_loadgen(rps, seed, args):
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
                                            {"name": "SEED", "value": str(seed)},
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
        cpu = parse_cpu(p["spec"]["containers"][0]["resources"]["limits"]["cpu"])
        for cs in p.get("status", {}).get("containerStatuses", []):
            restarts += cs.get("restartCount", 0)
            applied = (cs.get("resources") or {}).get("limits", {}).get("cpu")
            if applied is not None:          # what the kubelet has applied, not what was asked for
                cpu = parse_cpu(applied)
        cpus.append(cpu)
        ready += any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))
    return dict(replicas=dep["spec"].get("replicas"), pods=len(pods), ready=ready,
                cpu_per_pod=max(cpus) if cpus else 0.0, allocated=round(sum(cpus), 3), restarts=restarts)


def run_mode(mode: str, seed: int, rps, args, out: Path):
    print(f"=== seed {seed}, {mode} ===", flush=True)
    reset(args)
    install_controller(mode, args)
    time.sleep(args.settle)
    start_loadgen(rps, seed, args)
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
    with open(out / f"timeline_{mode}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    if not mode.startswith("hpa"):
        (out / f"operator_{mode}.log").write_text(
            kubectl("-n", "kube-system", "logs", "deploy/chimera-controller",
                    f"--since={int(time.time() - t0) + 60}s", check=False), encoding="utf-8")
    summary = dict(mode=mode, seed=seed,
                   core_minutes=round(sum(r["allocated"] for r in scored) * 5.0 / 60.0, 2),
                   cpu_resizes=sum(1 for a, b in zip(scored, scored[1:]) if a["cpu_per_pod"] != b["cpu_per_pod"]),
                   replica_changes=sum(1 for a, b in zip(scored, scored[1:]) if a["replicas"] != b["replicas"]),
                   max_replicas=max(r["replicas"] for r in scored), max_cpu_per_pod=max(r["cpu_per_pod"] for r in scored),
                   restarts=rows[-1]["restarts"], **result)
    (out / f"result_{mode}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"  -> SLO violations {100 * summary['slo_violation_rate']:.2f} %, p99 {summary['p99']:.2f} s, "
          f"{summary['core_minutes']} core-min", flush=True)


def report(cores, args, out: Path):
    res = {}
    for seed in args.seeds:
        for mode in args.modes:
            f = out / f"seed{seed}" / f"result_{mode}.json"
            if f.exists():
                res[(mode, seed)] = json.loads(f.read_text())
    seeds = [s for s in args.seeds if all((m, s) in res for m in args.modes)]
    if not seeds:
        return
    version = json.loads(kubectl('version', '-o', 'json'))['serverVersion']['gitVersion']
    L = ["# Live trace replay: CHIMERA against the Kubernetes HPA", "",
         f"Generated by `k8s/replay/run_replay.py`. Kubernetes {version}, one kind node. Service: "
         f"`registry.k8s.io/hpa-example` (CPU-bound), measured cost {args.service_time * 1000:.0f} ms of CPU per "
         f"request. Primary SLO {args.slo:.2f} s (5 x the service time). {len(seeds)} complete seed(s): "
         f"{', '.join(map(str, seeds))}; open-loop Poisson arrivals. Every run starts from 2 pods x 500m with "
         f"{args.lead_in} unscored lead-in minutes; client timeout {args.timeout:.0f} s, counted as a violation. "
         f"Both sides are limited to 10 cores (HPA: {HPA_MAX_REPLICAS} x 500m; CHIMERA: {CHIMERA_MAX_REPLICAS} x "
         f"{CHIMERA_MAX_CPU}). CHIMERA runs at its evaluated default setting ({args.target_utilization}). "
         "The order of controllers is rotated from seed to seed.", "",
         f"Demand trace: {len(cores)} minutes; 12 from the real Azure trace w_0005 (minutes 70-82, mean scaled to "
         f"0.6 cores), then a flash crowd (0.3 cores, spike to 3 cores, decay). Mean {cores.mean():.2f} cores, "
         f"peak {cores.max():.2f}.", "",
         "## Mean over seeds", "",
         "| Controller | SLO violations (min - max) | p95 (s) | CPU core-min (min - max) | CPU resizes | Replica changes | Restarts |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for m in args.modes:
        rs = [res[(m, s)] for s in seeds]
        v = [100 * r['slo_violation_rate'] for r in rs]
        c = [r['core_minutes'] for r in rs]
        L.append(f"| {m} | {np.mean(v):.1f} % ({min(v):.1f} - {max(v):.1f}) | {np.mean([r['p95'] for r in rs]):.2f} | "
                 f"{np.mean(c):.1f} ({min(c):.1f} - {max(c):.1f}) | {np.mean([r['cpu_resizes'] for r in rs]):.0f} | "
                 f"{np.mean([r['replica_changes'] for r in rs]):.0f} | {sum(r['restarts'] for r in rs)} |")

    thr = sorted({k for r in res.values() for k in r.get('violations_at', {})}, key=float)
    if thr:
        L += ["", "Violation rate at other latency thresholds (mean over seeds):", "",
              "| Controller | " + " | ".join(f"> {t} s" for t in thr) + " |", "|---|" + "---:|" * len(thr)]
        for m in args.modes:
            L.append(f"| {m} | " + " | ".join(
                f"{100 * np.mean([res[(m, s)]['violations_at'][t] for s in seeds]):.1f} %" for t in thr) + " |")

    hpas = sorted([m for m in args.modes if m.startswith("hpa")], key=hpa_target)
    for ch in [m for m in args.modes if m.startswith("chimera")]:
        if len(hpas) < 2:
            break
        L += ["", f"## {ch} against the HPA frontier at equal CPU", "",
              "The HPA's violation rate interpolated between its settings at the CPU CHIMERA used, seed by seed "
              "(same request stream). Gap = HPA minus CHIMERA; positive means CHIMERA has fewer violations at the "
              "same cost. 'outside' means CHIMERA's cost is outside the range the HPA settings span.", "",
              "| Seed | CHIMERA: violations / core-min | HPA points (violations / core-min) | HPA at CHIMERA's cost | Gap (pp) |",
              "|---:|---|---|---:|---:|"]
        gaps = []
        for s in seeds:
            pts = sorted((res[(h, s)]['core_minutes'], 100 * res[(h, s)]['slo_violation_rate']) for h in hpas)
            x, y = [p[0] for p in pts], [p[1] for p in pts]
            cv, cc = 100 * res[(ch, s)]['slo_violation_rate'], res[(ch, s)]['core_minutes']
            inside = x[0] <= cc <= x[-1]
            at = float(np.interp(cc, x, y))
            if inside:
                gaps.append(at - cv)
            L.append(f"| {s} | {cv:.1f} % / {cc:.1f} | " + "; ".join(f"{b:.1f} % / {a:.1f}" for a, b in pts) +
                     f" | {at:.1f} %{'' if inside else ' (outside)'} | {at - cv:+.1f}{'' if inside else ' (outside)'} |")
        if gaps:
            L += ["", f"Inside the HPA's cost range on {len(gaps)} of {len(seeds)} seeds: mean gap "
                      f"{np.mean(gaps):+.1f} pp, CHIMERA better on {sum(g > 0 for g in gaps)} of {len(gaps)}. "
                      f"With this few seeds no significance test is meaningful."]

    L += ["", "## Every run", "",
          "| Seed | Controller | SLO violations | mean (s) | p95 (s) | CPU (core-min) | Max replicas | Max CPU/pod | Requests | Timeouts |",
          "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in seeds:
        for m in args.modes:
            r = res[(m, s)]
            L.append(f"| {s} | {m} | {100 * r['slo_violation_rate']:.2f} % | {r['mean']:.3f} | {r['p95']:.2f} | "
                     f"{r['core_minutes']:.1f} | {r['max_replicas']} | {r['max_cpu_per_pod']} | {r['requests']} | {r['errors']} |")
    (out / "REPLAY_REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    print(f"report: {out / 'REPLAY_REPORT.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="results/replay3")
    ap.add_argument("--modes", nargs="+", default=["hpa-70", "hpa-50", "hpa-35", "chimera-rate"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--service-time", type=float, required=True, help="measured CPU seconds per request")
    ap.add_argument("--slo", type=float, help="latency objective, seconds (default 5 x service time)")
    ap.add_argument("--target-utilization", type=float, default=0.46,
                    help="CHIMERA setting; 0.46 is its default in the evaluation (v6)")
    ap.add_argument("--step", type=float, default=15.0)
    ap.add_argument("--settle", type=float, default=30.0)
    ap.add_argument("--start-delay", type=float, default=30.0)
    ap.add_argument("--minutes", type=int, help="replay only the first N minutes (smoke test)")
    ap.add_argument("--lead-in", type=int, default=3,
                    help="unscored minutes before the trace, ramping from 0.3 cores to its first value, so "
                         "that every controller enters the scored window settled")
    ap.add_argument("--timeout", type=float, default=10.0, help="client timeout, seconds")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    args.slo = args.slo or 5 * args.service_time

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cores = demand_trace()[:args.minutes] if args.minutes else demand_trace()
    per_min = int(round(60 / args.step))
    lead = np.linspace(0.3, cores[0], args.lead_in * per_min) if args.lead_in else np.array([])
    rps = np.concatenate([lead, np.repeat(cores, per_min)]) / args.service_time
    if not args.report_only:
        for i, seed in enumerate(args.seeds):
            d = out / f"seed{seed}"
            d.mkdir(exist_ok=True)
            order = args.modes[i % len(args.modes):] + args.modes[:i % len(args.modes)]
            for mode in order:
                if (d / f"result_{mode}.json").exists():
                    continue                      # resume after an interruption
                try:
                    run_mode(mode, seed, rps, args, d)
                except Exception as exc:          # one failed run must not lose the others
                    print(f"  !! seed {seed}, {mode} failed: {exc}", flush=True)
            report(cores, args, out)
        reset(args)
    report(cores, args, out)


if __name__ == "__main__":
    main()
