"""
CHIMERA operator -- control logic, independent of the Kubernetes API.

One `TargetState` per ChimeraAutoscaler object. Each control tick the manager hands it
an `Observation` (read from the cluster and Prometheus) and applies the `Decision` it
returns. Keeping this free of any client library means the exact decision path can be
unit-tested without a cluster (tests/test_operator.py).

The step mirrors ChimeraSimulator.autoscaling_controller (the code that was evaluated):
    forecast interval -> target demand -> target split x* = [v*, h*]
    -> residual vertical bridging -> fast-attack/slow-release smoothing
    -> delay-augmented LQR + Lyapunov filter -> thresholds, cooldowns, guardrail
    -> in-flight command buffer update -> ADWIN drift monitor.

Forecasting difference, stated plainly: the simulator uses a LightGBM CQR model trained
per trace offline. A freshly deployed operator has no training history for its target,
so it uses online split-conformal prediction around an EWMA forecast -- the same
conformal interval construction, with a model that needs no offline training.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import math
import sys
import os

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.core.chimera_controller import ChimeraController   # noqa: E402
from src.models.drift_detector import AdwinDriftDetector     # noqa: E402


@dataclass
class Spec:
    """Parsed ChimeraAutoscaler spec (see k8s/crd.yaml)."""
    min_replicas: int = 2
    max_replicas: int = 30
    min_cpu: float = 0.5
    max_cpu: float = 4.0
    target_utilization: float = 0.70
    ratio_cpu_to_replicas: float = 0.5
    pod_startup_seconds: float = 20.0
    cpu_per_request: Optional[float] = None   # cores per req/s; learned online if None
    scale_down_cooldown: float = 300.0
    vertical_cooldown: float = 60.0
    v_thresh: float = 0.05
    saturation_boost: float = 1.5
    # cascade inner loop (fast_step): release CPU once the need is this fraction below the
    # allocation for this many consecutive fast ticks
    release_band: float = 0.15
    release_ticks: int = 2
    # v4: the fast step is the only thing that moves CPU; replicas move only when vertical
    # resizing cannot absorb the change (kappa = replica_band; 0 disables)
    inner_owns_cpu: bool = True
    replica_band: float = 0.85
    # v5: risk-optimal sizing. k = SLO / mean per-request CPU service time (set from the CRD;
    # both in seconds). With a certain forecast the result is exactly demand / target.
    risk_sizing: bool = True
    slo_seconds: float = 0.5
    service_time_seconds: float = 0.1
    up_band: float = 0.10


@dataclass
class Observation:
    now: float                       # seconds (monotonic or wall clock)
    replicas: int                    # desired replicas currently on the Deployment
    ready_replicas: int              # pods that are Ready and serving
    cpu_per_pod: float               # current CPU allocation per pod, cores
    cpu_usage: Optional[float]       # total CPU used by the target, cores (Prometheus)
    request_rate: Optional[float]    # req/s (Prometheus), optional


@dataclass
class Decision:
    new_cpu: Optional[float]         # None = leave pod CPU unchanged
    new_replicas: Optional[int]      # None = leave replicas unchanged
    demand_cores: float
    interval_width: float
    drift: bool
    reason: str


def _viol_slope(u, k):
    """d/du of the per-request violation probability p(u) = exp(-k (1 - u)) (u < 0.95),
    with the linear-slowdown branch above 0.95 (matches the simulator's service model)."""
    u = np.asarray(u, dtype=float)
    lo = u < 0.95
    out = np.empty_like(u)
    out[lo] = k * np.exp(-k * (1 - u[lo]))
    slow = 20.0 + 100.0 * (u[~lo] - 0.95)
    out[~lo] = np.exp(-k / slow) * k * 100.0 / slow ** 2
    return out


def _viol_prob(u, k):
    u = np.asarray(u, dtype=float)
    lo = u < 0.95
    out = np.empty_like(u)
    out[lo] = np.exp(-k * (1 - u[lo]))
    out[~lo] = np.exp(-k / (20.0 + 100.0 * (u[~lo] - 0.95)))
    return out


def risk_cores(scenarios, target_utilization: float, k: float) -> float:
    """Cores minimising mean_i L_i p(L_i / c) + lambda c over demand scenarios L_i (cores),
    with lambda = p'(u0) u0^2 so that a certain demand L gives exactly L / u0. Minimised
    globally on a grid (the objective is not convex: p saturates above u = 0.95), over
    capacities keeping the expected demand at or below 95 % utilisation."""
    L = np.maximum(np.asarray(scenarios, dtype=float), 1e-9)
    lam = float(_viol_slope(np.array([target_utilization]), k)[0]) * target_utilization ** 2

    def cost(c):
        c = np.atleast_1d(c)
        u = L[None, :] / c[:, None]
        return (L[None, :] * _viol_prob(u.ravel(), k).reshape(u.shape)).mean(axis=1) + lam * c

    lo = L.mean() / 0.95
    hi = max(lo * 1.01, L.max() / 0.05)
    grid = np.geomspace(lo, hi, 400)
    i = int(np.argmin(cost(grid)))
    from scipy.optimize import minimize_scalar
    res = minimize_scalar(lambda c: float(cost(c)[0]), bounds=(grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]),
                          method='bounded', options={'xatol': 1e-9})
    return float(res.x)


class OnlineConformalForecaster:
    """EWMA point forecast with a split-conformal interval from recent one-step errors."""

    def __init__(self, alpha: float = 0.1, halflife_steps: float = 4.0, window: int = 40):
        self.alpha = alpha
        self.decay = 0.5 ** (1.0 / halflife_steps)
        self.window = window
        self.level: Optional[float] = None
        self.residuals: List[float] = []
        self.steps: List[float] = []          # signed one-step changes, for risk scenarios
        self._last: Optional[float] = None

    def update(self, y: float) -> None:
        if self._last is not None:
            self.steps = (self.steps + [y - self._last])[-self.window:]
        self._last = y
        if self.level is None:
            self.level = y
            return
        self.residuals.append(abs(y - self.level))
        self.residuals = self.residuals[-self.window:]
        self.level = self.decay * self.level + (1 - self.decay) * y

    def interval(self):
        if self.level is None:
            return 0.0, 0.0
        if len(self.residuals) < 5:
            q = 0.5 * self.level          # wide until there is evidence
        else:
            n = len(self.residuals)
            k = min(n - 1, int(math.ceil((n + 1) * (1 - self.alpha))) - 1)
            q = float(np.sort(self.residuals)[k])
        return max(0.0, self.level - q), self.level + q


class TargetState:
    def __init__(self, spec: Spec):
        self.spec = spec
        # sigma=100 disables the runtime Lyapunov filter, as in the evaluated v4 controller:
        # the Riccati P already certifies that the LQR law decreases V = e'Pe.
        self.controller = ChimeraController(
            v_min=spec.min_cpu, v_max=spec.max_cpu,
            h_min=spec.min_replicas, h_max=spec.max_replicas,
            gamma_ratio=spec.ratio_cpu_to_replicas,
            C_max=spec.max_cpu * spec.max_replicas, sigma=100.0)
        self.forecaster = OnlineConformalForecaster()
        self.drift = AdwinDriftDetector(delta=0.05)
        self.cores_per_req: Optional[float] = spec.cpu_per_request
        self.x_star_smoothed: Optional[np.ndarray] = None
        self.last_scale_down = -math.inf
        self.last_vertical = -math.inf
        self.last_demand: Optional[float] = None
        self._last_hi: Optional[float] = None
        self._release_streak = 0

    # -- demand estimation ----------------------------------------------------------
    def _observed_demand(self, obs: Observation) -> Optional[float]:
        """Current demand in cores. CPU usage is capped by the limit when pods throttle,
        so a saturated reading is treated as a lower bound and boosted."""
        allocated = obs.cpu_per_pod * max(1, obs.ready_replicas)
        if obs.request_rate is not None and obs.cpu_usage is not None and obs.request_rate > 0:
            if obs.cpu_usage < 0.9 * allocated:   # learn cost/request only when unsaturated
                sample = obs.cpu_usage / obs.request_rate
                self.cores_per_req = sample if self.cores_per_req is None else \
                    0.8 * self.cores_per_req + 0.2 * sample
        if obs.request_rate is not None and self.cores_per_req is not None:
            return obs.request_rate * self.cores_per_req
        if obs.cpu_usage is not None:
            if obs.cpu_usage >= 0.95 * allocated:
                return obs.cpu_usage * self.spec.saturation_boost
            return obs.cpu_usage
        return None

    # -- one control tick -----------------------------------------------------------
    def step(self, obs: Observation) -> Decision:
        s, c = self.spec, self.controller
        demand = self._observed_demand(obs)
        if demand is None:
            return Decision(None, None, 0.0, 0.0, False, "no metrics: holding allocation")

        # 1. Forecast interval; hybrid reactive fallback for out-of-distribution jumps
        self.forecaster.update(demand)
        lo, hi = self.forecaster.interval()
        width = hi - lo
        target_demand = max(demand, hi)
        self._last_hi = hi

        # 2. Target split x* = [v*, h*]
        cores_needed = (self._risk(demand) if s.risk_sizing else target_demand / s.target_utilization)
        h_star = int(np.ceil(np.sqrt(cores_needed / c.gamma_ratio)))
        # Capacity consistency: once v* hits v_max the split must add replicas instead,
        # otherwise h* x v_max plans for less than the demand (and a fixed-size-pod
        # configuration gets sqrt-many pods). Integer-ceiling so h* x v_max >= need.
        h_star = max(h_star, int(np.ceil(cores_needed / c.v_max - 1e-9)))
        if s.replica_band > 0:
            # Pods do not change the CPU bill between these bounds, but each new one boots
            # for 20 s: keep the current count while vertical resizing can absorb the change.
            h_lo = int(np.ceil(cores_needed / (s.replica_band * c.v_max) - 1e-9))
            h_hi = max(h_lo, c.h_min, int(np.floor(cores_needed / c.v_min + 1e-9)))
            h_star = obs.replicas if h_lo <= obs.replicas <= h_hi else min(max(h_star, h_lo), h_hi)
        h_star = max(c.h_min, min(h_star, c.h_max))
        v_star = cores_needed / h_star
        # Residual vertical bridging: size CPU for the pods that can serve now
        v_star = max(v_star, cores_needed / max(1, obs.ready_replicas))
        v_star = max(c.v_min, min(v_star, c.v_max))
        raw = np.array([v_star, float(h_star)])
        if self.x_star_smoothed is None:
            self.x_star_smoothed = raw
        else:   # fast attack, slow release
            self.x_star_smoothed = np.maximum(raw, 0.5 * raw + 0.5 * self.x_star_smoothed)

        # 3. Joint delay-augmented LQR with the Lyapunov safety filter
        # h in the delay-augmented model is SERVING replicas; ordered-but-booting pods
        # are carried by the in-flight buffer (counting them in both double-counts them)
        x_t = np.array([obs.cpu_per_pod, float(obs.ready_replicas)])
        self._reconcile_inflight(obs)
        u, _ = c.get_desired_allocation(x_t, self.x_star_smoothed, width, tau=s.pod_startup_seconds)
        dv, dh = float(u[0]), int(np.round(u[1]))

        reasons = []
        new_cpu = None
        if s.inner_owns_cpu:
            pass                     # CPU belongs to fast_step (the cascade's inner loop)
        elif abs(dv) >= s.v_thresh and (dv > 0 or obs.now - self.last_vertical >= s.vertical_cooldown):
            new_cpu = float(np.clip(obs.cpu_per_pod + dv, c.v_min, c.v_max))
            self.last_vertical = obs.now
            reasons.append(f"cpu {obs.cpu_per_pod:.2f}->{new_cpu:.2f}")

        # Guardrail: never remove replicas while ordered ones are still booting
        if obs.ready_replicas < obs.replicas:
            dh = max(0, dh)
        applied_dh = 0
        new_replicas = None
        if dh > 0:
            applied_dh = max(0, min(dh, c.h_max - obs.replicas))
        elif dh < 0 and obs.now - self.last_scale_down >= s.scale_down_cooldown:
            applied_dh = -min(-dh, obs.replicas - c.h_min)
            if applied_dh < 0:
                self.last_scale_down = obs.now
        if applied_dh:
            new_replicas = obs.replicas + applied_dh
            reasons.append(f"replicas {obs.replicas}->{new_replicas}")

        # 4. Feed the command actually applied into the in-flight buffer
        if c.dh_history:
            # only scale-ups are in flight; removed pods stop serving immediately
            c.dh_history = [max(0, applied_dh)] + c.dh_history[:-1]

        # 5. Drift monitor on the forecast error
        drift = False
        if self.last_demand is not None:
            drift = bool(self.drift.add_element(abs(demand - self.last_demand)))
            if drift:
                c.zeta = min(2.0, c.zeta * 2)
                # re-learn the margin from the new regime (v4: adwin_margin_reset)
                self.forecaster.residuals = self.forecaster.residuals[-8:]
                reasons.append("drift detected: margin reset")
        self.last_demand = target_demand

        return Decision(new_cpu, new_replicas, demand, width, drift,
                        ", ".join(reasons) or "hold")

    def _reconcile_inflight(self, obs: Observation) -> None:
        """Make the in-flight buffer agree with the cluster. The buffer assumes every
        ordered pod takes pod_startup_seconds to serve; a pod that is Ready sooner would
        otherwise be counted both as serving and as in flight, and the controller would
        remove the pod it had just added (seen on the first live run). What is really in
        flight is observable: desired replicas minus Ready ones."""
        hist = list(self.controller.dh_history)
        if not hist:
            return
        excess = sum(hist) - max(0, obs.replicas - obs.ready_replicas)
        for i in reversed(range(len(hist))):      # oldest orders arrive first
            take = min(hist[i], max(0, excess))
            hist[i] -= take
            excess -= take
        if excess < 0:                            # pods booting that this buffer never saw
            hist[-1] += -excess
        self.controller.dh_history = hist

    def _risk(self, demand: float) -> float:
        """Risk-optimal cores: scenarios = demand + recent signed changes, plus the
        forecaster's upper bound."""
        s = self.spec
        sc = [max(0.0, demand + d) for d in self.forecaster.steps] + [demand]
        if self._last_hi is not None:
            sc.append(self._last_hi)
        return risk_cores(sc, s.target_utilization, s.slo_seconds / max(1e-6, s.service_time_seconds))

    # -- cascade inner loop -----------------------------------------------------------
    def fast_step(self, obs: Observation) -> Decision:
        """Vertical-only tick between full steps (the simulator's vertical_inner_loop).

        Keeps CPU per serving pod on demand: scale up as soon as the need exceeds the
        allocation, release once it has stayed `release_band` below it for
        `release_ticks` ticks. Replicas and the in-flight buffer are left to step().
        """
        s, c = self.spec, self.controller
        demand = self._observed_demand(obs)
        if demand is None:
            return Decision(None, None, 0.0, 0.0, False, "no metrics: holding allocation")
        if s.risk_sizing:
            cores = self._risk(demand)
        else:
            if self._last_hi is not None:
                demand = max(demand, self._last_hi)
            cores = demand / s.target_utilization
        v_need = float(np.clip(cores / max(1, obs.ready_replicas), c.v_min, c.v_max))
        v = obs.cpu_per_pod
        new_cpu, reason = None, "hold"
        if v_need >= max(v + s.v_thresh, v * (1 + s.up_band)):
            self._release_streak = 0
            new_cpu, reason = v_need, f"fast: cpu {v:.2f}->{v_need:.2f}"
        elif v_need < v * (1 - s.release_band) and v - v_need >= s.v_thresh:
            self._release_streak += 1
            if self._release_streak >= s.release_ticks:
                self._release_streak = 0
                new_cpu, reason = v_need, f"fast release: cpu {v:.2f}->{v_need:.2f}"
        else:
            self._release_streak = 0
        if new_cpu is not None:
            self.last_vertical = obs.now
        return Decision(new_cpu, None, demand, 0.0, False, reason)
