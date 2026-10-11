import numpy as np
import simpy
from src.core.chimera_controller import ChimeraController
from src.core.chimera_simulator import ChimeraSimulator, calculate_extended_rei, JointMetrics
from src.core.simulator import Pod

# 1. SPIKE Workload
spike_trace = np.concatenate([
    np.ones(30) * 10.0,
    np.ones(10) * 150.0,
    np.ones(30) * 10.0,
    np.ones(10) * 200.0,
    np.ones(40) * 10.0
])

cpp = 0.5

class MockCQR:
    def __init__(self, normal_headroom, rapid_headroom):
        self.sim = None
        self.alpha = 0.1
        self.normal_headroom = normal_headroom
        self.rapid_headroom = rapid_headroom
        
    def predict_conformal_bounds(self, features):
        history = self.sim.load_history
        if not history:
            current_load = 0.0
        else:
            current_load = history[-1]
            
        L_t = np.array([current_load * 0.8])
        headroom = self.normal_headroom
        if len(history) >= 2 and history[-1] > history[-2]:
            headroom = self.rapid_headroom
            
        U_t = np.array([current_load * headroom])
        u_width = float(np.mean(U_t - L_t))
        return L_t, U_t, u_width

print("SWEEPING CHIMERA HEADROOM AND TARGET UTILIZATION")
print("-" * 110)
print(f"{'target_util':<15}{'norm_hr':<10}{'rap_hr':<10}{'SLO Viol%':<12}{'PodMin':<10}{'TotalCPUMin':<15}{'Actions':<10}{'REI':<8}")
print("-" * 110)

for target_util in [0.5, 0.55, 0.6, 0.65]:
    for norm_hr in [1.5, 1.8]:
        for rap_hr in [2.0, 2.4, 2.8]:
            np.random.seed(42)
            env = simpy.Environment()
            
            controller = ChimeraController(
                v_min=0.5, v_max=4.0, h_min=2, h_max=30,
                sigma=0.1, zeta=0.5, kappa=0.5, eta=0.2
            )
            
            cqr = MockCQR(norm_hr, rap_hr)
            sim = ChimeraSimulator(
                env,
                controller,
                cqr_regressor=cqr,
                slo_threshold=0.5,
                use_inplace_resize=True,
                cpu_per_request=cpp
            )
            cqr.sim = sim
            sim.vertical_scale_cooldown = 15.0
            
            def custom_autoscaling_controller(self, features_list=None):
                while True:
                    current_load = self.calculate_current_load()
                    current_replicas = len(self.pods)
                    self.load_history.append(current_load)
                    
                    L_t, U_t, u_width = self.cqr_regressor.predict_conformal_bounds(None)
                    target_demand = float(U_t[0])
                    
                    # Heuristic split using loop variable target_util
                    total_cores_needed = target_demand * self.cpu_per_request / target_util
                    h_star = int(np.ceil(np.sqrt(total_cores_needed / self.controller.gamma_ratio)))
                    h_star = max(self.controller.h_min, min(h_star, self.controller.h_max))
                    v_star = total_cores_needed / h_star
                    v_star = max(self.controller.v_min, min(v_star, self.controller.v_max))
                    
                    x_star_raw = np.array([v_star, h_star])
                    if not hasattr(self, 'x_star_smoothed'):
                        self.x_star_smoothed = x_star_raw.copy()
                    else:
                        self.x_star_smoothed = 0.1 * x_star_raw + 0.9 * self.x_star_smoothed
                    x_star = self.x_star_smoothed
                    
                    x_t = np.array([self.current_cpu_cores, current_replicas])
                    
                    u_opt, x_next = self.controller.get_desired_allocation(
                        x_t=x_t,
                        x_star=x_star,
                        uncertainty_width=u_width,
                        tau=self.horizontal_delay
                    )
                    
                    dv, dh = u_opt[0], u_opt[1]
                    
                    # Actuate vertical scaling
                    if abs(dv) >= 0.20 and (self.env.now - self.last_vertical_scale_time >= self.vertical_scale_cooldown):
                        new_cpu = x_t[0] + dv
                        for pod in self.pods:
                            pod.cpu_cores = new_cpu
                        self.current_cpu_cores = new_cpu
                        self.metrics.scaling_actions += 1
                        self.last_vertical_scale_time = self.env.now
                        
                    # Actuate horizontal scaling
                    dh_int = int(np.round(dh))
                    if dh_int > 0:
                        for _ in range(dh_int):
                            self.pods.append(Pod(id=f"pod-{len(self.pods)}", cpu_cores=self.current_cpu_cores, warmup_time=self.env.now))
                        self.metrics.scaling_actions += 1
                    elif dh_int < 0:
                        if self.env.now - self.last_scale_down_time >= self.scale_down_cooldown:
                            self.pods.sort(key=lambda p: p.last_used)
                            to_remove = min(abs(dh_int), len(self.pods) - self.controller.h_min)
                            if to_remove > 0:
                                self.pods = self.pods[to_remove:]
                                self.metrics.scaling_actions += 1
                                self.last_scale_down_time = self.env.now
                                
                    self.metrics.cpu_limit_history.append(self.current_cpu_cores)
                    total_active_cpu = sum(p.cpu_cores for p in self.pods)
                    self.metrics.total_cpu_allocated += total_active_cpu * (self.scale_interval / 60.0)
                    self.metrics.pod_minutes += len(self.pods) * (self.scale_interval / 60.0)
                    
                    yield self.env.timeout(self.scale_interval)

            import types
            sim.autoscaling_controller = types.MethodType(custom_autoscaling_controller, sim)
            
            exec_dist = lambda: np.random.exponential(0.1)
            metrics = sim.run(spike_trace, "sweep-func", exec_dist)
            rei = calculate_extended_rei(metrics)
            
            print(f"{target_util:<15.2f}{norm_hr:<10.1f}{rap_hr:<10.1f}{metrics.get_slo_violation_rate()*100:<12.2f}{metrics.pod_minutes:<10.2f}{metrics.total_cpu_allocated:<15.2f}{metrics.scaling_actions:<10d}{rei:<8.3f}")

print("-" * 110)
