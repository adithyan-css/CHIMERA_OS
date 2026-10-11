"""
Minimal Prometheus HTTP API client for the CHIMERA operator.

Talks to the standard instant-query endpoint (GET /api/v1/query) using only the
standard library, so the operator image needs no extra dependency.

    prom = PrometheusClient("http://prometheus.monitoring.svc:9090")
    cores = prom.query_scalar('sum(rate(container_cpu_usage_seconds_total{namespace="default"}[1m]))')

Reference: https://prometheus.io/docs/prometheus/latest/querying/api/
"""

import json
import math
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple


class PrometheusError(RuntimeError):
    """Prometheus was unreachable, or answered with status != "success"."""


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 5.0,
                 bearer_token: Optional[str] = None, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # In-cluster Prometheus behind kube-rbac-proxy / OpenShift wants the SA token
        if bearer_token is None:
            token_file = os.environ.get("PROMETHEUS_TOKEN_FILE")
            if token_file and os.path.exists(token_file):
                with open(token_file) as fh:
                    bearer_token = fh.read().strip()
        self.bearer_token = bearer_token
        self._ssl = None if verify_tls else ssl._create_unverified_context()

    def query(self, promql: str) -> List[Tuple[Dict[str, str], float]]:
        """Run an instant query. Returns [(labels, value)] for vector/scalar results."""
        url = f"{self.base_url}/api/v1/query?" + urllib.parse.urlencode({"query": promql})
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if self.bearer_token:
            req.add_header("Authorization", f"Bearer {self.bearer_token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Prometheus returns a JSON error body with 400/422/503
            try:
                body = json.loads(exc.read().decode("utf-8"))
                raise PrometheusError(f"{body.get('errorType')}: {body.get('error')}") from exc
            except (ValueError, AttributeError):
                raise PrometheusError(f"HTTP {exc.code} from {self.base_url}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PrometheusError(f"cannot query {self.base_url}: {exc}") from exc

        if body.get("status") != "success":
            raise PrometheusError(f"{body.get('errorType')}: {body.get('error')}")

        data = body.get("data", {})
        rtype, result = data.get("resultType"), data.get("result")
        if rtype == "vector":
            return [(item.get("metric", {}), float(item["value"][1])) for item in result]
        if rtype == "scalar":
            return [({}, float(result[1]))]
        raise PrometheusError(f"unsupported result type {rtype!r} (use an instant vector or scalar query)")

    def query_scalar(self, promql: str) -> Optional[float]:
        """Sum of all series in the result, or None when the query matched nothing.

        None (no data) is different from 0.0 (a real zero): the operator holds its last
        allocation when a metric is missing rather than scaling to the floor.
        """
        series = self.query(promql)
        values = [v for _, v in series if not math.isnan(v)]
        if not values:
            return None
        return float(sum(values))
