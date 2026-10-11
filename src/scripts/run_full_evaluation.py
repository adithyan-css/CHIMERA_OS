"""
CHIMERA evaluation harness.

Runs every controller over the same nine workloads and the same seeds, under identical
resource bounds and the same target utilisation, and writes one row per run.

    python src/scripts/run_full_evaluation.py                      # main comparison + ablation
    python src/scripts/run_full_evaluation.py --sweep              # + cost/SLO frontier sweep
    python src/scripts/run_full_evaluation.py --seeds 42 123 --workers 4 --out results/x.csv

Output defaults to results/results_v2.csv. It never touches
results/results_FINAL_verified.csv (the Review-2 dataset).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import warnings
warnings.filterwarnings('ignore')

import argparse
import copy
import time
import numpy as np
import pandas as pd
from pathlib import Path
import logging
import simpy
import json
from multiprocessing import Pool
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

CPU_PER_REQUEST = 0.5   # cores consumed per request/second
SLO_SECONDS = 0.5
DEFAULT_SEEDS = [42, 123, 456, 789, 1024]

# --- Fair Baseline Autoscalers with pod size scaling corrections (Phase 8) ---

class DecoupledHPAAutoscaler(HPAAutoscaler):
    """HPA baseline corrected to utilize actual pod CPU limit instead of 1.0."""
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
    """AAPA baseline corrected to classify online without oracle and use correct pod capacity."""
    def __init__(self, trace_history: np.ndarray, cpu_per_request: float = 0.01, pod_cpu: float = 1.5,
                 util_scale: float = 1.0):
        super().__init__(model_path=None, encoder_path=None, cpu_per_request=cpu_per_request, true_archetype=None)
        self.trace_history = trace_history
        self.pod_cpu = pod_cpu
        # For the cost/SLO frontier sweep: every archetype target is scaled by the same
        # factor (1.0 = AAPA's published targets), capped below saturation.
        for strat in self.strategies.values():
            strat['target_util'] = min(0.95, strat['target_util'] * util_scale)
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
    """GP forecast + cost/violation-probability trade-off over replica counts.

    FIX: capacity used to be hard-coded as h * 1.5 cores, while the baseline actually
    runs 0.5-core pods. It therefore believed every replica had 3x the capacity it did
    and chronically under-provisioned (74% violations). Capacity now uses the real pod
    size, so the baseline is compared at its intended behaviour, not a strawman.
    """
    def __init__(self, target_cpu_utilization: float = 0.7, cost_weight: float = 0.5,
                 pod_cpu: float = 1.5, cpu_per_request: float = 0.5):
        self.target_cpu_utilization = target_cpu_utilization
        self.cost_weight = cost_weight
        self.pod_cpu = pod_cpu
        self.cpu_per_request = cpu_per_request
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
            # capacity of h replicas in RPS at the target utilisation
            capacity = h * self.pod_cpu * self.target_cpu_utilization / self.cpu_per_request
            prob_violation = 1.0 - norm.cdf(capacity, loc=mean, scale=std)
            cost = h * self.cost_weight + 50.0 * prob_violation
            if cost < min_cost:
                min_cost = cost
                best_h = h
        return best_h

# --- Standalone VPA and Coexistence Simulators (Phase 5 & 8) ---
class VPASimulator(ChimeraSimulator):
    """Standalone VPA baseline: scales only CPU size vertically, fixed replicas (h_min = h_max)."""
    def __init__(self, env, controller, cpu_per_request, target_utilization=0.70):
        super().__init__(env, controller, cpu_per_request=cpu_per_request,
                         target_utilization=target_utilization)
        self.fixed_replicas = controller.h_min

    def autoscaling_controller(self, features_list = None):
        while True:
            current_load = self.calculate_current_load()
            self.load_history.append(current_load)

            desired_total_cpu = (current_load * self.cpu_per_request) / self.target_utilization
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

class HPA_VPA_Coexistence_Simulator(ChimeraSimulator):
    """Coexistence baseline: stock HPA and stock VPA on the same CPU signal, uncoordinated.

    This is the configuration the Kubernetes docs warn against. Each loop sees only its
    own actuator:
      * HPA -- desired = ceil(replicas * util / target), util against current pod size.
      * VPA -- per-pod request = observed usage / target, spread over the pods that are
               actually serving (VPA's recommender only sees usage from running containers).
    FIX: the previous version started at 1.5 cores/pod and spread usage over all pods,
    including ones still booting, so its vertical loop never fired and the row reduced to
    plain HPA. It now starts from the same v_min / h_min as every other controller.
    """
    def __init__(self, env, controller, cpu_per_request, target_utilization=0.70):
        super().__init__(env, controller, cpu_per_request=cpu_per_request,
                         target_utilization=target_utilization)
        self.hpa = DecoupledHPAAutoscaler(target_cpu_utilization=target_utilization,
                                          cpu_per_request=cpu_per_request, pod_cpu=controller.v_min)

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

            # 2. VPA Loop (vertical sizing), blind to the replicas HPA just ordered
            serving = max(1, sum(1 for p in self.pods if p.is_warm))
            desired_total_cpu = (current_load * self.cpu_per_request) / self.target_utilization
            desired_cpu_per_replica = desired_total_cpu / serving
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

class ReplicaOnlySimulator(ChimeraSimulator):
    """Shared actuation loop for the horizontal-only baselines (HPA, AAPA, MagicScaler).

    `decide(sim, current_load, current_replicas)` returns the desired replica count; pod
    size stays fixed at v_min. Replaces three copy-pasted per-baseline classes whose
    loops were identical apart from that one call.
    """
    def __init__(self, env, controller, cpu_per_request, decide, target_utilization=0.70):
        super().__init__(env, controller, cpu_per_request=cpu_per_request,
                         target_utilization=target_utilization)
        self.decide = decide

    def autoscaling_controller(self, features_list = None):
        while True:
            current_load = self.calculate_current_load()
            current_replicas = len(self.pods)
            self.load_history.append(current_load)
            desired_replicas = self.decide(self, current_load, current_replicas)
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
            self.timeline.append((self.env.now, len(self.pods), self.current_cpu_cores, []))
            yield self.env.timeout(self.scale_interval)

# --- Workload Inventory & Stress Scenarios (Phase 2, 3, 4, 5, 8) ---
def get_remediation_workloads() -> List[Dict[str, Any]]:
    workloads = []
    workloads_dir = Path("dataset/workloads")
    # FIX: w_0003 was silently excluded with no documented reason. It is a legitimate,
    # non-duplicate real trace (corr with w_0001 = -0.03). Restored -- dropping 1 of 5
    # available real traces from the subset the headline claim rests on reads as
    # selective reporting. See CODE_FIXES_LOG.md item 8.
    # NOTE: all five files are 100-minute EXCERPTS of 14-day traces (see
    # dataset/EXAMPLE_DATA_NOTE.md). Regenerate the full dataset before submission.
    kswd_files = {
        'real_kswd_w1': 'w_0001.json',
        'real_kswd_w2': 'w_0002.json',
        'real_kswd_w3': 'w_0003.json',
        'real_kswd_w4': 'w_0004.json',
        'real_kswd_w5': 'w_0005.json'
    }

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

    # Synthetic Coupled Oscillation (opposing scaling pressures)
    coupled_osc = np.concatenate([
        np.ones(50) * 30.0,
        np.ones(20) * 30.0,
        np.array([120.0, 5.0] * 15)  # alternating high/low loads to cause flapping
    ])
    workloads.append({'id': 'synthetic_coupled_oscillation', 'trace': coupled_osc, 'type': 'PERIODIC'})

    # Synthetic Extreme Overload (triggers Lyapunov constraint)
    extreme_overload = np.concatenate([
        np.ones(50) * 15.0,
        np.ones(20) * 15.0,
        np.ones(5) * 15.0,
        np.ones(3) * 600.0,
        np.ones(22) * 15.0
    ])
    workloads.append({'id': 'synthetic_extreme_overload', 'trace': extreme_overload, 'type': 'SPIKE'})

    # Workload Drift (ADWIN validation)
    drift = np.concatenate([
        np.ones(50) * 15.0,
        np.ones(20) * 15.0,
        np.ones(10) * 15.0,
        np.ones(20) * 90.0  # abrupt shift from 15 to 90
    ])
    workloads.append({'id': 'synthetic_drift', 'trace': drift, 'type': 'STATIONARY_NOISY'})

    # Synthetic Stress Uncertainty (CQR Validation)
    stress = np.zeros(100)
    rng = np.random.RandomState(42)
    for t in range(100):
        stress[t] = 40.0 + 20.0 * np.sin(2 * np.pi * t / 20.0) + rng.normal(0, 15.0)
    stress = np.maximum(5.0, stress)
    workloads.append({'id': 'synthetic_stress_uncertainty', 'trace': stress, 'type': 'PERIODIC'})

    return workloads

# Data splits (minutes of each 100-minute trace). "test" is the protocol every reported
# number uses and is unchanged. "dev" scores an EARLIER, disjoint window (40-70) with the
# forecaster trained only on minutes before it; design decisions are made on dev (with
# dev-only synthetic traces and dev-only seeds) so the test window is never tuned on.
SPLITS = {
    'test': dict(train=(10, 50), cal=(50, 70), prime=(50, 70), window=(70, 100), classify=50),
    'dev':  dict(train=(10, 30), cal=(30, 40), prime=(20, 40), window=(40, 70), classify=40),
}
DEV_SEEDS = [7, 11, 13, 17, 19]


def get_dev_workloads() -> List[Dict[str, Any]]:
    """Real traces (scored on the dev window) plus synthetic shapes that do NOT appear in
    the test set. Never used for reported results."""
    real = [w for w in get_remediation_workloads() if w['id'].startswith('real')]
    rng = np.random.RandomState(2024)
    t = np.arange(100)
    spikes = np.full(100, 20.0)
    for s0 in rng.choice(np.arange(5, 95), 9, replace=False):
        spikes[s0:s0 + rng.randint(1, 4)] = rng.uniform(150, 400)
    square = np.where((t // rng.randint(6, 11)) % 2 == 0, 25.0, 140.0)
    ramp_down = np.clip(300.0 - 2.6 * t + rng.normal(0, 12, 100), 10, None)
    heavy = np.clip(40.0 * rng.pareto(2.5, 100) + 15.0, 5, 600)
    dev = [('dev_random_spikes', spikes, 'SPIKE'), ('dev_square_wave', square, 'PERIODIC'),
           ('dev_ramp_down', ramp_down, 'RAMP'), ('dev_heavy_tail', heavy, 'STATIONARY_NOISY')]
    return real + [{'id': i, 'trace': tr, 'type': ty} for i, tr, ty in dev]


# Held-out final evaluation (declared 1 Oct 2026, before any v5 result existed). The five
# real traces have been scored three times by now, so the v5 verdict adds four synthetic
# shapes that appear in neither the dev nor the earlier test set, generated with their own
# seed, and ten seeds never used anywhere. Never used for design.
HOLDOUT_SEEDS = [101, 202, 303, 404, 505, 606, 707, 808, 909, 1010]


def get_holdout_workloads() -> List[Dict[str, Any]]:
    rng = np.random.RandomState(7777)
    t = np.arange(100)
    flash = np.full(100, 30.0)
    flash[80:82] = [180.0, 400.0]
    flash[82:] = 30.0 + 370.0 * 0.5 ** ((t[82:] - 81) / 4.0)
    flash[:80] += rng.normal(0, 4, 80)
    diurnal = 60.0 + 40.0 * np.sin(2 * np.pi * t / 30.0) + rng.normal(0, 8, 100)
    step_down = np.where(t < 82, 250.0, 40.0) + rng.normal(0, 10, 100)
    on_off = np.where((t // 5) % 2 == 0, 200.0, 20.0) + rng.normal(0, 15, 100)
    shapes = [('holdout_flash_crowd', flash, 'SPIKE'), ('holdout_diurnal', diurnal, 'PERIODIC'),
              ('holdout_step_down', step_down, 'RAMP'), ('holdout_on_off', on_off, 'PERIODIC')]
    real = [w for w in get_remediation_workloads() if w['id'].startswith('real')]
    return real + [{'id': i, 'trace': np.clip(tr, 5.0, None), 'type': ty} for i, tr, ty in shapes]


class OracleSimulator(ChimeraSimulator):
    """Minute-level clairvoyant reference, not a controller one could deploy: at every
    minute boundary it sets capacity to exactly the coming minute's true average demand /
    target utilisation, instantly, with no boot delay (all pods warm). It is the best
    PER-MINUTE plan. It is not a lower bound: within a minute, arrivals are Poisson and the
    service model reacts to bursts in a 15 s window, so a controller that also tracks
    within-minute bursts (CHIMERA's 5 s inner loop) can land below this line."""
    def __init__(self, env, controller, cpu_per_request, rates_rpm, target_utilization=0.70):
        super().__init__(env, controller, cpu_per_request=cpu_per_request,
                         target_utilization=target_utilization)
        self.rates_rpm = list(rates_rpm)

    def autoscaling_controller(self, features_list=None):
        c = self.controller
        for rpm in self.rates_rpm:
            cores = max(c.h_min * c.v_min, rpm / 60.0 * self.cpu_per_request / self.target_utilization)
            h = max(c.h_min, int(np.ceil(cores / c.v_max - 1e-9)))
            v = cores / h
            self.pods = [Pod(f"pod-{i}", cpu_cores=v, is_warm=True, warmup_time=0.0) for i in range(h)]
            self.current_cpu_cores = v
            self.metrics.total_cpu_allocated += cores
            self.metrics.pod_minutes += h
            self.timeline.append((self.env.now, h, v, []))
            yield self.env.timeout(60.0)


def train_cqr_for_trace(trace: np.ndarray, split: str = 'test') -> ConformalQuantileRegressor:
    """Train ConformalQuantileRegressor in RPS units on the split's train window, calibrate
    on its calibration window (test: 10-50 / 50-70)."""
    sp = SPLITS[split]
    trace_rps = trace / 60.0
    extractor = TimeSeriesFeatureExtractor()
    X_train, y_train = [], []
    for t in range(*sp['train']):
        history = trace_rps[:t]
        feat = np.array(list(extractor.extract_features(history).values()))
        X_train.append(feat)
        y_train.append(trace_rps[t])

    X_val, y_val = [], []
    for t in range(*sp['cal']):
        history = trace_rps[:t]
        feat = np.array(list(extractor.extract_features(history).values()))
        X_val.append(feat)
        y_val.append(trace_rps[t])

    cqr = ConformalQuantileRegressor(alpha=0.1, calibration_size=20)
    cqr.fit(np.array(X_train), np.array(y_train), np.array(X_val), np.array(y_val))
    return cqr

def _private_cqr(cqr_model):
    """Per-run copy of the CQR model. The boosters are read-only and shared; the
    calibration buffer is mutable (online conformal updates, ADWIN flushes) and used to
    leak from one run into the next when the same object was reused."""
    if cqr_model is None:
        return None
    c = copy.copy(cqr_model)
    c.calibration_scores = list(cqr_model.calibration_scores)
    return c

# Service-time distributions, all with mean 0.1 s of CPU. 'exponential' is the reported
# protocol; the others are for robustness runs under a model CHIMERA does not assume.
SERVICE_DISTS = {
    'exponential': lambda: np.random.exponential(0.1),
    'lognormal':   lambda: np.random.lognormal(np.log(0.1) - 0.5, 1.0),    # heavier tail
    'gamma4':      lambda: np.random.gamma(4.0, 0.025),                    # lighter tail
    'bimodal':     lambda: 0.0611 if np.random.random() < 0.9 else 0.45,   # 10 % slow requests
}


def run_simulation(trace: np.ndarray, config_name: str, config: dict, w_type: str, cqr_model, seed: int,
                   return_sim: bool = False, split: str = 'test', warmup: int = 0):
    """One run. Returns JointMetrics, or (metrics, simulator) with return_sim=True.

    warmup: minutes simulated before the scored window and not scored, so every controller
    enters the window in its own steady state instead of cold from h_min (0 = the v2-v4
    protocol, which starts cold at the window)."""
    sp = SPLITS[split]
    w0, w1 = sp['window']
    start = w0 - warmup
    np.random.seed(seed)
    env = simpy.Environment()
    kind = config['kind']
    target_util = config.get('target_utilization', 0.70)

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
        decoupled_safety=config.get('decoupled_safety', False),
        coupling_mode=config.get('coupling_mode', 'opposing'),
        legacy_delay_model=config.get('legacy_delay_model', False)
    )

    cpp = CPU_PER_REQUEST
    test_trace_rpm = trace[start:w1]
    trace_rps = trace / 60.0

    # Pre-calculate features list in RPS for simulation steps (features for minute t use
    # only minutes < t, so nothing is forecast from data it has already seen)
    features_list = []
    extractor = TimeSeriesFeatureExtractor()
    for t in range(start, w1):
        feat = np.array(list(extractor.extract_features(trace_rps[:t]).values()))
        features_list.append(feat)

    if kind == 'hpa':
        scaler = DecoupledHPAAutoscaler(target_cpu_utilization=target_util, cpu_per_request=cpp, pod_cpu=config['v_min'])
        decide = lambda sim, load, n: scaler.get_desired_replicas(load, n, sim.load_history, {'time': sim.env.now})
        sim = ReplicaOnlySimulator(env, controller, cpp, decide, target_utilization=target_util)

    elif kind == 'aapa':
        scaler = DecoupledAAPAAutoscaler(trace[:sp['classify']], cpu_per_request=cpp, pod_cpu=config['v_min'],
                                         util_scale=target_util / 0.70)
        decide = lambda sim, load, n: scaler.get_desired_replicas(load, n, sim.load_history, {'time': sim.env.now})
        sim = ReplicaOnlySimulator(env, controller, cpp, decide, target_utilization=target_util)

    elif kind == 'magic':
        scaler = MagicScalerAutoscaler(target_cpu_utilization=target_util, pod_cpu=config['v_min'], cpu_per_request=cpp)
        decide = lambda sim, load, n: scaler.get_desired_replicas(sim.load_history)
        sim = ReplicaOnlySimulator(env, controller, cpp, decide, target_utilization=target_util)

    elif kind == 'kosmos':
        sim = KOSMOSSimulator(env, controller, cpu_per_request=cpp,
                              DC=config.get('DC', 0.8),
                              BC=config.get('BC', 0.15),
                              alpha=config.get('alpha', 0.70),
                              util_low=config.get('util_low', 0.35),
                              use_inplace_resize=config.get('use_inplace_resize', True),
                              target_utilization=target_util)

    elif kind == 'vpa':
        sim = VPASimulator(env, controller, cpu_per_request=cpp, target_utilization=target_util)

    elif kind == 'hpa_vpa':
        sim = HPA_VPA_Coexistence_Simulator(env, controller, cpu_per_request=cpp, target_utilization=target_util)

    elif kind == 'chimera':
        cqr = _private_cqr(cqr_model) if config.get('use_cqr', False) else None
        drift = AdwinDriftDetector(delta=0.05) if config.get('use_drift', False) else None

        sim = ChimeraSimulator(
            env,
            controller,
            cqr_regressor=cqr,
            drift_detector=drift,
            slo_threshold=SLO_SECONDS,
            use_inplace_resize=config.get('use_inplace_resize', True),
            cpu_per_request=cpp,
            target_utilization=target_util,  # FIX: was 0.60, asymmetric vs. baselines' 0.70 (see CODE_FIXES_LOG.md)
            v_thresh=config.get('v_thresh', 0.05),  # FIX: was hardcoded 0.15 (see CODE_FIXES_LOG.md)
            vertical_bridging=config.get('vertical_bridging', False),
            asymmetric_smoothing=config.get('asymmetric_smoothing', False),
            online_conformal=config.get('online_conformal', False),
            naive_conformal_fallback=config.get('naive_conformal_fallback', False),
            serving_replica_state=config.get('serving_replica_state', False),
            inner_vertical_period=config.get('inner_vertical_period', 0.0),
            inner_release_band=config.get('inner_release_band', 0.0),
            inner_up_band=config.get('inner_up_band', 0.0),
            inner_release_ticks=config.get('inner_release_ticks', 1),
            inner_release_headroom=config.get('inner_release_headroom', 0.0),
            inner_owns_vertical=config.get('inner_owns_vertical', False),
            gated_estimator=config.get('gated_estimator', False),
            replica_band=config.get('replica_band', 0.0),
            one_sided_margin=config.get('one_sided_margin', False),
            adwin_margin_reset=config.get('adwin_margin_reset', False),
            risk_sizing=config.get('risk_sizing', False),
            inner_min_dwell=config.get('inner_min_dwell', 0.0)
        )
        # FIX: was hardcoded to 15.0 (== scale_interval), which silently disabled the 60s cooldown.
        sim.vertical_scale_cooldown = config.get('vertical_scale_cooldown', 60.0)
        sim.use_ood_fallback = config.get('use_ood_fallback', True)
        sim.decoupled_safety = config.get('decoupled_safety', False)
    elif kind == 'oracle':
        sim = OracleSimulator(env, controller, cpp, test_trace_rpm, target_utilization=target_util)
    else:
        raise ValueError(f"unknown controller kind {kind!r} for {config_name}")

    exec_dist = SERVICE_DISTS[_WORKER_STATE.get('service_dist', 'exponential')]
    sim.slowdown_model = _WORKER_STATE.get('slowdown_model', 'inverse')
    # Prime load history in RPS
    if warmup:
        sim.load_history = list(trace_rps[max(0, start - 20):start])
        sim.score_from = warmup * 60.0
    else:
        sim.load_history = list(trace_rps[sp['prime'][0]:sp['prime'][1]])

    metrics = sim.run(test_trace_rpm, f"{config_name.lower()}-func", exec_dist, features_list=features_list)
    return (metrics, sim) if return_sim else metrics

# --- Configurations ------------------------------------------------------------------

BOUNDS_JOINT = {'v_min': 0.5, 'v_max': 4.0, 'h_min': 2, 'h_max': 30}
BOUNDS_H_ONLY = {'v_min': 0.5, 'v_max': 0.5, 'h_min': 2, 'h_max': 30}

# CHIMERA v1 = the controller presented at Review 2 (legacy coupling term, symmetric
# smoothing, CPU target sized for replicas that have not booted yet).
CHIMERA_V1 = dict(kind='chimera', **BOUNDS_JOINT,
                  sigma=0.1, zeta=0.5, kappa=0.5, eta=0.2, use_cqr=True, use_drift=True,
                  use_inplace_resize=True, use_state_augmentation=True, use_scale_down_guardrail=True,
                  coupling_mode='absolute', legacy_delay_model=True)

# CHIMERA (v2): v1 + opposing-only coupling + residual vertical bridging +
# fast-attack/slow-release target smoothing + online conformal recalibration +
# a conformal margin on the reactive fallback + serving-replica state (in-flight pods
# counted once, in the delay buffer, instead of twice). (The simulator also enforces a
# capacity-consistent target split for every CHIMERA variant, v1 included.)
CHIMERA_V2 = dict(CHIMERA_V1, coupling_mode='opposing', vertical_bridging=True,
                  asymmetric_smoothing=True, online_conformal=True,
                  naive_conformal_fallback=True, serving_replica_state=True,
                  legacy_delay_model=False)

# CHIMERA (v3): v2 + cascade control. A 5 s inner loop drives only the 0.1 s vertical
# actuator (fast attack on significant surges, fast release once the need falls 15 %
# below the allocation for two ticks); the 15 s outer loop keeps the joint LQR, replicas
# and the in-flight buffer. Chosen on the dev split (src/scripts/run_full_evaluation.py
# --split dev), never on the test window.
CHIMERA_V3 = dict(CHIMERA_V2, inner_vertical_period=5.0, inner_release_band=0.15,
                  inner_release_ticks=2)

# CHIMERA (v4): v3 made a proper cascade, plus every remaining component either given a job
# it measurably does or removed. All choices made on the dev split (CODE_FIXES_LOG.md, v4):
#   inner_owns_vertical  -- only the inner loop moves CPU (v3's two loops both resized it)
#   replica_band 0.85    -- move replicas only when vertical resizing cannot absorb the change
#   adwin_margin_reset   -- on detected drift, the reactive margin re-learns the new regime
#   online_conformal off -- measured to cost efficiency on dev
#   sigma 100            -- Lyapunov runtime filter off: V = e'Pe from the Riccati solution
#                           already decreases under the LQR law, so the filter never binds
#                           (0.06 activations per run in v3); stability is certified by P > 0.
CHIMERA_V4 = dict(CHIMERA_V3, inner_owns_vertical=True, replica_band=0.85, adwin_margin_reset=True,
                  online_conformal=False, sigma=100.0)

# CHIMERA (v5): v4 + risk-optimal sizing (capacity balances expected SLO violations under
# the queueing model against a price per core, over conformal demand scenarios) + a 10 %
# relative dead-band on inner-loop scale-ups. Its knob is the price, expressed as the
# utilisation it would pick for a CERTAIN load; the default 0.5 was chosen on the dev split
# (cheapest setting whose dev SLO is no worse than v4's default). All choices on dev.
CHIMERA_V5 = dict(CHIMERA_V4, risk_sizing=True, inner_up_band=0.10, target_utilization=0.5)


KOSMOS_GAINS = dict(DC=0.03, BC=0.15)


def build_configs() -> Dict[str, dict]:
    B = lambda **kw: dict(run_type='baseline', **kw)
    A = lambda **kw: dict(CHIMERA_V5, run_type='ablation', **kw)
    return {
        # --- Baselines: identical bounds (0.5 core/pod start, >= 2 replicas), 70% target ---
        'HPA':                  B(kind='hpa', **BOUNDS_H_ONLY),
        'VPA':                  B(kind='vpa', v_min=0.5, v_max=4.0, h_min=5, h_max=5),
        'HPA+VPA':              B(kind='hpa_vpa', **BOUNDS_JOINT),
        'AAPA':                 B(kind='aapa', **BOUNDS_H_ONLY),
        'MagicScaler-Inspired': B(kind='magic', **BOUNDS_H_ONLY),
        # KOSMOS PI gains re-tuned on the dev split under the current simulator
        # (src/scripts/kosmos_retune.py, CODE_FIXES_LOG.md item 20). The August gains
        # (DC=0.8, BC=0.15, alpha=0.70) predate the load-sensor fix and make the vertical loop
        # bang-bang; they are kept as KOSMOS_AugustGains for reference.
        'KOSMOS':               B(kind='kosmos', **BOUNDS_JOINT, **KOSMOS_GAINS, alpha=0.25),
        'KOSMOS_AugustGains':   dict(kind='kosmos', run_type='sensitivity', **BOUNDS_JOINT, DC=0.8, BC=0.15,
                                     alpha=0.70),
        'CHIMERA_v1':           dict(CHIMERA_V1, run_type='baseline'),
        'CHIMERA_v2':           dict(CHIMERA_V2, run_type='baseline'),
        'CHIMERA_v3':           dict(CHIMERA_V3, run_type='baseline'),
        'CHIMERA_v4':           dict(CHIMERA_V4, run_type='baseline'),
        'CHIMERA':              dict(CHIMERA_V5, run_type='baseline'),
        'CHIMERA_Decoupled':    dict(CHIMERA_V5, run_type='baseline', decoupled_safety=True),
        # --- Reference bound: clairvoyant, instant, no boot delay. Not a competitor. ---
        'Oracle':               dict(kind='oracle', run_type='reference', **BOUNDS_JOINT),
        # --- Sensitivity: 3x head-start pods for horizontal-only baselines; no KEP-1287 ---
        'HPA_1.5':              dict(kind='hpa', run_type='sensitivity', v_min=1.5, v_max=1.5, h_min=2, h_max=30),
        'AAPA_1.5':             dict(kind='aapa', run_type='sensitivity', v_min=1.5, v_max=1.5, h_min=2, h_max=30),
        'KOSMOS_NoInPlace':     dict(kind='kosmos', run_type='sensitivity', **BOUNDS_JOINT, **KOSMOS_GAINS,
                                     alpha=0.25, use_inplace_resize=False),
        'CHIMERA_NoInPlace':    dict(CHIMERA_V5, run_type='sensitivity', use_inplace_resize=False),
        # --- Ablation: each removes (w/o) or re-adds (+) exactly one mechanism of CHIMERA v4 ---
        'CHIMERA_Full':                     A(),
        'CHIMERA_+Lyapunov_Filter':         A(sigma=0.1),
        'CHIMERA_w/o_Coupling_Penalty':     A(kappa=0.0, eta=0.0),
        'CHIMERA_w/o_CQR':                  A(use_cqr=False),
        'CHIMERA_w/o_ADWIN_Drift':          A(use_drift=False),
        'CHIMERA_w/o_OOD_Fallback':         A(use_ood_fallback=False),
        'CHIMERA_w/o_State_Augmentation':   A(use_state_augmentation=False),
        'CHIMERA_w/o_Anti_Chattering':      A(use_scale_down_guardrail=False),
        'CHIMERA_w/o_Joint_H_V':            A(v_min=0.5, v_max=0.5, use_inplace_resize=False),
        'CHIMERA_w/o_Vertical_Bridging':    A(vertical_bridging=False),
        'CHIMERA_w/o_Asymmetric_Smoothing': A(asymmetric_smoothing=False),
        'CHIMERA_+Online_Conformal':        A(online_conformal=True),
        'CHIMERA_w/o_Naive_Conformal':      A(naive_conformal_fallback=False),
        'CHIMERA_Double_Count_Inflight':    A(serving_replica_state=False),
        'CHIMERA_Legacy_Delay_Model':       A(legacy_delay_model=True),
        'CHIMERA_Legacy_Coupling':          A(coupling_mode='absolute'),
        'CHIMERA_w/o_Cascade':              A(inner_vertical_period=0.0),
        'CHIMERA_w/o_Fast_Release':         A(inner_release_band=0.0),
        'CHIMERA_w/o_Replica_Band':         A(replica_band=0.0),
        'CHIMERA_w/o_Inner_Owns_CPU':       A(inner_owns_vertical=False),
        'CHIMERA_w/o_ADWIN_Margin_Reset':   A(adwin_margin_reset=False),
        'CHIMERA_w/o_Up_Deadband':          A(inner_up_band=0.0),
    }


# Cost/SLO frontier: every controller swept over its own conservatism knob.
# Extended to 0.95 for every controller so the cheap end of each frontier is covered.
SWEEP_UTILS = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
# KOSMOS's setpoint is the MEAN response time (alpha x SLO), not utilisation. A per-request
# SLO needs a mean well below the threshold, so the sweep covers small alpha.
SWEEP_KOSMOS_ALPHA = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70]
SWEEP_FAMILIES = ['HPA', 'VPA', 'HPA+VPA', 'AAPA', 'MagicScaler-Inspired', 'KOSMOS', 'CHIMERA_v1', 'CHIMERA_v2',
                  'CHIMERA_v3', 'CHIMERA_v4', 'CHIMERA', 'Oracle']


def build_sweep_configs(base: Dict[str, dict]) -> Dict[str, dict]:
    out = {}
    for fam in SWEEP_FAMILIES:
        if fam == 'KOSMOS':
            for a in SWEEP_KOSMOS_ALPHA:
                out[f'{fam}@a={a:.2f}'] = dict(base[fam], run_type='sweep', family=fam, knob=a, alpha=a)
        else:
            for u in SWEEP_UTILS:
                out[f'{fam}@u={u:.2f}'] = dict(base[fam], run_type='sweep', family=fam, knob=u,
                                               target_utilization=u)
    return out


# --- Parallel execution ----------------------------------------------------------------

_WORKER_STATE: Dict[str, Any] = {}

def _init_worker(workloads, cqr_models, split='test', warmup=0, service_dist='exponential',
                 slowdown_model='inverse'):
    logging.disable(logging.CRITICAL)
    _WORKER_STATE['service_dist'] = service_dist
    _WORKER_STATE['slowdown_model'] = slowdown_model
    _WORKER_STATE['workloads'] = {w['id']: w for w in workloads}
    _WORKER_STATE['cqr'] = cqr_models
    _WORKER_STATE['split'] = split
    _WORKER_STATE['warmup'] = warmup

def _run_one(job):
    w_id, config_name, config, seed = job
    item = _WORKER_STATE['workloads'][w_id]
    t0 = time.perf_counter()
    metrics = run_simulation(item['trace'], config_name, config, item['type'], _WORKER_STATE['cqr'][w_id], seed,
                             split=_WORKER_STATE['split'], warmup=_WORKER_STATE.get('warmup', 0))
    wall = time.perf_counter() - t0
    rts = np.asarray(metrics.response_times) if metrics.response_times else np.zeros(1)
    return {
        'seed': seed,
        'run_type': config['run_type'],
        'config_name': config_name,
        'family': config.get('family', config_name),
        'knob': config.get('knob', config.get('target_utilization', 0.70)),
        'workload_id': w_id,
        'workload_type': item['type'],
        'slo_violation_rate': metrics.get_slo_violation_rate(),
        'mean_response_time': metrics.get_mean_response_time(),
        'p95_response_time': float(np.percentile(rts, 95)),
        'p99_response_time': metrics.get_p99_response_time(),
        'pod_minutes': metrics.pod_minutes,
        # Real cost: cores x minutes allocated (pod-minutes ignore pod size)
        'core_minutes': metrics.total_cpu_allocated,
        'scaling_actions': metrics.scaling_actions,
        'vertical_scaling_actions': metrics.vertical_scaling_actions,
        'horizontal_scaling_actions': metrics.horizontal_scaling_actions,
        'coupling_penalty': metrics.get_coupling_penalty_rate(),
        'rei': calculate_extended_rei(metrics),
        'lyapunov_filter_activation': metrics.lyapunov_activations,
        'drift_detector_activation': metrics.drift_activations,
        'coupling_penalty_activation': metrics.coupling_activations,
        'CQR_interval_width': float(np.mean(metrics.cqr_widths)) if metrics.cqr_widths else 0.0,
        'CQR_empirical_coverage': float(np.mean(metrics.cqr_coverages)) if metrics.cqr_coverages else 0.0,
        'replica_oscillation': metrics.replica_oscillations,
        'wall_seconds': wall,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--seeds', type=int, nargs='+', default=DEFAULT_SEEDS)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--out', default='results/results_v2.csv')
    ap.add_argument('--sweep', action='store_true', help='also run the cost/SLO frontier sweep')
    ap.add_argument('--sweep-only', action='store_true', help='run only the frontier sweep')
    ap.add_argument('--configs', nargs='*', help='restrict to these config names')
    ap.add_argument('--workloads', nargs='*', help='restrict to these workload ids')
    ap.add_argument('--split', choices=sorted(SPLITS), default='test',
                    help='test = the reported protocol (minutes 70-100); dev = earlier window, '
                         'dev-only synthetic traces, for design work')
    ap.add_argument('--holdout', action='store_true',
                    help='held-out final set: real traces + four held-out synthetic shapes (use with '
                         '--seeds from HOLDOUT_SEEDS)')
    ap.add_argument('--warmup', type=int, default=0,
                    help='unscored warm-up minutes before the window (0 = start cold at the window)')
    ap.add_argument('--service-dist', choices=sorted(SERVICE_DISTS), default='exponential',
                    help='per-request CPU service-time distribution (mean 0.1 s in every case)')
    ap.add_argument('--slowdown-model', choices=['inverse', 'kingman', 'steep'], default='inverse',
                    help='response-time inflation law; CHIMERA risk sizing assumes inverse')
    args = ap.parse_args()

    out = Path(args.out)
    if out.resolve() == Path('results/results_FINAL_verified.csv').resolve():
        sys.exit("Refusing to overwrite the Review-2 verified results file.")

    logger.info("Starting CHIMERA evaluation harness...")
    if args.holdout:
        workloads = get_holdout_workloads()
    else:
        workloads = get_remediation_workloads() if args.split == 'test' else get_dev_workloads()
    if args.workloads:
        workloads = [w for w in workloads if w['id'] in args.workloads]

    base = build_configs()
    configs = {} if args.sweep_only else dict(base)
    if args.sweep or args.sweep_only:
        configs.update(build_sweep_configs(base))
    if args.configs:
        configs = {k: v for k, v in configs.items() if k in args.configs}

    cqr_models = {}
    for item in workloads:
        logger.info(f"Training CQR model for trace {item['id']}...")
        cqr_models[item['id']] = train_cqr_for_trace(item['trace'], args.split)

    jobs = [(w['id'], name, cfg, seed) for w in workloads for name, cfg in configs.items() for seed in args.seeds]
    logger.info(f"{len(jobs)} runs = {len(configs)} configs x {len(workloads)} workloads x {len(args.seeds)} seeds, "
                f"{args.workers} workers")

    t0 = time.time()
    results = []
    with Pool(args.workers, initializer=_init_worker, initargs=(workloads, cqr_models, args.split, args.warmup,
                                                          args.service_dist, args.slowdown_model)) as pool:
        for i, row in enumerate(pool.imap_unordered(_run_one, jobs, chunksize=2), 1):
            results.append(row)
            if i % 50 == 0 or i == len(jobs):
                logger.info(f"  {i}/{len(jobs)} runs done ({time.time() - t0:.0f}s)")

    df = pd.DataFrame(results).sort_values(['run_type', 'config_name', 'workload_id', 'seed'])
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    logger.info(f"Evaluation completed in {time.time() - t0:.0f}s. Results saved to {out}")


if __name__ == "__main__":
    main()
