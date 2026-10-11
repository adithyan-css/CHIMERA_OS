import numpy as np
import simpy
from src.core.chimera_controller import ChimeraController
from src.core.chimera_simulator import ChimeraSimulator

# Spike trace: rate in requests/minute
spike_trace = np.concatenate([
    np.ones(10) * 10.0,
    np.ones(10) * 150.0,
    np.ones(10) * 10.0,
    np.ones(10) * 200.0,
    np.ones(20) * 10.0
])

print("Running Simulator Physics Sanity Check (Utilization vs SLO Violation Rate)")
print("-" * 80)
print(f"{'Replicas':<10}{'CPU/Pod':<10}{'Capacity (Cores)':<18}{'Avg Load (RPS)':<16}{'Avg Util %':<14}{'SLO Viol %':<10}")
print("-" * 80)

# Test different fixed provisioning sizes
configurations = [
    (2, 0.5),   # Severely under-provisioned (1.0 core capacity)
    (2, 1.0),   # Under-provisioned (2.0 cores capacity)
    (4, 1.5),   # Moderate capacity (6.0 cores capacity)
    (8, 2.0),   # Well-provisioned (16.0 cores capacity)
    (15, 3.0),  # Over-provisioned (45.0 cores capacity)
    (30, 4.0),  # Max-provisioned (120.0 cores capacity)
]

for replicas, cpu_per_pod in configurations:
    np.random.seed(42)
    env = simpy.Environment()
    
    # We want a static/fixed size controller to prevent scaling actions so we can measure static capacity effects
    controller = ChimeraController(v_min=cpu_per_pod, v_max=cpu_per_pod, h_min=replicas, h_max=replicas)
    
    sim = ChimeraSimulator(
        env,
        controller,
        slo_threshold=0.5,
        use_inplace_resize=False,
        cpu_per_request=10.0 # Match the 10.0 core multiplier from run_eval.py to scale spike rates
    )
    
    exec_dist = lambda: np.random.exponential(0.1)
    
    # Override autoscaling_controller to do nothing so capacity remains fixed
    def dummy_autoscaler(*args, **kwargs):
        while True:
            yield env.timeout(10.0)
    sim.autoscaling_controller = dummy_autoscaler
    
    metrics = sim.run(spike_trace, "sanity-check-func", exec_dist)
    
    # Calculate average utilization
    # demand_rps = arrival_rate_rps
    # capacity_rps = total_cpu / cpu_per_request
    # utilization = demand_rps / capacity_rps = arrival_rate * cpu_per_request / total_cpu
    avg_load_rps = np.mean(spike_trace) / 60.0
    total_cpu = replicas * cpu_per_pod
    capacity_rps = total_cpu / 10.0
    avg_util = (avg_load_rps / capacity_rps) * 100
    
    slo_viol_pct = metrics.get_slo_violation_rate() * 100
    
    print(f"{replicas:<10}{cpu_per_pod:<10.1f}{total_cpu:<18.1f}{avg_load_rps:<16.3f}{avg_util:<14.1f}{slo_viol_pct:<10.2f}")

print("-" * 80)
