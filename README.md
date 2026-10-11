# CHIMERA: Coupled Horizontal/Vertical Intelligent Machine-learning-based Resource Autoscaler

CHIMERA is a next-generation Kubernetes autoscaler that jointly optimizes Horizontal Pod Autoscaling (HPA) and Vertical Pod Autoscaling (VPA) through optimal feedback control theory (LQR/Riccati) under mathematical Lyapunov stability boundaries coupled to Conformalized Quantile Regression (CQR) workload uncertainty bounds.

It is built as a major architectural upgrade and extension of the **AAPA** baseline paper framework (arXiv:2507.05653).

---

## Key Features
- **Joint HPA+VPA Control (★★★★★)**: Solves for both replica count ($h_t$) and CPU cores ($v_t$) as a coupled 2D state-space vector ($x_t = [v_t, h_t]^T$).
- **Cross-Coupling Penalty (★★★★☆)**: Formalizes the dynamic interference $\Psi(v, h)$ of simultaneous horizontal and vertical resource transitions.
- **Lyapunov Stability Bound (★★★★☆)**: Formally guarantees bounded-input bounded-state (BIBS) convergence using a Control Lyapunov Function (CLF) safety filter.
- **Conformal Uncertainty Bounds (★★★☆☆)**: Employs Conformalized Quantile Regression (CQR) for prediction interval coverage instead of standard calibration heuristics.
- **Concept Drift Detection (★☆☆☆☆)**: Employs ADWIN to track errors and dynamically adapt Lyapunov safety weights under workload distribution shifts.

---

## Project Structure
```
chimera_project/
├── src/
│   ├── core/
│   │   ├── chimera_controller.py  # Joint LQR-Lyapunov controller
│   │   ├── chimera_simulator.py   # SimPy 2D Kubernetes simulator
│   │   ├── simulator.py           # Original AAPA simulator
│   │   └── autoscaler.py          # Original AAPA autoscaler
│   ├── models/
│   │   ├── cqr_uncertainty.py     # CQR interval prediction models
│   │   └── drift_detector.py      # ADWIN drift detection
│   └── scripts/
│       ├── train_cqr.py           # Quantile training script
│       └── run_eval.py            # Comparative baseline evaluation
├── tests/
│   └── test_chimera.py            # Unit tests suite
├── k8s/
│   ├── crd.yaml                   # ChimeraAutoscaler CRD
│   ├── controller.yaml            # Manager Deployment & RBAC
│   ├── Dockerfile                 # Image build recipe
│   ├── manager.py                 # Operator reconciliation manager
│   └── quickstart.sh              # Local Kind cluster quickstart setup
└── requirements.txt               # Dependencies list
```

---

## Getting Started

### 1. Installation
Ensure Python 3.10+ is installed. Clone the repository and install dependencies:
```bash
pip install -r requirements.txt
pip install kubernetes prometheus-client
```

### 2. Run Unit Tests
Verify the control theory math and code safety (dependency-free tests):
```bash
python tests/test_chimera.py
```

### 3. Model Training
Train the CQR quantile regression models (fits GBDT regressors to predict quantile intervals):
```bash
python src/scripts/train_cqr.py
```
This saves model files under `models/cqr_model_low.txt` and `models/cqr_model_high.txt`.

### 4. Running Comparative Simulation Evaluation
To run the full comparative analysis (HPA vs AAPA vs MagicScaler vs AWARE vs CHIMERA) on benchmark traces:
```bash
python src/scripts/run_eval.py
```
This saves detailed metrics for every step to `results/simulation_results.csv` with a fixed column schema for downstream plots:
`autoscaler,workload_id,slo_violation_rate,mean_response_time,p99_response_time,pod_minutes,scaling_actions,coupling_penalty,rei`

---

## Kubernetes Quickstart (Kind / Local)

A complete local deployment pipeline is provided in `k8s/`. To build and run in a local `kind` cluster:
```bash
chmod +x k8s/quickstart.sh
./k8s/quickstart.sh
```

This script:
1. Spawns a local `kind` cluster named `chimera-cluster`.
2. Builds the operator Docker image (`chimera-controller:latest`).
3. Loads the image into the cluster.
4. Registers the `ChimeraAutoscaler` CRD.
5. Deploys the operator manager.
6. Launches a mock `billing-service` app deployment and attaches a `ChimeraAutoscaler` policy YAML.

---

## License & Attribution
This project is licensed under the MIT License. The code builds upon the open-source AAPA simulator framework.
All control-theoretic modifications, conformal prediction elements, and drift feedback integrations are proprietary contributions of the CHIMERA project.
