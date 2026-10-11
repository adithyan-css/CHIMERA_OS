"""
CHIMERA Kubernetes Controller Manager (Operator Loop)

Every control interval (default 15 s) this lists all ChimeraAutoscaler objects and, for
each one:
  1. reads the target Deployment and its pods (replicas, Ready count, CPU per pod),
  2. queries Prometheus for CPU usage and (optionally) request rate,
  3. runs the CHIMERA control step (k8s/operator_core.py),
  4. actuates: replicas via the Deployment scale subresource; CPU in place via the
     pods/resize subresource (KEP-1287, Kubernetes >= 1.33; falls back to patching the
     pod directly on 1.27-1.32 with the InPlacePodVerticalScaling feature gate), or via
     a rolling update of the pod template when spec.resizeMode is "Rolling",
  5. writes status back to the ChimeraAutoscaler object.

Configuration (environment):
  PROMETHEUS_URL        default http://prometheus.monitoring.svc:9090
  PROMETHEUS_TOKEN_FILE optional bearer token file
  CHIMERA_DRY_RUN=1     compute and log decisions, never patch anything
  CHIMERA_INTERVAL      full joint step period, seconds (default 15)
  CHIMERA_FAST_INTERVAL cascade vertical-only step period, seconds (default 5)
"""

import datetime
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from operator_core import Observation, Spec, TargetState
from prom_query import PrometheusClient, PrometheusError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("chimera-operator")

GROUP, VERSION, PLURAL = "autoscaling.chimera.io", "v1alpha1", "chimeraautoscalers"

DEFAULT_CPU_QUERY = ('sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",'
                     'pod=~"{deployment}-.*",container!="",container!="POD"}}[1m]))')


def parse_cpu(cpu_str) -> float:
    """Convert Kubernetes CPU quantities (500m, 1.5, 2) to cores."""
    s = str(cpu_str)
    if s.endswith('m'):
        return float(s[:-1]) / 1000.0
    return float(s)


def format_cpu(cpu_val: float) -> str:
    """Format cores as millicores."""
    return f"{int(round(cpu_val * 1000))}m"


def spec_from_cr(obj: dict) -> Spec:
    sp = obj.get('spec', {})
    return Spec(
        min_replicas=int(sp.get('minReplicas', 2)),
        max_replicas=int(sp.get('maxReplicas', 30)),
        min_cpu=parse_cpu(sp.get('minCPU', '500m')),
        max_cpu=parse_cpu(sp.get('maxCPU', '4000m')),
        target_utilization=float(sp.get('targetUtilization', 0.70)),
        ratio_cpu_to_replicas=float(sp.get('ratioCPUToReplicas', 0.5)),
        pod_startup_seconds=float(sp.get('podStartupSeconds', 20)),
        slo_seconds=float(sp.get('targetSLOMilliseconds', 500)) / 1000.0,
        service_time_seconds=float(sp.get('serviceTimeMilliseconds', 100)) / 1000.0,
        cpu_per_request=(float(sp['metrics']['cpuPerRequest'])
                         if sp.get('metrics', {}).get('cpuPerRequest') is not None else None),
    )


class ChimeraReconciler:
    def __init__(self, prom: PrometheusClient, dry_run: bool = False):
        self.prom = prom
        self.dry_run = dry_run
        self.states = {}         # (namespace, name) -> (spec, TargetState)
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()

    # -- reads ----------------------------------------------------------------------
    def _metric(self, query_template: str, namespace: str, deployment: str):
        if not query_template:
            return None
        q = query_template.format(namespace=namespace, deployment=deployment)
        try:
            return self.prom.query_scalar(q)
        except PrometheusError as exc:
            logger.warning(f"{namespace}/{deployment}: Prometheus query failed: {exc}")
            return None

    def observe(self, obj: dict) -> tuple:
        ns = obj['metadata']['namespace']
        sp = obj['spec']
        target = sp['scaleTargetRef']['name']
        deploy = self.apps.read_namespaced_deployment(target, ns)

        selector = ",".join(f"{k}={v}" for k, v in (deploy.spec.selector.match_labels or {}).items())
        pods = [p for p in self.core.list_namespaced_pod(ns, label_selector=selector).items
                if p.metadata.deletion_timestamp is None]
        ready = sum(1 for p in pods if any(c.type == "Ready" and c.status == "True"
                                           for c in (p.status.conditions or [])))

        # CPU per pod: what the running pods actually have (in-place resizes change pods,
        # not the template); fall back to the template before any pod exists.
        cpu = None
        for p in pods:
            res = (p.spec.containers[0].resources.limits or {})
            if 'cpu' in res:
                cpu = max(cpu or 0.0, parse_cpu(res['cpu']))
        if cpu is None:
            limits = deploy.spec.template.spec.containers[0].resources.limits or {}
            cpu = parse_cpu(limits.get('cpu', '500m'))

        metrics = sp.get('metrics', {})
        obs = Observation(
            now=time.monotonic(),
            replicas=int(deploy.spec.replicas or 0),
            ready_replicas=ready,
            cpu_per_pod=cpu,
            cpu_usage=self._metric(metrics.get('cpuUsageQuery', DEFAULT_CPU_QUERY), ns, target),
            request_rate=self._metric(metrics.get('requestRateQuery', ''), ns, target),
        )
        return deploy, pods, obs

    # -- writes ---------------------------------------------------------------------
    def _resize_pod_in_place(self, ns: str, pod, container: str, cpu: str):
        body = {"spec": {"containers": [{"name": container,
                                         "resources": {"requests": {"cpu": cpu}, "limits": {"cpu": cpu}}}]}}
        try:   # Kubernetes >= 1.33: the pods/resize subresource (typed client method)
            self.core.patch_namespaced_pod_resize(pod.metadata.name, ns, body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            # 1.27-1.32 with InPlacePodVerticalScaling: resources are mutable on the pod itself
            self.core.patch_namespaced_pod(pod.metadata.name, ns, body)

    def actuate(self, obj: dict, deploy, pods, decision, obs_cpu: float):
        ns = obj['metadata']['namespace']
        name = deploy.metadata.name
        mode = obj['spec'].get('resizeMode', 'InPlace')
        container = deploy.spec.template.spec.containers[0].name

        if mode == 'Rolling':
            if decision.new_cpu is not None:
                cpu = format_cpu(decision.new_cpu)
                self.apps.patch_namespaced_deployment(name, ns, {"spec": {"template": {"spec": {"containers": [
                    {"name": container, "resources": {"requests": {"cpu": cpu}, "limits": {"cpu": cpu}}}]}}}})
        else:
            # In place: bring every pod to the current allocation. This also catches pods
            # that were just created from the (unchanged) template at its original size.
            target = decision.new_cpu if decision.new_cpu is not None else obs_cpu
            cpu = format_cpu(target)
            for p in pods:
                limits = p.spec.containers[0].resources.limits or {}
                if format_cpu(parse_cpu(limits.get('cpu', '0'))) != cpu:
                    self._resize_pod_in_place(ns, p, container, cpu)
        if decision.new_replicas is not None:
            self.apps.patch_namespaced_deployment_scale(name, ns, {"spec": {"replicas": decision.new_replicas}})

    def write_status(self, obj: dict, obs: Observation, decision):
        status = {
            "currentReplicas": decision.new_replicas or obs.replicas,
            "readyReplicas": obs.ready_replicas,
            "currentCPUAllocation": format_cpu(decision.new_cpu or obs.cpu_per_pod),
            "observedDemandCores": round(decision.demand_cores, 4),
            "conformalIntervalWidth": round(decision.interval_width, 4),
            "driftDetected": decision.drift,
            "lastDecision": decision.reason,
            "lastScaleTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        try:
            self.custom.patch_namespaced_custom_object_status(
                GROUP, VERSION, obj['metadata']['namespace'], PLURAL, obj['metadata']['name'],
                {"status": status})
        except ApiException as exc:
            logger.warning(f"status update failed: {exc.reason}")

    # -- loop -----------------------------------------------------------------------
    def reconcile(self, obj: dict, full: bool = True):
        """full=True: joint step (replicas + CPU). full=False: the cascade's fast,
        vertical-only step that runs between full steps."""
        key = (obj['metadata']['namespace'], obj['metadata']['name'])
        spec = spec_from_cr(obj)
        if key not in self.states or self.states[key][0] != spec:
            self.states[key] = (spec, TargetState(spec))   # new object or spec edited
            full = True
        state = self.states[key][1]

        deploy, pods, obs = self.observe(obj)
        decision = state.step(obs) if full else state.fast_step(obs)
        if not full and decision.new_cpu is None:
            return
        logger.info(f"{key[0]}/{key[1]}: demand={decision.demand_cores:.3f} cores, "
                    f"replicas={obs.replicas} ready={obs.ready_replicas} cpu/pod={obs.cpu_per_pod:.2f} "
                    f"-> {decision.reason}{' [dry-run]' if self.dry_run else ''}")
        if not self.dry_run:
            self.actuate(obj, deploy, pods, decision, obs.cpu_per_pod)
            self.write_status(obj, obs, decision)

    def run_forever(self, interval: float = 15.0, fast_interval: float = 5.0):
        """Cascade: a fast vertical-only tick every `fast_interval` seconds, and a full
        joint step every `interval` seconds."""
        every = max(1, int(round(interval / fast_interval)))
        logger.info(f"CHIMERA operator started (full step every {interval:.0f}s, fast vertical step every "
                    f"{fast_interval:.0f}s, Prometheus {self.prom.base_url})")
        tick = 0
        while True:
            started = time.monotonic()
            full = tick % every == 0
            tick += 1
            try:
                objs = self.custom.list_cluster_custom_object(GROUP, VERSION, PLURAL).get('items', [])
            except ApiException as exc:
                logger.error(f"cannot list ChimeraAutoscalers: {exc.reason}")
                objs = []
            live = set()
            for obj in objs:
                live.add((obj['metadata']['namespace'], obj['metadata']['name']))
                try:
                    self.reconcile(obj, full=full)
                except ApiException as exc:
                    logger.error(f"{obj['metadata']['name']}: API error {exc.status} {exc.reason}")
                except Exception:
                    logger.exception(f"{obj['metadata']['name']}: reconcile failed")
            for gone in set(self.states) - live:   # forget deleted objects
                del self.states[gone]
            time.sleep(max(0.0, fast_interval - (time.monotonic() - started)))


def main():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    prom = PrometheusClient(os.environ.get("PROMETHEUS_URL", "http://prometheus.monitoring.svc:9090"))
    ChimeraReconciler(prom, dry_run=os.environ.get("CHIMERA_DRY_RUN") == "1").run_forever(
        float(os.environ.get("CHIMERA_INTERVAL", "15")), float(os.environ.get("CHIMERA_FAST_INTERVAL", "5")))


if __name__ == "__main__":
    main()
