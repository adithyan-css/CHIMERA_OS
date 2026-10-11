import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from pathlib import Path
import logging
import simpy
import json
from typing import List, Tuple, Any, Dict, Callable
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF

from src.core.chimera_controller import ChimeraController
from src.core.kosmos_simulator import KOSMOSSimulator
from src.core.chimera_simulator import ChimeraSimulator, calculate_extended_rei, JointMetrics
from src.core.simulator import Pod, HPAAutoscaler, AAPAAutoscaler
from src.models.drift_detector import AdwinDriftDetector
from src.models.cqr_uncertainty import ConformalQuantileRegressor
from src.data.features import TimeSeriesFeatureExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Fair Baseline Autoscalers with pod size scaling corrections (Phase 8) ---

class DecoupledHPAAutoscaler(HPAAutoscaler):
    """HPA baseline corrected to utilize actual pod CPU limit (1.5 cores) instead of 1.0."""
    def __init__(self, target_cpu_utilization: float = 0.7, cpu_per_request: float = 0.01, pod_cpu: float = 1.5):
        super().__init__(target_cpu_utilization=target_cpu_utilization, cpu_per_request=cpu_per_request)
        self.pod_cpu = pod_cpu
        
    def get_desired_replicas(self, current_load: float, current_replicas: int,
                            history: List[float], predictions: Dict[str, Any]) -> int:
        if current_replicas == 0:
            return 2
        current_cpu = current_load * self.cpu_per_request
        # Divide by actual pod CPU limit size to compute correct utilization
        util = current_cpu / (current_replicas * self.pod_cpu)
        desired = int(np.ceil(current_replicas * util / self.target_cpu_utilization))
        
        if desired < current_replicas:
            if predictions.get('time', 0) - self.last_scale_down < self.scale_down_cooldown:
                return current_replicas
            self.last_scale_down = predictions.get('time', 0)
            
        return max(2, desired)

class DecoupledAAPAAutoscaler(AAPAAutoscaler):
    """AAPA baseline corrected to classify online without oracle and use correct pod capacity (1.5 cores) instead of 0.1."""
    def __init__(self, trace_history: np.ndarray, cpu_per_request: float = 0.01, pod_cpu: float = 1.5):
        super().__init__(model_path=None, encoder_path=None, cpu_per_request=cpu_per_request, true_archetype=None)
        self.trace_history = trace_history
        self.pod_cpu = pod_cpu
        self.pred_archetype, self.pred_confidence = rule_based_classify(self.trace_history[:50])
        
    def classify_workload(self, features: np.ndarray) -> Tuple[str, float]:
        return self.pred_archetype, self.pred_confidence

    def get_desired_replicas(self, current_load: float, current_replicas: int,
                            history: List[float], predictions: Dict[str, Any]) -> int:
        strategy = self.strategies[self.pred_archetype]
        
        if self.pred_archetype == 'SPIKE' and self.pred_confidence < 0.8:
            adjusted_target_util = strategy['target_util'] * 0.8
        else:
            adjusted_target_util = strategy['target_util']
            
        current_cpu = current_load * self.cpu_per_request
        replica_capacity = self.pod_cpu * adjusted_target_util
        
        base_replicas = int(np.ceil(current_cpu / replica_capacity))
        
        if self.pred_archetype == 'SPIKE':
            spike_detected = False
            if len(history) >= 2:
                if current_load > strategy['spike_threshold'] * np.median(history[-10:]):
                    spike_detected = True
            if self.predict_spike(history, strategy):
                spike_detected = True
            if spike_detected:
                base_replicas = int(base_replicas * strategy['burst_multiplier'])
                base_replicas = max(base_replicas, 5)
        elif self.pred_archetype == 'PERIODIC':
            if len(history) >= 60:
                y = np.array(history[-60:])
                peaks = [i for i in range(1, len(y)-1) if y[i] > y[i-1] and y[i] > y[i+1] and y[i] > np.median(y) * 1.5]
                if peaks and len(history) % 20 < 5:
                    base_replicas = int(base_replicas * 1.2)
        elif self.pred_archetype == 'RAMP':
            if len(history) >= 5:
                recent_trend = np.polyfit(range(5), history[-5:], 1)[0]
                if recent_trend > 0.1:
                    future_load = current_load + recent_trend * strategy['predictive_window']
                    future_cpu = future_load * self.cpu_per_request
                    base_replicas = int(np.ceil(future_cpu / replica_capacity))
                    
        desired = base_replicas + strategy['warm_pool_size']
        
        if desired < current_replicas:
            if predictions.get('time', 0) - self.last_scale_down < strategy['scale_down_cooldown']:
                return current_replicas
            self.last_scale_down = predictions.get('time', 0)
            
        return max(2, desired)

def rule_based_classify(trace: np.ndarray) -> Tuple[str, float]:
    """Classify KSWD workloads using extracted feature rules from history."""
    extractor = TimeSeriesFeatureExtractor()
    # Normalize trace history to RPS for consistent classification
    feat_dict = extractor.extract_features(trace / 60.0)
    peak_to_mean = feat_dict.get('max_to_mean_ratio', 0.0)
    autocorr_60 = feat_dict.get('autocorr_lag_60', 0.0)
    variance = feat_dict.get('variance', 0.0)
    
    if peak_to_mean > 10.0:
        return 'SPIKE', 0.95
    elif autocorr_60 > 0.6:
        if variance > 10.0:
            return 'RAMP', 0.90
        else:
            return 'PERIODIC', 0.90
    else:
        return 'STATIONARY_NOISY', 0.85

# --- MagicScaler-Inspired GP Autoscaler (Phase 5 & 8) ---
class MagicScalerAutoscaler:
    def __init__(self, target_cpu_utilization: float = 0.7, cost_weight: float = 0.5):
        self.target_cpu_utilization = target_cpu_utilization
        self.cost_weight = cost_weight
        self.gp = GaussianProcessRegressor(kernel=RBF(length_scale=10.0), optimizer=None, alpha=0.1, random_state=42)
        
    def get_desired_replicas(self, history: List[float]) -> int:
        window_size = 30
        recent_history = history[-window_size:]
        if len(recent_history) < 5:
            return 2
            
        X = np.arange(len(recent_history)).reshape(-1, 1)
        y = np.array(recent_history)
        self.gp.fit(X, y)
        
        next_step = np.array([[len(recent_history)]])
        mean, std = self.gp.predict(next_step, return_std=True)
        mean = max(0.0, float(mean[0]))
        std = max(1e-3, float(std[0]))
        
        best_h = 2
        min_cost = float('inf')
        for h in range(2, 31):
            # capacity of h replicas in RPS: h * 1.5 * target_util / cpu_per_request (0.5)
            capacity = h * 1.5 * self.target_cpu_utilization / 0.5
            prob_violation = 1.0 - norm.cdf(capacity, loc=mean, scale=std)
            cost = h * self.cost_weight + 50.0 * prob_violation
            if cost < min_cost:
                min_cost = cost
                best_h = h
        return best_h

# --- Standalone VPA and Coexistence Simulators (Phase 5 & 8) ---
class VPASimulator(ChimeraSimulator):
    """Standalone VPA baseline: scales only CPU size vertically, fixed replicas."""
    def __init__(self, env, controller, cpu_per_request):
        super().__init__(env, controller, cpu_per_request=cpu_per_request)
        # Fixed replica count for standalone VPA
        self.fixed_replicas = 5
        self.current_cpu_cores = 1.5
        
    def autoscaling_controller(self, features_list = None):
        while True:
            current_load = self.calculate_current_load()
            self.load_history.append(current_load)
            
            desired_total_cpu = (current_load * self.cpu_per_request) / 0.70
            desired_cpu_per_replica = desired_total_cpu / self.fixed_replicas
            desired_cpu_per_replica = max(self.controller.v_min, min(desired_cpu_per_replica, self.controller.v_max))
            
            dv = desired_cpu_per_replica - self.current_cpu_cores
            if abs(dv) >= self.v_thresh and (self.env.now - self.last_vertical_scale_time >= self.vertical_scale_cooldown):
                new_cpu = self.current_cpu_cores + dv
                for pod in self.pods:
                    pod.cpu_cores = new_cpu
                self.current_cpu_cores = new_cpu
                self.metrics.scaling_actions += 1
                self.metrics.vertical_scaling_actions += 1
                self.last_vertical_scale_time = self.env.now
                
            self.metrics.cpu_limit_history.append(self.current_cpu_cores)
            total_active_cpu = sum(p.cpu_cores for p in self.pods)
            self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
            self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
            yield self.env.timeout(self.scale_interval)

    def run(self, workload: List[float], function_id: str, execution_time_dist: Callable, features_list = None, duration = None):
        self.pods = [Pod(id=f"pod-{i}", cpu_cores=self.current_cpu_cores, is_warm=True, warmup_time=0.0) for i in range(self.fixed_replicas)]
        return super().run(workload, function_id, execution_time_dist, features_list, duration)

class HPA_VPA_Coexistence_Simulator(ChimeraSimulator):
    """Coexistence baseline: HPA and VPA loops run simultaneously and uncoordinated (Phase 5)."""
    def __init__(self, env, controller, cpu_per_request):
        super().__init__(env, controller, cpu_per_request=cpu_per_request)
        self.hpa = DecoupledHPAAutoscaler(target_cpu_utilization=0.7, cpu_per_request=cpu_per_request, pod_cpu=1.5)
        self.current_cpu_cores = 1.5
        
    def autoscaling_controller(self, features_list = None):
        while True:
            current_load = self.calculate_current_load()
            current_replicas = len(self.pods)
            self.load_history.append(current_load)
            
            # 1. HPA Loop (replica scaling)
            self.hpa.pod_cpu = self.current_cpu_cores
            predictions = {'time': self.env.now}
            desired_replicas = self.hpa.get_desired_replicas(current_load, current_replicas, self.load_history, predictions)
            desired_replicas = max(self.controller.h_min, min(desired_replicas, self.controller.h_max))
            
            dh = desired_replicas - current_replicas
            if dh > 0:
                for _ in range(dh):
                    self.pods.append(Pod(f"pod-{len(self.pods)}", cpu_cores=self.current_cpu_cores, startup_time=self.horizontal_delay, warmup_time=self.env.now))
                self.metrics.scaling_actions += 1
                self.metrics.horizontal_scaling_actions += 1
            elif dh < 0:
                if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                    self.pods.sort(key=lambda p: p.last_used)
                    to_remove = min(abs(dh), len(self.pods) - self.controller.h_min)
                    if to_remove > 0:
                        self.pods = self.pods[to_remove:]
                        self.metrics.scaling_actions += 1
                        self.metrics.horizontal_scaling_actions += 1
                        self.last_scale_down_time = self.env.now
            
            # 2. VPA Loop (vertical sizing)
            desired_total_cpu = (current_load * self.cpu_per_request) / 0.70
            desired_cpu_per_replica = desired_total_cpu / len(self.pods)
            desired_cpu_per_replica = max(self.controller.v_min, min(desired_cpu_per_replica, self.controller.v_max))
            
            dv = desired_cpu_per_replica - self.current_cpu_cores
            if abs(dv) >= self.v_thresh and (self.env.now - self.last_vertical_scale_time >= self.vertical_scale_cooldown):
                new_cpu = self.current_cpu_cores + dv
                for pod in self.pods:
                    pod.cpu_cores = new_cpu
                self.current_cpu_cores = new_cpu
                self.metrics.scaling_actions += 1
                self.metrics.vertical_scaling_actions += 1
                self.last_vertical_scale_time = self.env.now
                
            self.metrics.cpu_limit_history.append(self.current_cpu_cores)
            total_active_cpu = sum(p.cpu_cores for p in self.pods)
            self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
            self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
            yield self.env.timeout(self.scale_interval)

# --- Workload Inventory & Stress Scenarios (Phase 2, 3, 4, 5, 8) ---
def get_remediation_workloads() -> List[Dict[str, Any]]:
    workloads = []
    workloads_dir = Path("dataset/workloads")
    # FIX: w_0003 was silently excluded with no documented reason. It is a legitimate,
    # non-duplicate real trace (corr with w_0001 = -0.03). Restored -- dropping 1 of 5
    # available real traces from the subset the headline claim rests on reads as
    # selective reporting. See CHIMERA_CODE_VERIFICATION_ADDENDUM.md Section 10e.
    # NOTE: all five files are 100-minute EXCERPTS of 14-day traces (see
    # dataset/EXAMPLE_DATA_NOTE.md). Regenerate the full dataset before submission.
    kswd_files = {
        'real_kswd_w1': 'w_0001.json',
        'real_kswd_w2': 'w_0002.json',
        'real_kswd_w3': 'w_0003.json',
        'real_kswd_w4': 'w_0004.json',
        'real_kswd_w5': 'w_0005.json'
    }
    
    # 4 Real KSWD Traces
    for w_id, filename in kswd_files.items():
        with open(workloads_dir / filename, "r") as f:
            data = json.load(f)
            # scaled requests per minute trace
            trace = np.array(data["request_trace"]["requests_per_minute"]) * 15.0
            workloads.append({
                'id': w_id,
                'trace': trace,
                'type': data.get('archetype', 'UNKNOWN').upper()
            })
            
    # 5. Synthetic Coupled Oscillation (opposing scaling pressures)
    # Oscillation trace designed to force conflict between independent HPA and VPA
    coupled_osc = np.concatenate([
        np.ones(50) * 30.0,
        np.ones(20) * 30.0,
        np.array([120.0, 5.0] * 15)  # alternating high/low loads to cause flapping
    ])
    workloads.append({'id': 'synthetic_coupled_oscillation', 'trace': coupled_osc, 'type': 'PERIODIC'})
    
    # 6. Synthetic Extreme Overload (triggers Lyapunov constraint)
    # Sudden large overload to test stability boundaries
    extreme_overload = np.concatenate([
        np.ones(50) * 15.0,
        np.ones(20) * 15.0,
        np.ones(5) * 15.0,
        np.ones(3) * 600.0,  # Extreme spike (triggers Lyapunov drift limit)
        np.ones(22) * 15.0
    ])
    workloads.append({'id': 'synthetic_extreme_overload', 'trace': extreme_overload, 'type': 'SPIKE'})
    
    # 7. Workload Drift (ADWIN validation)
    # Abrupt mean shift to verify drift response and prediction speed
    drift = np.concatenate([
        np.ones(50) * 15.0,
        np.ones(20) * 15.0,
        np.ones(10) * 15.0,
        np.ones(20) * 90.0  # abrupt shift from 15 to 90
    ])
    workloads.append({'id': 'synthetic_drift', 'trace': drift, 'type': 'STATIONARY_NOISY'})
    
    # 8. Synthetic Stress Uncertainty (CQR Validation)
    # Highly noisy sine trace to inspect conformal intervals
    stress = np.zeros(100)
    np.random.seed(42)
    for t in range(100):
        stress[t] = 40.0 + 20.0 * np.sin(2 * np.pi * t / 20.0) + np.random.normal(0, 15.0)
    stress = np.maximum(5.0, stress)
    workloads.append({'id': 'synthetic_stress_uncertainty', 'trace': stress, 'type': 'PERIODIC'})
    
    return workloads

def train_cqr_for_trace(trace: np.ndarray) -> ConformalQuantileRegressor:
    """Train ConformalQuantileRegressor in RPS units (Phase 1)"""
    trace_rps = trace / 60.0
    extractor = TimeSeriesFeatureExtractor()
    X_train, y_train = [], []
    for t in range(10, 50):
        history = trace_rps[:t]
        feat = np.array(list(extractor.extract_features(history).values()))
        X_train.append(feat)
        y_train.append(trace_rps[t])
        
    X_val, y_val = [], []
    for t in range(50, 70):
        history = trace_rps[:t]
        feat = np.array(list(extractor.extract_features(history).values()))
        X_val.append(feat)
        y_val.append(trace_rps[t])
        
    cqr = ConformalQuantileRegressor(alpha=0.1, calibration_size=20)
    cqr.fit(np.array(X_train), np.array(y_train), np.array(X_val), np.array(y_val))
    return cqr

def run_simulation(trace: np.ndarray, config_name: str, config: dict, w_type: str, cqr_model, seed: int) -> JointMetrics:
    np.random.seed(seed)
    env = simpy.Environment()
    
    controller = ChimeraController(
        v_min=config['v_min'],
        v_max=config['v_max'],
        h_min=config['h_min'],
        h_max=config['h_max'],
        kappa=config.get('kappa', 0.5),
        eta=config.get('eta', 0.2),
        sigma=config.get('sigma', 0.1),
        zeta=config.get('zeta', 0.5),
        use_state_augmentation=config.get('use_state_augmentation', True),
        # FIX: this flag was previously never threaded through from config at all, so
        # CHIMERA_w/o_Anti_Chattering silently ran identically to CHIMERA_Full (see CODE_FIXES_LOG.md).
        use_scale_down_guardrail=config.get('use_scale_down_guardrail', True),
        # NEW: true structural decoupling (no joint capacity check, no joint Lyapunov certificate,
        # no cross-coupling penalty) for the CHIMERA_Decoupled baseline.
        decoupled_safety=config.get('decoupled_safety', False)
    )
    
    cpp = 0.5  # fixed cpu_per_request
    test_trace_rpm = trace[70:100]
    trace_rps = trace / 60.0
    
    # Pre-calculate features list in RPS for simulation steps
    features_list = []
    extractor = TimeSeriesFeatureExtractor()
    for t in range(70, 100):
        feat = np.array(list(extractor.extract_features(trace_rps[:t]).values()))
        features_list.append(feat)
        
    if config_name.startswith('HPA') and config_name != 'HPA+VPA':
        class HPASimulator(ChimeraSimulator):
            def __init__(self, env, controller):
                super().__init__(env, controller, cpu_per_request=cpp, target_utilization=0.70)
                self.scaler = DecoupledHPAAutoscaler(target_cpu_utilization=0.7, cpu_per_request=cpp, pod_cpu=config['v_min'])
                self.current_cpu_cores = config['v_min']
                
            def autoscaling_controller(self, features_list = None):
                while True:
                    current_load = self.calculate_current_load()
                    current_replicas = len(self.pods)
                    self.load_history.append(current_load)
                    predictions = {'time': self.env.now}
                    desired_replicas = self.scaler.get_desired_replicas(
                        current_load, current_replicas, self.load_history, predictions
                    )
                    desired_replicas = max(self.controller.h_min, min(desired_replicas, self.controller.h_max))
                    dh = desired_replicas - current_replicas
                    if dh > 0:
                        for _ in range(dh):
                            self.pods.append(Pod(f"pod-{len(self.pods)}", cpu_cores=self.current_cpu_cores, startup_time=self.horizontal_delay, warmup_time=self.env.now))
                        self.metrics.scaling_actions += 1
                        self.metrics.horizontal_scaling_actions += 1
                    elif dh < 0:
                        if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                            self.pods.sort(key=lambda p: p.last_used)
                            to_remove = min(abs(dh), len(self.pods) - self.controller.h_min)
                            if to_remove > 0:
                                self.pods = self.pods[to_remove:]
                                self.metrics.scaling_actions += 1
                                self.metrics.horizontal_scaling_actions += 1
                                self.last_scale_down_time = self.env.now
                    self.metrics.cpu_limit_history.append(self.current_cpu_cores)
                    total_active_cpu = sum(p.cpu_cores for p in self.pods)
                    self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
                    self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
                    yield self.env.timeout(self.scale_interval)
        sim = HPASimulator(env, controller)
        
    elif config_name.startswith('KOSMOS'):
        sim = KOSMOSSimulator(env, controller, cpu_per_request=cpp,
                              DC=config.get('DC', 0.35),
                              BC=config.get('BC', 0.06),
                              alpha=config.get('alpha', 0.85),
                              util_low=config.get('util_low', 0.35),
                              use_inplace_resize=config.get('use_inplace_resize', True),
                              target_utilization=0.70)

    elif config_name == 'VPA':
        sim = VPASimulator(env, controller, cpu_per_request=cpp)
        
    elif config_name == 'HPA+VPA':
        sim = HPA_VPA_Coexistence_Simulator(env, controller, cpu_per_request=cpp)
        
    elif config_name.startswith('AAPA'):
        class AAPASimulator(ChimeraSimulator):
            def __init__(self, env, controller):
                super().__init__(env, controller, cpu_per_request=cpp, target_utilization=0.70)
                self.scaler = DecoupledAAPAAutoscaler(trace, cpu_per_request=cpp, pod_cpu=config['v_min'])
                self.current_cpu_cores = config['v_min']
                
            def autoscaling_controller(self, features_list = None):
                while True:
                    current_load = self.calculate_current_load()
                    current_replicas = len(self.pods)
                    self.load_history.append(current_load)
                    predictions = {'time': self.env.now}
                    desired_replicas = self.scaler.get_desired_replicas(
                        current_load, current_replicas, self.load_history, predictions
                    )
                    desired_replicas = max(self.controller.h_min, min(desired_replicas, self.controller.h_max))
                    dh = desired_replicas - current_replicas
                    if dh > 0:
                        for _ in range(dh):
                            self.pods.append(Pod(f"pod-{len(self.pods)}", cpu_cores=self.current_cpu_cores, startup_time=self.horizontal_delay, warmup_time=self.env.now))
                        self.metrics.scaling_actions += 1
                        self.metrics.horizontal_scaling_actions += 1
                    elif dh < 0:
                        if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                            self.pods.sort(key=lambda p: p.last_used)
                            to_remove = min(abs(dh), len(self.pods) - self.controller.h_min)
                            if to_remove > 0:
                                self.pods = self.pods[to_remove:]
                                self.metrics.scaling_actions += 1
                                self.metrics.horizontal_scaling_actions += 1
                                self.last_scale_down_time = self.env.now
                    self.metrics.cpu_limit_history.append(self.current_cpu_cores)
                    total_active_cpu = sum(p.cpu_cores for p in self.pods)
                    self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
                    self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
                    yield self.env.timeout(self.scale_interval)
        sim = AAPASimulator(env, controller)
        
    elif config_name.startswith('MagicScaler-Inspired'):
        class MagicScalerSimulator(ChimeraSimulator):
            def __init__(self, env, controller):
                super().__init__(env, controller, cpu_per_request=cpp, target_utilization=0.70)
                self.magic_scaler = MagicScalerAutoscaler()
                self.current_cpu_cores = config['v_min']
                
            def autoscaling_controller(self, features_list = None):
                while True:
                    current_load = self.calculate_current_load()
                    current_replicas = len(self.pods)
                    self.load_history.append(current_load)
                    desired_replicas = self.magic_scaler.get_desired_replicas(self.load_history)
                    desired_replicas = max(self.controller.h_min, min(desired_replicas, self.controller.h_max))
                    dh = desired_replicas - current_replicas
                    if dh > 0:
                        for _ in range(dh):
                            self.pods.append(Pod(f"pod-{len(self.pods)}", cpu_cores=self.current_cpu_cores, startup_time=self.horizontal_delay, warmup_time=self.env.now))
                        self.metrics.scaling_actions += 1
                        self.metrics.horizontal_scaling_actions += 1
                    elif dh < 0:
                        if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                            self.pods.sort(key=lambda p: p.last_used)
                            to_remove = min(abs(dh), len(self.pods) - self.controller.h_min)
                            if to_remove > 0:
                                self.pods = self.pods[to_remove:]
                                self.metrics.scaling_actions += 1
                                self.metrics.horizontal_scaling_actions += 1
                                self.last_scale_down_time = self.env.now
                    self.metrics.cpu_limit_history.append(self.current_cpu_cores)
                    total_active_cpu = sum(p.cpu_cores for p in self.pods)
                    self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
                    self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
                    yield self.env.timeout(self.scale_interval)
        sim = MagicScalerSimulator(env, controller)
        
    else:
        # CHIMERA and Ablation models
        cqr = cqr_model if config.get('use_cqr', False) else None
        drift = AdwinDriftDetector(delta=0.05) if config.get('use_drift', False) else None
        
        sim = ChimeraSimulator(
            env,
            controller,
            cqr_regressor=cqr,
            drift_detector=drift,
            slo_threshold=0.5,
            use_inplace_resize=config.get('use_inplace_resize', True),
            cpu_per_request=cpp,
            target_utilization=0.70,  # FIX: was 0.60, asymmetric vs. baselines' 0.70 (see CODE_FIXES_LOG.md)
            v_thresh=config.get('v_thresh', 0.05)  # FIX: was hardcoded 0.15, which silently absorbed the CLF filter's
                                                    # corrections before they could reach actuation (see CODE_FIXES_LOG.md)
        )
        # FIX: was hardcoded to 15.0 (== scale_interval), which trivially satisfied the cooldown check every
        # single loop tick and so silently disabled the documented 60s vertical cooldown. Restored to spec.
        sim.vertical_scale_cooldown = config.get('vertical_scale_cooldown', 60.0)
        sim.use_ood_fallback = config.get('use_ood_fallback', True)
        sim.decoupled_safety = config.get('decoupled_safety', False)  # NEW: true MIMO decoupling for CHIMERA_Decoupled
        
    exec_dist = lambda: np.random.exponential(0.1)
    # Prime load history in RPS
    sim.load_history = list(trace_rps[50:70])
    
    metrics = sim.run(test_trace_rpm, f"{config_name.lower()}-func", exec_dist, features_list=features_list)
    return metrics

def main():
    logger.info("Starting CHIMERA Remediated Simulation Harness...")
    workloads = get_remediation_workloads()
    
    # Baseline Configurations (Phase 4 & 8)
    configs = {
        'HPA': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 0.5, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': False
        },
        'HPA_1.5': {
            'run_type': 'baseline', 'v_min': 1.5, 'v_max': 1.5, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': False
        },
        'VPA': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 5, 'h_max': 5,
            'use_inplace_resize': True
        },
        'HPA+VPA': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': True
        },
        'AAPA': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 0.5, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': False
        },
        'AAPA_1.5': {
            'run_type': 'baseline', 'v_min': 1.5, 'v_max': 1.5, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': False
        },
        'MagicScaler-Inspired': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 0.5, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': False
        },
        'MagicScaler-Inspired_1.5': {
            'run_type': 'baseline', 'v_min': 1.5, 'v_max': 1.5, 'h_min': 2, 'h_max': 30,
            'use_inplace_resize': False
        },
        'Joint-LQR-Lyapunov (Unconstrained)': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 100.0, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        'CHIMERA_Original': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': False, 'use_scale_down_guardrail': False
        },
        'CHIMERA': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        # Ablation Variants (Phase 9)
        'CHIMERA_Full': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        'CHIMERA_w/o_Lyapunov': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 100.0, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        'CHIMERA_w/o_Coupling_Penalty': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.0, 'eta': 0.0, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        'CHIMERA_w/o_CQR': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': False, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        'CHIMERA_w/o_ADWIN_Drift': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': False,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        'CHIMERA_w/o_OOD_Fallback': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True,
            'use_ood_fallback': False
        },
        'CHIMERA_w/o_State_Augmentation': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': False, 'use_scale_down_guardrail': True
        },
        'CHIMERA_w/o_Anti_Chattering': {
            'run_type': 'ablation', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': False
        },
        'CHIMERA_w/o_Joint_H_V': {
            'run_type': 'ablation', 'v_min': 1.5, 'v_max': 1.5, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': False, 'use_state_augmentation': True, 'use_scale_down_guardrail': True
        },
        # NEW: genuine decoupled-coordination baseline (KOSMOS-style). Same actuators, same
        # delay-augmented LQR gains, same CQR/ADWIN/OOD-fallback pipeline as CHIMERA -- the ONLY
        # difference is that the safety/coordination layer never lets the two axes see each other:
        # no joint capacity constraint, no joint Lyapunov certificate, no cross-coupling penalty.
        # This isolates whether *coordination itself* helps, independent of "having two actuators".
        'CHIMERA_Decoupled': {
            'run_type': 'baseline', 'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30,
            'sigma': 0.1, 'zeta': 0.5, 'kappa': 0.5, 'eta': 0.2, 'use_cqr': True, 'use_drift': True,
            'use_inplace_resize': True, 'use_state_augmentation': True, 'use_scale_down_guardrail': True,
            'decoupled_safety': True
        }
    }
    
    seeds = [42, 123, 456, 789, 1024, 2048, 4096, 8192, 16384, 32768]
    results = []
    
    cqr_models = {}
    for item in workloads:
        w_id = item['id']
        trace = item['trace']
        logger.info(f"Training aligned CQR model for trace {w_id}...")
        cqr_models[w_id] = train_cqr_for_trace(trace)
        
    for item in workloads:
        w_id = item['id']
        trace = item['trace']
        w_type = item['type']
        
        for config_name, config in configs.items():
            for seed in seeds:
                logger.info(f"Running config '{config_name}' on trace '{w_id}' with seed {seed}...")
                metrics = run_simulation(trace, config_name, config, w_type, cqr_models[w_id], seed)
                
                cqr_width = np.mean(metrics.cqr_widths) if metrics.cqr_widths else 0.0
                cqr_coverage = np.mean(metrics.cqr_coverages) if metrics.cqr_coverages else 0.0
                
                results.append({
                    'seed': seed,
                    'run_type': config['run_type'],
                    'config_name': config_name,
                    'workload_id': w_id,
                    'workload_type': w_type,
                    'slo_violation_rate': metrics.get_slo_violation_rate(),
                    'mean_response_time': metrics.get_mean_response_time(),
                    'p95_response_time': np.percentile(metrics.response_times, 95) if metrics.response_times else 0.0,
                    'p99_response_time': metrics.get_p99_response_time(),
                    'pod_minutes': metrics.pod_minutes,
                    'scaling_actions': metrics.scaling_actions,
                    'vertical_scaling_actions': metrics.vertical_scaling_actions,
                    'horizontal_scaling_actions': metrics.horizontal_scaling_actions,
                    'coupling_penalty': metrics.get_coupling_penalty_rate(),
                    'rei': calculate_extended_rei(metrics),
                    
                    # Observability Metrics (Phases 1, 2, 3, 4)
                    'lyapunov_filter_activation': metrics.lyapunov_activations,
                    'drift_detector_activation': metrics.drift_activations,
                    'coupling_penalty_activation': metrics.coupling_activations,
                    'CQR_interval_width': cqr_width,
                    'CQR_empirical_coverage': cqr_coverage,
                    'replica_oscillation': metrics.replica_oscillations
                })
                
    df = pd.DataFrame(results)
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    
    # Save the absolute single source of truth results.csv
    results_csv = results_dir / "results.csv"
    df.to_csv(results_csv, index=False)
    
    # Save seed-average compatibility files
    df_avg = df.groupby(['run_type', 'config_name', 'workload_id', 'workload_type']).mean().reset_index()
    df_avg[df_avg['run_type'] == 'baseline'].to_csv(results_dir / "simulation_results.csv", index=False)
    df_avg[df_avg['run_type'] == 'ablation'].to_csv(results_dir / "ablation_results.csv", index=False)
    
    logger.info(f"Evaluation completed. Master results saved to {results_csv}")
    
if __name__ == "__main__":
    main()
