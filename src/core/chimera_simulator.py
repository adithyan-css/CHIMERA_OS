"""
CHIMERA Joint HPA+VPA Simulation Module

This module extends the AAPA SimPy simulator to support 2D horizontal/vertical scaling,
actuation delays, cross-coupling penalties, and in-place resizing dynamics (KEP-1287).
"""

import simpy
import numpy as np
import pandas as pd
import logging
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
                 v_thresh: float = 0.10
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
        
        # Override metrics
        self.metrics = JointMetrics()
        
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
                # Predict bounds using real CQR model
                L_t, U_t, u_width = self.cqr_regressor.predict_conformal_bounds(features)
                # Hybrid reactive-predictive fallback to handle out-of-distribution spikes
                use_ood = getattr(self, 'use_ood_fallback', True)
                if use_ood:
                    target_demand = max(current_load, float(U_t[0]))
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
            total_cores_needed = target_demand * self.cpu_per_request / self.target_utilization
            
            # Heuristic target split: balance CPU size and replicas
            h_star = int(np.ceil(np.sqrt(total_cores_needed / self.controller.gamma_ratio)))
            h_star = max(self.controller.h_min, min(h_star, self.controller.h_max))
            v_star = total_cores_needed / h_star
            v_star = max(self.controller.v_min, min(v_star, self.controller.v_max))
            
            x_star_raw = np.array([v_star, h_star])
            
            # Apply EMA low-pass smoothing to the target trajectory to prevent high-frequency actuator chattering
            if not hasattr(self, 'x_star_smoothed'):
                self.x_star_smoothed = x_star_raw.copy()
            else:
                self.x_star_smoothed = 0.5 * x_star_raw + 0.5 * self.x_star_smoothed
                
            x_star = self.x_star_smoothed
            
            # Current state vector
            x_t = np.array([self.current_cpu_cores, current_replicas])
            
            # 4. Invoke joint controller (LQR + Lyapunov)
            u_opt, x_next = self.controller.get_desired_allocation(
                x_t=x_t,
                x_star=x_star,
                uncertainty_width=u_width,
                tau=self.horizontal_delay
            )
            
            dv, dh = u_opt[0], u_opt[1]
            
            # Track Lyapunov constraint activation via double search (enforced vs unconstrained)
            orig_sigma = self.controller.sigma
            self.controller.sigma = 100.0
            u_opt_unconstrained, _ = self.controller.get_desired_allocation(
                x_t=x_t,
                x_star=x_star,
                uncertainty_width=u_width,
                tau=self.horizontal_delay
            )
            self.controller.sigma = orig_sigma
            
            if not np.allclose(u_opt, u_opt_unconstrained, atol=1e-3):
                self.metrics.lyapunov_activations += 1
                import os
                if os.environ.get("CHIMERA_DEBUG_LYAP"):
                    with open("/tmp/chimera_rerun/lyap_debug.log", "a") as fh:
                        dv_diff = abs(u_opt[0]-u_opt_unconstrained[0])
                        dh_diff = abs(int(np.round(u_opt[1])) - int(np.round(u_opt_unconstrained[1])))
                        fh.write(f"t={self.env.now:.1f} dv_diff={dv_diff:.4f} dh_diff={dh_diff} applied_dv={dv:.4f} v_thresh={self.v_thresh}\n")

            
            # Track Coupling Penalty activation
            if dv * dh < 0:
                self.metrics.coupling_activations += 1
            
            # Record coupling penalty
            penalty = self.controller.compute_cross_coupling_penalty(x_t, u_opt)
            self.metrics.coupling_penalties.append(penalty)
            
            # 5. Actuate vertical scaling
            is_cooldown_expired = (self.env.now - self.last_vertical_scale_time >= self.vertical_scale_cooldown)
            can_vertical_scale = True if dv > 0 else is_cooldown_expired
            
            if abs(dv) >= self.v_thresh and can_vertical_scale:
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
                self.controller.dh_history = [dh_applied] + self.controller.dh_history[:-1]
                
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
            
            # Record status metrics
            self.metrics.cpu_limit_history.append(self.current_cpu_cores)
            total_active_cpu = sum(p.cpu_cores for p in self.pods)
            self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
            self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
            
            # Wait for next scaling interval
            yield self.env.timeout(self.scale_interval)
            minute_idx += 1

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
        
        if duration is None:
            duration = len(workload) * 60
            
        self.env.run(until=duration)
        return self.metrics

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
