"""
CHIMERA operator tests.

  * PrometheusClient is exercised against a real HTTP server speaking the Prometheus
    /api/v1/query protocol (no mocking of the HTTP layer).
  * The control core (k8s/operator_core.py) is driven through observation sequences.
  * The manager's parsing helpers are checked when the kubernetes package is installed.

Run: python tests/test_operator.py
"""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "k8s"))

from prom_query import PrometheusClient, PrometheusError          # noqa: E402
from operator_core import Observation, Spec, TargetState           # noqa: E402


class FakePrometheus(BaseHTTPRequestHandler):
    """Answers instant queries the way Prometheus does, keyed on the query text."""
    seen = []

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query).get("query", [""])[0]
        FakePrometheus.seen.append((url.path, q, self.headers.get("Authorization")))
        if url.path != "/api/v1/query":
            return self._send(404, {"status": "error", "errorType": "not_found", "error": "no route"})
        if "bad" in q:
            return self._send(400, {"status": "error", "errorType": "bad_data", "error": "parse error"})
        if q.startswith("scalar("):
            return self._send(200, {"status": "success",
                                    "data": {"resultType": "scalar", "result": [1700000000, "2.5"]}})
        if "nothing" in q:
            return self._send(200, {"status": "success", "data": {"resultType": "vector", "result": []}})
        return self._send(200, {"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": {"pod": "web-1"}, "value": [1700000000, "0.75"]},
            {"metric": {"pod": "web-2"}, "value": [1700000000, "1.25"]},
        ]}})

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class TestPrometheusClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakePrometheus)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_vector_query_is_summed(self):
        prom = PrometheusClient(self.url)
        self.assertEqual(len(prom.query('sum(rate(cpu[1m]))')), 2)
        self.assertAlmostEqual(prom.query_scalar('rate(cpu[1m])'), 2.0)

    def test_query_is_url_encoded(self):
        q = 'sum(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"web-.*"}[1m]))'
        PrometheusClient(self.url).query_scalar(q)
        self.assertEqual(FakePrometheus.seen[-1][1], q)

    def test_scalar_result(self):
        self.assertAlmostEqual(PrometheusClient(self.url).query_scalar("scalar(vector(2.5))"), 2.5)

    def test_empty_result_is_none_not_zero(self):
        self.assertIsNone(PrometheusClient(self.url).query_scalar("nothing_matches"))

    def test_error_body_raises(self):
        with self.assertRaises(PrometheusError) as ctx:
            PrometheusClient(self.url).query("bad(")
        self.assertIn("bad_data", str(ctx.exception))

    def test_bearer_token_is_sent(self):
        PrometheusClient(self.url, bearer_token="s3cr3t").query("up")
        self.assertEqual(FakePrometheus.seen[-1][2], "Bearer s3cr3t")

    def test_unreachable_raises(self):
        with self.assertRaises(PrometheusError):
            PrometheusClient("http://127.0.0.1:1", timeout=0.5).query("up")


def obs(t, replicas=2, ready=2, cpu=0.5, usage=None, rate=None):
    return Observation(now=t, replicas=replicas, ready_replicas=ready, cpu_per_pod=cpu,
                       cpu_usage=usage, request_rate=rate)


class TestOperatorCore(unittest.TestCase):
    def test_no_metrics_holds(self):
        d = TargetState(Spec()).step(obs(0))
        self.assertIsNone(d.new_cpu)
        self.assertIsNone(d.new_replicas)

    def test_load_jump_uses_fast_actuator_first(self):
        """A jump from 0.3 to 3 cores with 2 serving pods: the next fast tick (5 s) raises
        CPU in place instead of waiting for pods that take 20 s to boot."""
        st = TargetState(Spec())
        for t in range(0, 150, 15):
            st.step(obs(t, usage=0.3))
        d = st.fast_step(obs(155, usage=3.0, cpu=0.5))
        self.assertIsNotNone(d.new_cpu)
        self.assertGreater(d.new_cpu, 0.5)

    def test_full_step_leaves_cpu_to_fast_step(self):
        st = TargetState(Spec())
        d = st.step(obs(0, usage=3.0, cpu=0.5))
        self.assertIsNone(d.new_cpu)
        self.assertIsNotNone(d.new_replicas)       # replicas are still the full step's job

    def test_replica_band_keeps_count_when_cpu_can_absorb(self):
        """6 pods, need ~4.3 cores: inside the band (cannot need more pods at 85 % of
        v_max, cannot be too many at v_min), so the full step leaves replicas alone."""
        st = TargetState(Spec())
        for t in range(0, 300, 15):
            d = st.step(obs(t, replicas=6, ready=6, cpu=0.8, usage=3.0))
            self.assertIsNone(d.new_replicas)

    def test_never_scales_in_while_pods_boot(self):
        st = TargetState(Spec())
        st.step(obs(0, replicas=6, ready=6, cpu=1.0, usage=3.0))
        for t in range(15, 600, 15):
            d = st.step(obs(t, replicas=6, ready=4, cpu=1.0, usage=0.2))
            if d.new_replicas is not None:
                self.assertGreaterEqual(d.new_replicas, 6)

    def test_scales_in_after_cooldown_when_idle(self):
        st = TargetState(Spec())
        reps = 8
        for t in range(0, 1200, 15):
            d = st.step(obs(t, replicas=reps, ready=reps, cpu=0.5, usage=0.2))
            if d.new_replicas is not None:
                reps = d.new_replicas
        self.assertLess(reps, 8)
        self.assertGreaterEqual(reps, Spec().min_replicas)

    def test_scale_in_is_not_counted_as_in_flight(self):
        """A removed pod stops serving at once; recording it in the delay buffer too would
        double-count it and make the next tick re-order the pod it just removed."""
        st = TargetState(Spec())
        reps = 8
        for t in range(0, 1200, 15):
            d = st.step(obs(t, replicas=reps, ready=reps, cpu=0.5, usage=0.2))
            if d.new_replicas is not None and d.new_replicas < reps:
                self.assertEqual(st.controller.dh_history[0], 0)
                nxt = st.step(obs(t + 15, replicas=d.new_replicas, ready=d.new_replicas, cpu=0.5, usage=0.2))
                self.assertTrue(nxt.new_replicas is None or nxt.new_replicas <= d.new_replicas)
                return
            reps = d.new_replicas or reps
        self.fail("expected a scale-in on an idle service")

    def test_fast_step_scales_up_at_once_and_releases_after_two_ticks(self):
        st = TargetState(Spec())
        st.step(obs(0, cpu=1.0, usage=0.6))
        up = st.fast_step(obs(5, cpu=1.0, usage=2.5))              # surge: up immediately
        self.assertIsNotNone(up.new_cpu)
        self.assertGreater(up.new_cpu, 1.0)
        self.assertIsNone(up.new_replicas)                          # replicas are step()'s job
        st._last_hi = None                                          # isolate the release logic
        first = st.fast_step(obs(10, cpu=up.new_cpu, usage=0.3))    # need falls far below
        self.assertIsNone(first.new_cpu)                            # one tick is not enough
        second = st.fast_step(obs(15, cpu=up.new_cpu, usage=0.3))
        self.assertIsNotNone(second.new_cpu)
        self.assertLess(second.new_cpu, up.new_cpu)

    def test_risk_cores_certain_and_uncertain(self):
        from operator_core import risk_cores
        self.assertAlmostEqual(risk_cores([3.0], 0.7, 5.0), 3.0 / 0.7, places=4)
        self.assertGreater(risk_cores([2.0, 3.0, 4.0], 0.7, 5.0), risk_cores([3.0], 0.7, 5.0))

    def test_volatile_demand_gets_more_headroom(self):
        """Same current demand, one history moderately volatile (+-20 %): risk sizing gives it
        more CPU. (For extreme swings the optimiser may decline to cover the rare high
        scenario when that costs more than the violations it prevents at the set price.)"""
        calm, wild = TargetState(Spec()), TargetState(Spec())
        for t, (a, b) in enumerate(zip([1.0] * 12, [0.8, 1.2] * 6)):
            calm.step(obs(15 * t, replicas=4, ready=4, cpu=1.0, usage=a))
            wild.step(obs(15 * t, replicas=4, ready=4, cpu=1.0, usage=b))
        calm._last_hi = wild._last_hi = None
        self.assertGreater(wild._risk(1.0), calm._risk(1.0))

    def test_bounds_respected(self):
        st = TargetState(Spec(max_replicas=5, max_cpu=2.0))
        reps, cpu = 2, 0.5
        for t in range(0, 600, 5):
            full = t % 15 == 0
            d = (st.step if full else st.fast_step)(obs(t, replicas=reps, ready=reps, cpu=cpu, usage=50.0))
            reps = d.new_replicas or reps
            cpu = d.new_cpu or cpu
            self.assertLessEqual(reps, 5)
            self.assertLessEqual(cpu, 2.0 + 1e-9)

    def test_request_rate_learns_cost_per_request(self):
        st = TargetState(Spec())
        st.step(obs(0, cpu=1.0, usage=0.5, rate=10.0))
        self.assertAlmostEqual(st.cores_per_req, 0.05)
        d = st.step(obs(15, cpu=1.0, usage=0.5, rate=20.0))
        self.assertAlmostEqual(d.demand_cores, 20.0 * st.cores_per_req)

    def test_saturated_usage_is_treated_as_lower_bound(self):
        st = TargetState(Spec())
        d = st.step(obs(0, replicas=2, ready=2, cpu=0.5, usage=1.0))  # usage == limit
        self.assertAlmostEqual(d.demand_cores, 1.5)

    def test_inflight_buffer_tracks_applied_commands(self):
        st = TargetState(Spec())
        d = st.step(obs(0, usage=6.0))
        self.assertIsNotNone(d.new_replicas)
        self.assertEqual(st.controller.dh_history[0], d.new_replicas - 2)
        # 20 s boot at a 15 s interval: one in-flight slot. On the next tick the order is
        # still in the buffer, so the controller must not re-order the same capacity.
        self.assertEqual(len(st.controller.dh_history), 1)
        d2 = st.step(obs(15, replicas=d.new_replicas, ready=2, usage=6.0))
        self.assertIsNone(d2.new_replicas)


    def test_pod_ready_sooner_than_assumed_is_not_removed(self):
        """Found on the first live run: a pod ordered at one tick was Ready by the next
        (faster than podStartupSeconds). It was counted as serving and as in flight, so
        the controller removed it 15 s after adding it."""
        st = TargetState(Spec(max_replicas=10, max_cpu=2.0))
        for t in range(0, 150, 15):
            st.step(obs(t, cpu=1.6, usage=2.0 + 0.4 * (t // 15 % 2)))
        d, t = None, 150
        while t < 600 and (d is None or d.new_replicas is None):
            d = st.step(obs(t, cpu=1.6, usage=3.1))
            t += 15
        self.assertIsNotNone(d.new_replicas, "expected a scale-out")
        self.assertGreater(d.new_replicas, 2)
        n = d.new_replicas
        d2 = st.step(obs(t, replicas=n, ready=n, cpu=1.6, usage=3.1))
        self.assertEqual(sum(st.controller.dh_history), 0)
        self.assertTrue(d2.new_replicas is None or d2.new_replicas >= n)

    def test_rate_signal_never_estimates_below_measured_cpu(self):
        """Found in the first live replay: with the cost per request set 20 % too low the
        rate-based estimate said 75 % utilisation while the pods were saturated, and the
        operator held."""
        st = TargetState(Spec(cpu_per_request=0.05))
        d = st.step(obs(0, replicas=2, ready=2, cpu=0.5, usage=1.0, rate=15.0))
        self.assertGreaterEqual(d.demand_cores, 1.0 * Spec().saturation_boost - 1e-9)
        calm = TargetState(Spec(cpu_per_request=0.05)).step(obs(0, replicas=2, ready=2, cpu=1.0, usage=0.2, rate=10.0))
        self.assertAlmostEqual(calm.demand_cores, 0.5)             # the faster signal leads when it is higher

    def test_no_scale_in_straight_after_scale_out(self):
        """Seen in the replay smoke test: a pod added while a backlog drained was removed
        45 s later when the demand estimate fell back."""
        st = TargetState(Spec(max_replicas=10, max_cpu=2.0))
        d, t = None, 0
        while t < 600 and (d is None or d.new_replicas is None):
            d = st.step(obs(t, cpu=1.9, usage=3.7))
            t += 15
        self.assertIsNotNone(d.new_replicas)
        n, t_out = d.new_replicas, t - 15
        while t < t_out + 120:
            d2 = st.step(obs(t, replicas=n, ready=n, cpu=0.5, usage=0.3))
            self.assertIsNone(d2.new_replicas, f"scaled in {t - t_out:.0f} s after scaling out")
            t += 15

    def test_stale_upper_bound_does_not_shrink_capacity_below_demand(self):
        """A fresh operator sees a jump: the forecast bound from before the jump is below
        the demand observed now. Sizing must still cover the observed demand."""
        st = TargetState(Spec())
        st.step(obs(0, cpu=1.0, usage=0.6))
        demand = st._observed_demand(obs(5, cpu=1.0, usage=2.5))
        self.assertGreaterEqual(st._risk(demand), demand / Spec().target_utilization - 1e-6)

    def test_inflight_buffer_adopts_pods_it_did_not_order(self):
        st = TargetState(Spec())
        st.step(obs(0, replicas=4, ready=4, cpu=1.0, usage=2.0))
        st.step(obs(15, replicas=6, ready=4, cpu=1.0, usage=2.0))   # scaled by someone else
        st._reconcile_inflight(obs(30, replicas=6, ready=4, cpu=1.0, usage=2.0))
        self.assertEqual(sum(st.controller.dh_history), 2)


class TestManagerHelpers(unittest.TestCase):
    def setUp(self):
        try:
            import kubernetes  # noqa: F401
        except ImportError:
            self.skipTest("kubernetes package not installed")

    def test_cpu_quantities(self):
        import manager
        self.assertAlmostEqual(manager.parse_cpu("500m"), 0.5)
        self.assertAlmostEqual(manager.parse_cpu("2"), 2.0)
        self.assertEqual(manager.format_cpu(1.25), "1250m")

    def test_spec_from_cr_defaults_and_overrides(self):
        import manager
        sp = manager.spec_from_cr({"spec": {"scaleTargetRef": {"name": "web"}, "maxCPU": "2000m",
                                            "targetUtilization": 0.5,
                                            "metrics": {"cpuPerRequest": 0.02}}})
        self.assertEqual(sp.min_replicas, 2)
        self.assertAlmostEqual(sp.max_cpu, 2.0)
        self.assertAlmostEqual(sp.target_utilization, 0.5)
        self.assertAlmostEqual(sp.cpu_per_request, 0.02)


class FakeCluster:
    """In-memory stand-in for the three Kubernetes API objects the reconciler uses.
    Records every write so the test can check what would have been sent."""

    def __init__(self, replicas=2, ready=2, cpu="500m"):
        from types import SimpleNamespace as NS
        self.NS = NS
        self.replicas, self.cpu = replicas, cpu
        self.pods = [self._pod(f"web-{i}", cpu, i < ready) for i in range(replicas)]
        self.writes = []

    def _pod(self, name, cpu, ready):
        NS = self.NS
        return NS(metadata=NS(name=name, deletion_timestamp=None),
                  spec=NS(containers=[NS(resources=NS(limits={"cpu": cpu}))]),
                  status=NS(conditions=[NS(type="Ready", status="True" if ready else "False")]))

    # AppsV1Api
    def read_namespaced_deployment(self, name, ns):
        NS = self.NS
        c = NS(name="web", resources=NS(limits={"cpu": self.cpu}))
        return NS(metadata=NS(name=name), spec=NS(replicas=self.replicas,
                  selector=NS(match_labels={"app": name}), template=NS(spec=NS(containers=[c]))))

    def patch_namespaced_deployment_scale(self, name, ns, body):
        self.writes.append(("scale", name, body["spec"]["replicas"]))

    def patch_namespaced_deployment(self, name, ns, body):
        self.writes.append(("template", name, body))

    # CoreV1Api
    def list_namespaced_pod(self, ns, label_selector=None):
        self.writes.append(("list", label_selector))
        return self.NS(items=self.pods)

    def patch_namespaced_pod(self, name, ns, body):
        self.writes.append(("pod", name, body))

    def patch_namespaced_pod_resize(self, name, namespace, body):
        # same positional signature as kubernetes.client.CoreV1Api (no **kwargs catch-all)
        self.writes.append(("resize", f"/api/v1/namespaces/{namespace}/pods/{name}/resize",
                            body["spec"]["containers"][0]["resources"]["limits"]["cpu"]))

    # CustomObjectsApi
    def patch_namespaced_custom_object_status(self, g, v, ns, plural, name, body):
        self.writes.append(("status", name, body["status"]))


class StubProm:
    base_url = "stub"

    def __init__(self, value):
        self.value = value

    def query_scalar(self, q):
        return self.value


class TestReconcileLoop(unittest.TestCase):
    CR = {"metadata": {"namespace": "default", "name": "web-scaler"},
          "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "web"},
                   "minReplicas": 2, "maxReplicas": 10, "minCPU": "500m", "maxCPU": "2000m"}}

    def setUp(self):
        try:
            import manager  # noqa: F401
        except ImportError:
            self.skipTest("kubernetes package not installed")

    def reconciler(self, cluster, usage):
        import manager
        r = manager.ChimeraReconciler(StubProm(usage))
        r.core = r.apps = r.custom = cluster
        return r

    def test_overload_within_cpu_headroom_is_absorbed_in_place(self):
        """Saturated at 0.5 cores/pod, but the need fits in 2 pods' CPU headroom: the fast
        step resizes both pods in place and no pod is added (no 20 s boot)."""
        cluster = FakeCluster(replicas=2, ready=2, cpu="500m")
        r = self.reconciler(cluster, usage=1.0)                    # saturated: 1.0 of 1.0 cores
        r.reconcile(self.CR)                                       # full step: replicas
        r.reconcile(self.CR, full=False)                           # fast step 5 s later: CPU
        kinds = [w[0] for w in cluster.writes]
        self.assertIn(("list", "app=web"), cluster.writes)
        resizes = [w for w in cluster.writes if w[0] == "resize"]
        self.assertEqual(len(resizes), 2)                          # both running pods, in place
        self.assertTrue(resizes[0][1].endswith("/pods/web-0/resize"))
        self.assertGreater(float(resizes[0][2].rstrip("m")), 500)
        self.assertNotIn("scale", kinds)
        self.assertNotIn("template", kinds)                        # no rolling restart
        status = [w for w in cluster.writes if w[0] == "status"][-1][2]
        self.assertEqual(status["readyReplicas"], 2)

    def test_overload_beyond_cpu_headroom_scales_out(self):
        """Saturated at max CPU per pod: vertical has nothing left, so replicas are added."""
        cluster = FakeCluster(replicas=2, ready=2, cpu="2000m")
        self.reconciler(cluster, usage=4.0).reconcile(self.CR)    # 4.0 of 4.0 cores, maxCPU 2000m
        scale = [w for w in cluster.writes if w[0] == "scale"]
        self.assertEqual(len(scale), 1)
        self.assertGreater(scale[0][2], 2)

    def test_dry_run_never_writes(self):
        cluster = FakeCluster()
        r = self.reconciler(cluster, usage=1.0)
        r.dry_run = True
        r.reconcile(self.CR)
        self.assertEqual({w[0] for w in cluster.writes}, {"list"})

    def test_missing_metrics_hold_and_realign_new_pods(self):
        """No Prometheus data: no scaling decision; a pod still at the template size is
        brought to the allocation the other pods already have."""
        cluster = FakeCluster(replicas=2, ready=2, cpu="500m")
        cluster.pods[0].spec.containers[0].resources.limits["cpu"] = "1200m"
        self.reconciler(cluster, usage=None).reconcile(self.CR)
        self.assertNotIn("scale", [w[0] for w in cluster.writes])
        self.assertEqual([w for w in cluster.writes if w[0] == "resize"],
                         [("resize", "/api/v1/namespaces/default/pods/web-1/resize", "1200m")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
