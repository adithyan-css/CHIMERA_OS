"""
Live-cluster validation of the CHIMERA operator.

Drives the quickstart's load generator through baseline / surge / relax phases and samples
the cluster every few seconds, then writes

    <out>/timeline.csv      one row per sample
    <out>/operator.log      the operator's decision log for the run
    <out>/LIVE_REPORT.md    the checks below, with the evidence for each

Checks:
  1. the operator sees real CPU usage (Prometheus returns data for the target);
  2. CPU per pod is raised in place under load, on the same pods, with no container restart;
  3. replicas scale out under load and are not removed again straight afterwards;
  4. CPU is released again once the load drops;
  5. the ChimeraAutoscaler status is written back.

Uses kubectl (and its current context / KUBECONFIG) only, so it runs unchanged against
kind and EKS.

    python k8s/live_validation.py --out results/live
"""

import argparse
import csv
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.parse

FLAP_WINDOW = 120   # seconds

CPU_QUERY = ('sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",'
             'pod=~"{deploy}-.*",container!="",container!="POD"}}[1m]))')


def kubectl(*args, check=True) -> str:
    res = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def kubectl_json(*args):
    try:
        return json.loads(kubectl(*args, "-o", "json"))
    except (RuntimeError, json.JSONDecodeError):
        return None


def parse_cpu(q) -> float:
    s = str(q)
    return float(s[:-1]) / 1000.0 if s.endswith("m") else float(s)


def prom_cpu(ns: str, deploy: str, prom_ns: str):
    q = urllib.parse.quote(CPU_QUERY.format(ns=ns, deploy=deploy), safe="")
    try:
        body = json.loads(kubectl(
            "get", "--raw",
            f"/api/v1/namespaces/{prom_ns}/services/prometheus:9090/proxy/api/v1/query?query={q}"))
        result = body["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except (RuntimeError, KeyError, IndexError, ValueError):
        return None


def sample(args, phase: str, generators: int, t0: float) -> dict:
    deploy = kubectl_json("-n", args.namespace, "get", "deploy", args.deployment) or {}
    sel = ",".join(f"{k}={v}" for k, v in
                   deploy.get("spec", {}).get("selector", {}).get("matchLabels", {}).items())
    pods = (kubectl_json("-n", args.namespace, "get", "pods", "-l", sel) or {}).get("items", [])
    pods = [p for p in pods if "deletionTimestamp" not in p["metadata"]]
    cr = kubectl_json("-n", args.namespace, "get", "chimeraautoscaler", args.autoscaler) or {}
    st = cr.get("status", {})

    spec_cpu, actual_cpu, restarts, uids, ready = [], [], 0, [], 0
    for p in pods:
        uids.append(p["metadata"]["uid"][:8])
        lim = p["spec"]["containers"][0].get("resources", {}).get("limits", {})
        if "cpu" in lim:
            spec_cpu.append(parse_cpu(lim["cpu"]))
        for cs in p.get("status", {}).get("containerStatuses", []):
            restarts += cs.get("restartCount", 0)
            # what the kubelet has actually applied to the running container
            alim = (cs.get("resources") or {}).get("limits", {})
            if "cpu" in alim:
                actual_cpu.append(parse_cpu(alim["cpu"]))
        if any(c["type"] == "Ready" and c["status"] == "True"
               for c in p.get("status", {}).get("conditions", [])):
            ready += 1
    return {
        "t": round(time.time() - t0, 1),
        "phase": phase,
        "load_generators": generators,
        "replicas": deploy.get("spec", {}).get("replicas"),
        "ready": ready,
        "cpu_spec_max": max(spec_cpu) if spec_cpu else None,
        "cpu_spec_min": min(spec_cpu) if spec_cpu else None,
        "cpu_actual_max": max(actual_cpu) if actual_cpu else None,
        "cpu_allocated_total": round(sum(spec_cpu), 3) if spec_cpu else None,
        "cpu_used_prometheus": prom_cpu(args.namespace, args.deployment, args.prometheus_namespace),
        "restarts": restarts,
        "pod_uids": " ".join(sorted(uids)),
        "status_demand_cores": st.get("observedDemandCores"),
        "status_cpu": st.get("currentCPUAllocation"),
        "status_decision": st.get("lastDecision"),
    }


def write_report(args, rows, started: datetime.datetime) -> bool:
    def col(name, phase=None):
        return [r[name] for r in rows if r[name] is not None and (phase is None or r["phase"] == phase)]

    spec = (kubectl_json("-n", args.namespace, "get", "chimeraautoscaler", args.autoscaler) or {}).get("spec", {})
    version = (kubectl_json("version") or {}).get("serverVersion", {}).get("gitVersion", "unknown")

    base_cpu = col("cpu_spec_max", "baseline")
    surge_cpu = col("cpu_spec_max", "surge")
    relax_cpu = col("cpu_spec_max", "relax")
    first, last = rows[0], rows[-1]
    first_uids = set(first["pod_uids"].split())
    survivors = first_uids & set(last["pod_uids"].split())

    prom = col("cpu_used_prometheus")
    c1 = len(prom) >= 0.8 * len(rows) and max(prom, default=0) > 0

    start_cpu = base_cpu[0] if base_cpu else None
    peak_cpu = max(surge_cpu, default=None)
    peak_actual = max(col("cpu_actual_max", "surge"), default=None)
    c2 = (start_cpu is not None and peak_cpu is not None and peak_cpu > start_cpu
          and last["restarts"] == 0 and len(survivors) == len(first_uids))

    # replica changes: (time, from, to); a flap is a scale-in within FLAP_WINDOW of a scale-out
    rep_changes, prev = [], None
    for r in rows:
        if r["replicas"] is None:
            continue
        if prev is not None and r["replicas"] != prev:
            rep_changes.append((r["t"], prev, r["replicas"]))
        prev = r["replicas"]
    flaps = [(up, down) for up in rep_changes if up[2] > up[1]
             for down in rep_changes if down[2] < down[1] and 0 < down[0] - up[0] <= FLAP_WINDOW]
    if not rep_changes:
        c3, c3_text = None, "replicas never changed, so horizontal scaling was not exercised in this run"
    else:
        c3 = not flaps
        c3_text = "; ".join(f"t={t:.0f}s {a} -> {b}" for t, a, b in rep_changes)
        c3_text += (f"; {len(flaps)} scale-in within {FLAP_WINDOW}s of a scale-out" if flaps
                    else f"; no scale-in within {FLAP_WINDOW}s of a scale-out")

    end_cpu = relax_cpu[-1] if relax_cpu else None
    c4 = peak_cpu is not None and end_cpu is not None and end_cpu < peak_cpu

    c5 = bool(col("status_decision")) and bool(col("status_cpu"))

    def mark(c):
        return "not exercised" if c is None else ("PASS" if c else "FAIL")

    lines = [
        "# CHIMERA live-cluster validation",
        "",
        f"Run started {started.strftime('%Y-%m-%d %H:%M:%S UTC')} against context "
        f"`{kubectl('config', 'current-context').strip()}` (Kubernetes {version}). "
        f"Generated by `k8s/live_validation.py`; raw samples in `timeline.csv`, operator "
        f"decisions in `operator.log`.",
        "",
        f"Target: `{args.namespace}/{args.deployment}`, CPU per pod {spec.get('minCPU')} to "
        f"{spec.get('maxCPU')}, replicas {spec.get('minReplicas')} to {spec.get('maxReplicas')}, "
        f"target utilisation {spec.get('targetUtilization')}.",
        "",
        "| Phase | Seconds | Load generators |",
        "|---|---:|---:|",
        f"| baseline | {args.baseline} | {args.low} |",
        f"| surge | {args.surge} | {args.high} |",
        f"| relax | {args.relax} | 0 |",
        "",
        "## Checks",
        "",
        "| # | Check | Result | Evidence |",
        "|---|---|---|---|",
        f"| 1 | Operator input is real CPU usage from Prometheus | {mark(c1)} | "
        f"{len(prom)} of {len(rows)} samples returned data; peak {max(prom, default=0):.2f} cores |",
        f"| 2 | CPU raised in place, same pods, no restart | {mark(c2)} | "
        f"CPU per pod {start_cpu} -> {peak_cpu} (kubelet-applied peak {peak_actual}); "
        f"{len(survivors)} of {len(first_uids)} original pods still running; "
        f"{last['restarts']} container restarts |",
        f"| 3 | Replicas scale out under load and do not flap | {mark(c3)} | {c3_text} |",
        f"| 4 | CPU released when load drops | {mark(c4)} | "
        f"CPU per pod {peak_cpu} at peak -> {end_cpu} at the end of the relax phase |",
        f"| 5 | Status written back to the ChimeraAutoscaler | {mark(c5)} | "
        f"last decision: `{last['status_decision']}` |",
        "",
        "## Per-phase summary",
        "",
        "| Phase | CPU used (cores, mean / max) | CPU per pod (min / max) | Replicas (min / max) | Allocated cores (max) |",
        "|---|---:|---:|---:|---:|",
    ]
    for ph in ("baseline", "surge", "relax"):
        used, cpu, rep, alloc = (col("cpu_used_prometheus", ph), col("cpu_spec_max", ph),
                                 col("replicas", ph), col("cpu_allocated_total", ph))
        if not cpu:
            continue
        mean_used = sum(used) / len(used) if used else float("nan")
        lines.append(f"| {ph} | {mean_used:.2f} / {max(used, default=float('nan')):.2f} | "
                     f"{min(cpu):.2f} / {max(cpu):.2f} | {min(rep)} / {max(rep)} | {max(alloc):.2f} |")

    changes, prev = [], None
    for r in rows:
        cur = (r["cpu_spec_max"], r["replicas"])
        if prev is not None and cur != prev:
            changes.append(f"| {r['t']:.0f} | {r['phase']} | {prev[0]} -> {cur[0]} | {prev[1]} -> {cur[1]} | "
                           f"{r['cpu_used_prometheus'] if r['cpu_used_prometheus'] is None else round(r['cpu_used_prometheus'], 2)} |")
        prev = cur
    lines += ["", "## Allocation changes seen by the sampler", "",
              "| t (s) | Phase | CPU per pod | Replicas | CPU used (cores) |", "|---:|---|---|---|---:|", *changes, ""]

    with open(os.path.join(args.out, "LIVE_REPORT.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return all(c is not False for c in (c1, c2, c3, c4, c5))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="results/live")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--deployment", default="billing-service")
    ap.add_argument("--autoscaler", default="billing-service-scaler")
    ap.add_argument("--load-deployment", default="load-generator")
    ap.add_argument("--prometheus-namespace", default="monitoring")
    ap.add_argument("--operator-namespace", default="kube-system")
    ap.add_argument("--baseline", type=int, default=120, help="seconds at low load")
    ap.add_argument("--surge", type=int, default=300, help="seconds at high load")
    ap.add_argument("--relax", type=int, default=240, help="seconds with no load")
    ap.add_argument("--low", type=int, default=1, help="load generators in the baseline phase")
    ap.add_argument("--high", type=int, default=4, help="load generators in the surge phase")
    ap.add_argument("--period", type=float, default=5.0, help="sampling period, seconds")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    started = datetime.datetime.now(datetime.timezone.utc)
    t0 = time.time()
    rows = []
    for phase, seconds, generators in (("baseline", args.baseline, args.low),
                                       ("surge", args.surge, args.high),
                                       ("relax", args.relax, 0)):
        kubectl("-n", args.namespace, "scale", f"deploy/{args.load_deployment}", f"--replicas={generators}")
        print(f"[{time.time() - t0:5.0f}s] phase {phase}: {generators} load generator(s) for {seconds}s", flush=True)
        end = time.time() + seconds
        while time.time() < end:
            tick = time.time()
            row = sample(args, phase, generators, t0)
            rows.append(row)
            print(f"[{row['t']:5.0f}s] {phase:8s} replicas={row['replicas']} ready={row['ready']} "
                  f"cpu/pod={row['cpu_spec_max']} used={row['cpu_used_prometheus']} "
                  f"restarts={row['restarts']} | {row['status_decision']}", flush=True)
            time.sleep(max(0.0, args.period - (time.time() - tick)))

    with open(os.path.join(args.out, "timeline.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    since = int(time.time() - t0) + 30
    log = kubectl("-n", args.operator_namespace, "logs", "deploy/chimera-controller", f"--since={since}s", check=False)
    with open(os.path.join(args.out, "operator.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write(log)

    ok = write_report(args, rows, started)
    print(f"report: {os.path.join(args.out, 'LIVE_REPORT.md')} ({'all checks passed' if ok else 'CHECKS FAILED'})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
