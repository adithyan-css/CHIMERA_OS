# CHIMERA Repository Audit Report

This report presents a comprehensive audit of the CHIMERA codebase, datasets, results, and literature references as of August 18, 2026.

---

## 1. Directory Structure and File Status

### 1.1 Active Production Modules
*   [`src/core/chimera_simulator.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/core/chimera_simulator.py): The main 2D SimPy simulation loop implementing horizontal-vertical scaling, KEP-1287 in-place resizes, rolling updates, feature extraction, concept drift checks, and dynamic CQR coverage/width logging. Includes the hybrid reactive-predictive fallback.
*   [`src/core/chimera_controller.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/core/chimera_controller.py): The optimal controller solving DARE to compute LQR feedback gains ($K$), and projecting candidates onto safety boundaries via grid-search using Control Lyapunov Functions (CLF) and cross-coupling penalties.
*   [`src/models/cqr_uncertainty.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/models/cqr_uncertainty.py): Conformalized Quantile Regressor implementing online LightGBM quantile training ($\alpha=0.1$) and conformal calibration.
*   [`src/models/drift_detector.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/models/drift_detector.py): ADWIN drift detector used to dynamically scale safety margins and flush cal scores.
*   [`src/data/features.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/data/features.py): Extractor of 37 statistical time/frequency features.
*   [`k8s/manager.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/k8s/manager.py): Kubernetes Custom Resource controller implementing the reconciliation loop for the `ChimeraAutoscaler` CRD.

### 1.2 Obsolete or Approximated Modules
*   [`src/scripts/run_eval.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/scripts/run_eval.py): Obsolete evaluation script using mock predictor bounds and benchmark-specific overrides.
*   [`src/scripts/train_cqr.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/scripts/train_cqr.py): Replaced by online training within evaluation loop, left as an offline reference.

### 1.3 Active Experiment and Analysis Scripts
*   [`src/scripts/run_full_evaluation.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/scripts/run_full_evaluation.py): Single command evaluation harness running 1,040 simulations over 10 seeds and 8 workloads.
*   [`src/scripts/generate_report.py`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/src/scripts/generate_report.py): Post-simulation aggregator verifying cell consistency, running statistical significance tests, and compiling markdown tables.

---

## 2. Experimental Data and Baseline Status

### 2.1 Final Result Files
*   [`results/results.csv`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/results/results.csv): Master raw database containing detailed performance and activation metrics for every seed-workload run.
*   [`results/simulation_results.csv`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/results/simulation_results.csv): Grouped averages for baseline autoscaling strategies.
*   [`results/ablation_results.csv`](file:///C:/Users/adith/.gemini/antigravity/scratch/chimera_project/results/ablation_results.csv): Grouped averages for ablation studies.

### 2.2 Workload Inventory
The evaluation harness utilizes 8 workloads:
1.  **real_kswd_w1 (`w_0001.json`)**: Real Azure Functions trace exhibiting sharp spikes.
2.  **real_kswd_w2 (`w_0002.json`)**: Real periodic daily traffic trace.
3.  **real_kswd_w4 (`w_0004.json`)**: Real gradual load ramp.
4.  **real_kswd_w5 (`w_0005.json`)**: Real stationary load with random noise.
5.  **synthetic_coupled_oscillation**: Alternating load peaks every minute to force horizontal/vertical fighting.
6.  **synthetic_extreme_overload**: Sharp spike (600 RPM) at minute 80 to test Lyapunov limits.
7.  **synthetic_drift**: Mean shift from 15 to 90 RPM at minute 80 to validate ADWIN drift.
8.  **synthetic_stress_uncertainty**: Noisy sine wave to check conformal interval coverage.

---

## 3. Baseline and Ablation Implementations

*   **HPA**: Fixed CPU limits (1.5 cores), scales horizontally using `DecoupledHPAAutoscaler`.
*   **VPA**: Fixed replicas (5 pods), scales container CPU limits vertically reactive between $[0.5, 4.0]$ cores.
*   **HPA+VPA**: Dual uncoordinated loops running concurrently.
*   **AAPA**: Scaled horizontal predictive strategy using online rule-based classifiers and custom warm pools.
*   **MagicScaler-Inspired**: GP-based horizontal scaling loop utilizing Gaussian Process uncertainty bounds.
*   **Joint-LQR-Lyapunov (Unconstrained)**: Joint optimal controller with Lyapunov CLF disabled.
*   **Ablation Configurations**: Deactivates CQR, Cross-coupling, Lyapunov CLF, ADWIN, and Joint optimization one-by-one to isolate performance contributions.
