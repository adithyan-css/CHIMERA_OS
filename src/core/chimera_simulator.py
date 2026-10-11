"""
CHIMERA Joint HPA+VPA Simulation Module

This module extends the AAPA SimPy simulator to support 2D horizontal/vertical scaling,
actuation delays, cross-coupling penalties, and in-place resizing dynamics (KEP-1287).
"""

import simpy
import numpy as np
import pandas as pd
import logging
from collections import deque
from typing import Dict, List, Tuple, Any, Callable
from dataclasses import dataclass, field
from src.core.simulator import Request, Pod, Metrics, ServerlessSimulator
from src.core.chimera_controller import ChimeraController
from src.data.features import TimeSeriesFeatureExtractor

logger = logging.getLogger(__name__)

@dataclass
class JointMetrics(Metrics):
    """Tracks joint HPA+VPA metrics including coupling penalties."""
    cpu_limit_history: List[float] = field(default_factory=list)
    coupling_penalties: List[float] = field(default_factory=list)
    total_cpu_allocated: float = 0.0  # Core-minutes
    
    # Validation & Observability Metrics (Phases 7, 10, 11)
    lyapunov_activations: int = 0
    drift_activations: int = 0
    coupling_activations: int = 0
    cqr_widths: List[float] = field(default_factory=list)
    cqr_coverages: List[float] = field(default_factory=list)
    replica_oscillations: int = 0
    vertical_scaling_actions: int = 0
    horizontal_scaling_actions: int = 0
    
    def get_coupling_penalty_rate(self) -> float:
        if not self.coupling_penalties:
            return 0.0
        return np.mean(self.coupling_penalties)

class ChimeraSimulator(ServerlessSimulator):
    """
    SimPy simulator supporting joint HPA+VPA scaling, KEP-1287,
    and extended REI metrics evaluation.
    """
    
    def __init__(self, 
                 env: simpy.Environment, 
                 controller: ChimeraController,
                 cqr_regressor = None,
                 drift_detector = None,
                 slo_threshold: float = 0.5,
                 use_inplace_resize: bool = True,
                 cpu_per_request: float = 0.01,
                 target_utilization: float = 0.70,
                 v_thresh: float = 0.10,
                 vertical_bridging: bool = False,
                 asymmetric_smoothing: bool = False,
                 online_conformal: bool = False,
                 naive_conformal_fallback: bool = False,
                 serving_replica_state: bool = False,
                 inner_vertical_period: float = 0.0,
                 inner_release_band: float = 0.0,
                 inner_up_band: float = 0.0,
                 inner_release_ticks: int = 1,
                 inner_release_headroom: float = 0.0,
                 inner_owns_vertical: bool = False,
                 gated_estimator: bool = False,
                 replica_band: float = 0.0,
                 one_sided_margin: bool = False,
                 adwin_margin_reset: bool = False,
                 risk_sizing: bool = False,
                 inner_min_dwell: float = 0.0
                 ):
        """Initialize simulator."""
        super().__init__(env, autoscaler=None, slo_threshold=slo_threshold, cpu_per_request=cpu_per_request)
        self.controller = controller
        self.cqr_regressor = cqr_regressor
        self.drift_detector = drift_detector
        self.use_inplace_resize = use_inplace_resize
        self.cpu_per_request = cpu_per_request
        self.target_utilization = target_utilization
        self.v_thresh = v_thresh
        # CHIMERA v2 mechanisms (off by default so legacy configs reproduce exactly):
        #  vertical_bridging    -- size the CPU target against the replicas that are
        #                          actually serving, so the 0.1 s vertical actuator covers
        #                          the 20 s horizontal boot gap and hands capacity back as
        #                          pods arrive (residual control of the slow actuator).
        #  asymmetric_smoothing -- EMA only on the way down ("fast attack, slow release"),
        #                          so smoothing never delays a scale-up.
        self.vertical_bridging = vertical_bridging
        self.asymmetric_smoothing = asymmetric_smoothing
        #  online_conformal     -- feed each realised load back into the CQR calibration
        #                          window (adaptive conformal), so the interval widens
        #                          after misses instead of staying at its offline width.
        self.online_conformal = online_conformal
        #  naive_conformal_fallback -- the reactive fallback gets its own conformal margin:
        #                          last-value forecast + the (1-alpha) quantile of recent
        #                          one-step load changes. When the learned model has nothing
        #                          to learn from (flat training window), its interval
        #                          collapses; this keeps a volatility-proportional margin.
        self.naive_conformal_fallback = naive_conformal_fallback
        #  serving_replica_state -- the delay-augmented model is h(t+1) = h(t) + dh(t-d):
        #                          an order only counts once its pods boot, and until then
        #                          it lives in the in-flight buffer. So the measured h must
        #                          be SERVING replicas. Feeding len(pods) -- which includes
        #                          pods the instant they are ordered -- counts every
        #                          in-flight pod twice (in h and in the buffer), and the
        #                          controller scales in right after scaling out.
        self.serving_replica_state = serving_replica_state
        #  inner_vertical_period -- cascade control. A fast inner loop (every N s) moves only
        #                          the 0.1 s vertical actuator to keep capacity on demand;
        #                          the 15 s outer loop keeps the joint LQR, replicas and the
        #                          in-flight buffer. Scale-ups fire on a statistically
        #                          significant surge in a short arrival window.
        #  inner_release_band   -- the inner loop also releases CPU once the need falls this
        #                          fraction below the allocation (re-acquiring costs 0.1 s,
        #                          so holding idle CPU through a 60 s cooldown is waste).
        self.inner_vertical_period = inner_vertical_period if use_inplace_resize else 0.0
        self.inner_release_band = inner_release_band
        #  inner_up_band        -- relative dead-band for inner-loop scale-ups: ignore a
        #                          need less than this fraction above the allocation, so the
        #                          loop does not chase every small fluctuation (churn).
        self.inner_up_band = inner_up_band
        #  inner_release_ticks / inner_release_headroom -- anti-ping-pong: release only after
        #                          the need has stayed below the band for this many inner
        #                          ticks, and release to need x (1 + headroom) rather than to
        #                          the need itself, so ordinary noise does not re-trigger a
        #                          scale-up on the next tick.
        self.inner_release_ticks = max(1, int(inner_release_ticks))
        self.inner_release_headroom = inner_release_headroom
        self._release_streak = 0
        #  inner_owns_vertical  -- proper cascade: the outer loop decides replicas and the
        #                          joint target, the inner loop is the ONLY thing that moves
        #                          CPU. Without this the outer LQR and the inner loop both
        #                          resize CPU and partly undo each other.
        self.inner_owns_vertical = inner_owns_vertical and self.inner_vertical_period > 0
        #  gated_estimator      -- size against the 60 s arrival rate, and switch to the 15 s
        #                          (or 5 s) rate only when it differs from the 60 s rate by more
        #                          than Poisson sampling noise. Capacity then holds still on a
        #                          flat-but-noisy load instead of chasing every wobble, yet still
        #                          moves within seconds on a genuine shift.
        self.gated_estimator = gated_estimator
        #  replica_band (kappa) -- move the slow, expensive actuator only when the fast one
        #                          runs out of range. Between h_lo = ceil(C / (kappa v_max))
        #                          and h_hi = floor(C / v_min) the replica count does not change
        #                          the CPU bill at all (cost is total cores), but every change
        #                          costs 20 s of CPU on a pod that cannot serve yet. So h* stays
        #                          at the current count while it is inside that band, and moves
        #                          to the gamma-split (clipped into the band) only when it is not.
        #                          kappa < 1 keeps vertical headroom for surges. 0 = off.
        self.replica_band = replica_band
        #  one_sided_margin     -- the margin is an UPPER bound on how far load can rise before
        #                          the next correction, so its conformal quantile is taken over
        #                          signed one-step changes (exact one-sided coverage). The
        #                          two-sided |change| version counts every drop as if it were a
        #                          rise: on a spiky trace it holds spike-sized capacity all the
        #                          time.
        self.one_sided_margin = one_sided_margin
        #  adwin_margin_reset   -- give the drift detector a job that matters: on a detected
        #                          regime change, drop the pre-change load history the reactive
        #                          margin is computed from (keep the last 8 samples), so the
        #                          margin re-learns the new regime instead of carrying the old
        #                          one's volatility for 10 minutes.
        self.adwin_margin_reset = adwin_margin_reset
        #  risk_sizing          -- choose capacity to balance expected SLO violations against a
        #                          price per core, instead of demand / target + an additive
        #                          margin. A request misses the SLO with p(u) = exp(-k (1-u)),
        #                          k = SLO / mean service time (learned online from completed
        #                          requests). Over forecast scenarios L_i (current load plus
        #                          recent signed one-step changes, plus the CQR upper bound):
        #                              mean_i L_i^2 cpp / c^2 * p'(L_i cpp / c) = lambda(u)
        #                          lambda(u) is the marginal value at utilisation u for a
        #                          CERTAIN load, so with no uncertainty c = L cpp / u exactly and
        #                          the target-utilisation knob keeps its meaning. Under
        #                          uncertainty the convex risk decides where headroom pays.
        self.risk_sizing = risk_sizing
        #  inner_min_dwell      -- minimum seconds between two inner-loop CPU resizes, except
        #                          when a statistically significant surge forces a scale-up.
        #                          Bounds resize churn without slowing the response to surges.
        self.inner_min_dwell = inner_min_dwell
        self._svc = deque(maxlen=4000) if risk_sizing else None
        # CPU changes between outer ticks, so cost is integrated every second instead of
        # being charged per tick (per-tick charging is exact only when state changes
        # solely at ticks, as for every other controller).
        self.exact_cost = self.inner_vertical_period > 0
        self._last_U = None
        self._last_interval = None
        
        # Override metrics
        self.metrics = JointMetrics()
        # One row per control decision: (t, replicas, cpu_per_pod, in-flight dh buffer)
        self.timeline = []
        
        # Scaling configurations
        self.scale_interval = 15.0  # 15s control loop
        self.horizontal_delay = 20.0 # 20s pod startup
        self.inplace_delay = 0.1     # 100ms in-place pod resize
        self.rolling_delay = 30.0    # 30s rolling pod restart
        
        self.current_cpu_cores = 1.0 # Init pod CPU
        
        # Telemetry history for controller
        self.conformal_buffer = []
        
        # Cooldown configurations
        self.last_scale_down_time = -300.0
        self.scale_down_cooldown = 300.0
        self.last_vertical_scale_time = -60.0
        self.vertical_scale_cooldown = 60.0
        
        # Feature extractor for dynamic CQR online prediction
        self.feature_extractor = TimeSeriesFeatureExtractor()
        self.prev_scaling_direction = 0  # To track oscillations
        
    def autoscaling_controller(self, features_list: List[np.ndarray] = None):
        """Autoscaling control loop using ChimeraController."""
        minute_idx = 0
        
        while True:
            # 1. Measure current cluster state
            current_load = self.calculate_current_load()
            current_replicas = len(self.pods)
            
            # Record current load history
            self.load_history.append(current_load)
            
            # Fetch or extract features for current time
            features = None
            if features_list:
                current_minute = int(self.env.now / 60)
                if current_minute < len(features_list):
                    features = features_list[current_minute]
            
            # If features not supplied or are dummy/empty, extract them dynamically
            if (features is None or np.all(features == 0)) and len(self.load_history) >= 5:
                try:
                    feat_dict = self.feature_extractor.extract_features(np.array(self.load_history))
                    features = np.array(list(feat_dict.values()))
                except Exception as e:
                    logger.debug(f"Dynamic feature extraction failed: {e}")
            
            # 2. Estimate prediction bounds via CQR regressor
            if self.cqr_regressor is not None and features is not None:
                # Score the previous interval against what actually arrived
                if self.online_conformal and self._last_interval is not None:
                    self.cqr_regressor.update_calibration_buffer(current_load, *self._last_interval)
                # Predict bounds using real CQR model
                L_t, U_t, u_width = self.cqr_regressor.predict_conformal_bounds(features)
                self._last_interval = (float(L_t[0]), float(U_t[0]))
                self._last_U = float(U_t[0])
                # Hybrid reactive-predictive fallback to handle out-of-distribution spikes
                use_ood = getattr(self, 'use_ood_fallback', True)
                if use_ood:
                    reactive = current_load
                    if self.naive_conformal_fallback:
                        reactive = current_load + self._naive_conformal_margin()
                    target_demand = max(reactive, float(U_t[0]))
                else:
                    target_demand = float(U_t[0])
                
                # Record observability metrics
                self.metrics.cqr_widths.append(u_width)
                is_covered = 1.0 if (L_t[0] <= current_load <= U_t[0]) else 0.0
                self.metrics.cqr_coverages.append(is_covered)
            else:
                # Fallback to simple load prediction
                u_width = 1.5
                target_demand = current_load * 1.2
            
            # 3. Compute optimal target resource allocation
            # target resource state: x* = [v*, h*]^T
            if self.risk_sizing:
                total_cores_needed = self._risk_cores(self._risk_scenarios(current_load))
            else:
                total_cores_needed = target_demand * self.cpu_per_request / self.target_utilization
            
            # Heuristic target split: balance CPU size and replicas
            h_star = int(np.ceil(np.sqrt(total_cores_needed / self.controller.gamma_ratio)))
            # Capacity consistency: once v* hits v_max the split must add replicas instead,
            # otherwise h* x v_max plans for less than the demand (and a fixed-size-pod
            # configuration gets sqrt-many pods). Integer-ceiling so h* x v_max >= need.
            h_star = max(h_star, int(np.ceil(total_cores_needed / self.controller.v_max - 1e-9)))
            if self.replica_band > 0:
                c = self.controller
                h_lo = int(np.ceil(total_cores_needed / (self.replica_band * c.v_max) - 1e-9))
                h_hi = max(h_lo, c.h_min, int(np.floor(total_cores_needed / c.v_min + 1e-9)))
                h_now = len(self.pods)                      # includes pods already ordered
                h_star = h_now if h_lo <= h_now <= h_hi else min(max(h_star, h_lo), h_hi)
            h_star = max(self.controller.h_min, min(h_star, self.controller.h_max))
            v_star = total_cores_needed / h_star
            if self.vertical_bridging:
                # Size CPU for the pods that can serve right now. While ordered pods are
                # still booting this is above the long-run split; once they are warm it
                # relaxes back to total/h_star.
                warm = sum(1 for p in self.pods if p.is_warm)
                v_star = max(v_star, total_cores_needed / max(1, warm))
            v_star = max(self.controller.v_min, min(v_star, self.controller.v_max))
            
            x_star_raw = np.array([v_star, h_star])
            
            # Apply EMA low-pass smoothing to the target trajectory to prevent high-frequency actuator chattering
            if not hasattr(self, 'x_star_smoothed'):
                self.x_star_smoothed = x_star_raw.copy()
            elif self.asymmetric_smoothing:
                ema = 0.5 * x_star_raw + 0.5 * self.x_star_smoothed
                self.x_star_smoothed = np.maximum(x_star_raw, ema)
            else:
                self.x_star_smoothed = 0.5 * x_star_raw + 0.5 * self.x_star_smoothed
                
            x_star = self.x_star_smoothed
            
            # Current state vector
            if self.serving_replica_state and self.controller.dh_history:
                h_measured = sum(1 for p in self.pods if p.is_warm)
            else:
                h_measured = current_replicas
            x_t = np.array([self.current_cpu_cores, h_measured])
            
            # 4. Invoke joint controller (LQR + Lyapunov)
            u_opt, x_next = self.controller.get_desired_allocation(
                x_t=x_t,
                x_star=x_star,
                uncertainty_width=u_width,
                tau=self.horizontal_delay
            )
            
            dv, dh = u_opt[0], u_opt[1]
            
            # Lyapunov filter activation: the controller records, in the same search,
            # whether the stability constraint changed the chosen action.
            if getattr(self.controller, 'last_filter_active', False):
                self.metrics.lyapunov_activations += 1

            # Track Coupling Penalty activation
            if dv * dh < 0:
                self.metrics.coupling_activations += 1
            
            # Record coupling penalty
            penalty = self.controller.compute_cross_coupling_penalty(x_t, u_opt)
            self.metrics.coupling_penalties.append(penalty)
            
            # 5. Actuate vertical scaling
            is_cooldown_expired = (self.env.now - self.last_vertical_scale_time >= self.vertical_scale_cooldown)
            can_vertical_scale = True if dv > 0 else is_cooldown_expired
            
            if self.inner_owns_vertical:
                pass                         # CPU belongs to the inner loop of the cascade
            elif abs(dv) >= self.v_thresh and can_vertical_scale:
                new_cpu = x_t[0] + dv
                if self.use_inplace_resize:
                    # In-place pod resize (KEP-1287) - fast actuation
                    yield self.env.timeout(self.inplace_delay)
                    for pod in self.pods:
                        pod.cpu_cores = new_cpu
                else:
                    # Rolling restart vertical resize - slow sequential actuation
                    for pod in self.pods:
                        yield self.env.timeout(self.rolling_delay / len(self.pods))
                        pod.cpu_cores = new_cpu
                self.current_cpu_cores = new_cpu
                self.metrics.scaling_actions += 1
                self.metrics.vertical_scaling_actions += 1
                self.last_vertical_scale_time = self.env.now
                
            # 6. Actuate horizontal scaling
            dh_int = int(np.round(dh))
            
            # Guardrail: do not scale down replicas if any pod is still starting up (not warm)
            use_guardrail = getattr(self.controller, 'use_scale_down_guardrail', True)
            if use_guardrail:
                has_cold_pods = any(not p.is_warm for p in self.pods)
                if has_cold_pods:
                    dh_int = max(0, dh_int)
                
            # Replica bounds apply to all pods, including ones still booting
            dh_int = min(dh_int, self.controller.h_max - len(self.pods))

            dh_applied = 0
            if dh_int > 0:
                # Scale up replicas
                for i in range(dh_int):
                    pod = Pod(
                        id=f"pod-{len(self.pods)}",
                        cpu_cores=self.current_cpu_cores,
                        startup_time=self.horizontal_delay,
                        warmup_time=self.env.now
                    )
                    self.pods.append(pod)
                self.metrics.scaling_actions += 1
                self.metrics.horizontal_scaling_actions += 1
                dh_applied = dh_int
                
                # Oscillation tracking
                if self.prev_scaling_direction < 0:
                    self.metrics.replica_oscillations += 1
                self.prev_scaling_direction = 1
                
            elif dh_int < 0:
                # Scale down replicas - enforce cooldown (e.g. 5 minutes)
                if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                    self.pods.sort(key=lambda p: p.last_used)
                    to_remove = min(abs(dh_int), len(self.pods) - self.controller.h_min)
                    if to_remove > 0:
                        self.pods = self.pods[to_remove:]
                        self.metrics.scaling_actions += 1
                        self.metrics.horizontal_scaling_actions += 1
                        self.last_scale_down_time = self.env.now
                        dh_applied = -to_remove
                        
                        # Oscillation tracking
                        if self.prev_scaling_direction > 0:
                            self.metrics.replica_oscillations += 1
                        self.prev_scaling_direction = -1
                        
            # Update LQR delay-augmented controller feedback history buffer
            if hasattr(self.controller, 'dh_history') and len(self.controller.dh_history) > 0:
                # Only scale-ups are delayed (pods must boot). A removed pod leaves the
                # serving set immediately, so it is already reflected in h; recording it in
                # the buffer as well would count it twice and trigger an immediate re-order.
                in_flight = max(0, dh_applied) if self.serving_replica_state else dh_applied
                self.controller.dh_history = [in_flight] + self.controller.dh_history[:-1]
                
            # 7. Monitor concept drift via ADWIN
            if self.drift_detector is not None:
                is_drift = self.drift_detector.add_element(abs(current_load - target_demand))
                if is_drift:
                    logger.info(f"ADWIN detected drift at time {self.env.now}s!")
                    self.metrics.drift_activations += 1
                    # Recalibrate: double Lyapunov uncertainty weight
                    self.controller.zeta = min(2.0, self.controller.zeta * 2)
                    if self.cqr_regressor is not None:
                        # Flush older scores in calibration buffer
                        self.cqr_regressor.calibration_scores = self.cqr_regressor.calibration_scores[-10:]
                    if self.adwin_margin_reset:
                        self.load_history = self.load_history[-8:]
            
            # Record status metrics
            self.metrics.cpu_limit_history.append(self.current_cpu_cores)
            if not self.exact_cost:
                total_active_cpu = sum(p.cpu_cores for p in self.pods)
                self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
                self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
            self.timeline.append((self.env.now, len(self.pods), self.current_cpu_cores,
                                  list(getattr(self.controller, 'dh_history', []))))
            
            # Wait for next scaling interval
            yield self.env.timeout(self.scale_interval)
            minute_idx += 1

    def _arrivals_within(self, window: float) -> int:
        """Number of arrivals in the last `window` seconds (window <= arrival_window)."""
        self.calculate_current_load()                    # prunes the buffer
        arrivals, cutoff = self.recent_arrivals, self.env.now - window
        lo, hi = 0, len(arrivals)
        while lo < hi:
            mid = (lo + hi) // 2
            if arrivals[mid] < cutoff:
                lo = mid + 1
            else:
                hi = mid
        return len(arrivals) - lo

    def _k_hat(self) -> float:
        """SLO / mean CPU service time of recent completed requests (5.0 until enough data)."""
        if self._svc is None or len(self._svc) < 200:
            return self.slo_threshold / 0.1
        return self.slo_threshold / max(1e-6, float(np.mean(self._svc)))

    @staticmethod
    def _viol_prob_and_slope(u, k):
        """p(u) and dp/du for the service model: slowdown 1/(1-u) below 0.95, linear above."""
        u = np.asarray(u, dtype=float)
        lo = u < 0.95
        p = np.empty_like(u)
        dp = np.empty_like(u)
        p[lo] = np.exp(-k * (1 - u[lo]))
        dp[lo] = k * p[lo]
        slow = 20.0 + 100.0 * (u[~lo] - 0.95)
        p[~lo] = np.exp(-k / slow)
        dp[~lo] = p[~lo] * k * 100.0 / slow ** 2
        return p, dp

    def _risk_cores(self, scenarios) -> float:
        """Capacity (cores) minimising expected violating requests/s + lambda * cores.

        lambda = p'(u0) u0^2 / cpp is the marginal value at target utilisation u0 for a
        CERTAIN load, so with one scenario the minimiser is exactly L cpp / u0. The violation
        probability is convex below u = 0.95 and saturates above it, so the objective is not
        convex in general: it is minimised globally on a grid and then refined, over
        capacities that keep the EXPECTED load at or below 95 % utilisation (below that
        floor the minimiser can be the degenerate 'allocate nothing, accept violations')."""
        L = np.maximum(np.asarray(scenarios, dtype=float), 1e-9)
        cpp, u0, k = self.cpu_per_request, self.target_utilization, self._k_hat()
        _, dp0 = self._viol_prob_and_slope(np.array([u0]), k)
        lam = dp0[0] * u0 ** 2 / cpp

        def cost(c):
            c = np.atleast_1d(c)
            u = L[None, :] * cpp / c[:, None]
            p, _ = self._viol_prob_and_slope(u.ravel(), k)
            return (L[None, :] * p.reshape(u.shape)).mean(axis=1) + lam * c

        lo = L.mean() * cpp / 0.95
        hi = max(lo * 1.01, L.max() * cpp / 0.05)
        grid = np.geomspace(lo, hi, 400)
        i = int(np.argmin(cost(grid)))
        a, b = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
        from scipy.optimize import minimize_scalar
        res = minimize_scalar(lambda c: float(cost(c)[0]), bounds=(a, b), method='bounded',
                              options={'xatol': 1e-9})
        return float(res.x)

    def _risk_scenarios(self, load: float):
        hist = np.asarray(self.load_history[-41:], dtype=float)
        steps = np.diff(hist) if len(hist) >= 6 else np.zeros(1)
        sc = list(np.maximum(load + steps, 0.0)) + [load]
        if self._last_U is not None:
            sc.append(self._last_U)
        return sc

    def _gated_rate(self, short: float, z: float = 2.5) -> float:
        """Arrival-rate estimate that ignores Poisson noise but not real shifts.

        Base estimate: the 60 s window. A shorter window (15 s, then `short` s) replaces it
        only if its count differs from what the 60 s rate predicts by more than z standard
        deviations of a Poisson count -- i.e. the load has really moved."""
        long_w = min(self.arrival_window, 60.0, max(self.env.now, 1.0))
        rate = self._arrivals_within(long_w) / long_w
        for w in (self.scale_interval, short):
            n = self._arrivals_within(w)
            expected = rate * w
            if abs(n - expected) > z * np.sqrt(max(expected, 1.0)):
                rate = n / w
        return rate

    def _resize_all(self, new_cpu: float):
        yield self.env.timeout(self.inplace_delay)
        for pod in self.pods:
            pod.cpu_cores = new_cpu
        self.current_cpu_cores = new_cpu
        self.metrics.scaling_actions += 1
        self.metrics.vertical_scaling_actions += 1
        self.last_vertical_scale_time = self.env.now

    def vertical_inner_loop(self):
        """Fast inner loop of the cascade: keep CPU on demand between outer ticks."""
        p = self.inner_vertical_period
        c = self.controller
        while True:
            yield self.env.timeout(p)
            surge = False
            if self.gated_estimator:
                load = self._gated_rate(p)
            else:
                load = self.calculate_current_load()                 # 15 s window
                n_short = self._arrivals_within(p)
                expected = load * p
                # Poisson surge test: short-window count significantly above the 15 s rate
                if n_short - expected > 2.0 * np.sqrt(max(expected, 1.0)):
                    load = n_short / p
                    surge = True
            if self.risk_sizing:
                cores = self._risk_cores(self._risk_scenarios(load))
            else:
                demand = load
                if self.naive_conformal_fallback:
                    demand += self._naive_conformal_margin()
                if self._last_U is not None:
                    demand = max(demand, self._last_U)
                cores = demand * self.cpu_per_request / self.target_utilization
            warm = max(1, sum(1 for pod in self.pods if pod.is_warm))
            v_need = float(np.clip(cores / warm, c.v_min, c.v_max))
            v = self.current_cpu_cores
            if not surge and self.env.now - self.last_vertical_scale_time < self.inner_min_dwell:
                continue
            if v_need >= max(v + self.v_thresh, v * (1 + self.inner_up_band)):
                self._release_streak = 0
                yield from self._resize_all(v_need)
            elif (self.inner_release_band > 0 and v_need < v * (1 - self.inner_release_band)
                  and v - v_need >= self.v_thresh):
                self._release_streak += 1
                if self._release_streak >= self.inner_release_ticks:
                    self._release_streak = 0
                    target = min(v, max(c.v_min, v_need * (1 + self.inner_release_headroom)))
                    if v - target >= self.v_thresh:
                        yield from self._resize_all(target)
            else:
                self._release_streak = 0

    def cost_integrator(self):
        """Charge allocated CPU and pods second by second."""
        while True:
            self.metrics.total_cpu_allocated += sum(pod.cpu_cores for pod in self.pods) / 60.0
            self.metrics.pod_minutes += len(self.pods) / 60.0
            yield self.env.timeout(1.0)

    def _naive_conformal_margin(self, alpha: float = 0.1, window: int = 40) -> float:
        """Split-conformal half-width for a last-value forecast: the ceil((n+1)(1-alpha))-th
        smallest of the last `window` absolute one-step changes in measured load."""
        hist = np.asarray(self.load_history[-(window + 1):], dtype=float)
        if len(hist) < 6:
            return 0.0
        steps = np.diff(hist)
        res = np.sort(steps if self.one_sided_margin else np.abs(steps))
        n = len(res)
        k = min(n - 1, int(np.ceil((n + 1) * (1 - alpha))) - 1)
        return max(0.0, float(res[k]))

    def run(self, workload: List[float], function_id: str,
            execution_time_dist: Callable, features_list: List[np.ndarray] = None,
            duration: float = None):
        """Run simulation."""
        # Initialize cluster with min replicas
        self.pods = []
        for i in range(self.controller.h_min):
            pod = Pod(
                id=f"pod-{i}",
                cpu_cores=self.controller.v_min,
                is_warm=True,
                warmup_time=0.0
            )
            self.pods.append(pod)
        self.current_cpu_cores = self.controller.v_min
        
        # Start processes
        self.env.process(self.request_generator(workload, function_id, execution_time_dist))
        self.env.process(self.request_handler())
        self.env.process(self.pod_warmer())
        self.env.process(self.autoscaling_controller(features_list))
        if self.exact_cost:
            self.env.process(self.cost_integrator())
        if self.inner_vertical_period > 0:
            self.env.process(self.vertical_inner_loop())
        
        if duration is None:
            duration = len(workload) * 60
            
        snap = {}
        if self.score_from > 0:
            def snapshot():
                # Just before the scoring start, so a tick at score_from (which charges the
                # interval that follows it) is counted.
                yield self.env.timeout(max(0.0, self.score_from - 1e-6))
                m = self.metrics
                for f in self._COUNTERS:
                    snap[f] = getattr(m, f)
                for f in self._SERIES:
                    snap[f] = len(getattr(m, f))
            self.env.process(snapshot())

        self.env.run(until=duration)
        if snap:
            m = self.metrics
            for f in self._COUNTERS:
                setattr(m, f, getattr(m, f) - snap[f])
            for f in self._SERIES:
                setattr(m, f, getattr(m, f)[snap[f]:])
        return self.metrics

    # Cost/action counters and per-tick series that warm-up must not contribute to
    _COUNTERS = ('total_cpu_allocated', 'pod_minutes', 'scaling_actions', 'vertical_scaling_actions',
                 'horizontal_scaling_actions', 'replica_oscillations', 'lyapunov_activations',
                 'drift_activations', 'coupling_activations')
    _SERIES = ('cpu_limit_history', 'coupling_penalties', 'cqr_widths', 'cqr_coverages')

def calculate_extended_rei(metrics: JointMetrics, delta: float = 0.15) -> float:
    """
    Calculate extended REI score.
    Implements Phase 2 Section 6.2 formula.
    """
    s_slo = 1.0 - metrics.get_slo_violation_rate()
    
    # Normalize resource usage (assumes footprint cap of 1000.0 CPU-minutes)
    s_eff = 1.0 - min(metrics.total_cpu_allocated / 1000.0, 1.0)
    
    # Normalize scaling actions (assuming max 50 actions)
    s_stab = 1.0 - min(metrics.scaling_actions / 50.0, 1.0)
    
    # Normalize coupling penalty score
    s_coupling = metrics.get_coupling_penalty_rate()
    
    # Extended REI weighted score
    alpha, beta, gamma = 0.4, 0.3, 0.3
    rei_base = alpha * s_slo + beta * s_eff + gamma * s_stab
    
    # Deduct coupling penalty
    rei_ext = rei_base - delta * s_coupling
    return float(np.clip(rei_ext, 0.0, 1.0))
