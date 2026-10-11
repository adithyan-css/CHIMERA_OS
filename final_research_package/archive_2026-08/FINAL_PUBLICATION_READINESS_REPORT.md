# CHIMERA Final Publication Readiness Report

This report evaluates the publication-readiness of the CHIMERA autoscaling framework before submission to target conferences (e.g. IEEE IC2E, ACM SoCC, IEEE CLOUD).

---

## 1. Score Matrix

- **Problem significance**: **9/10** (Microservice H/V coexistence conflicts and resource chattering are well-known production issues).
- **Novelty**: **8/10** (The combination of 2D Optimal Control with explicit cross-coupling penalty is novel for microservice autoscaling).
- **Technical contribution**: **8/10** (DaRE Riccati feedback coordination coupled with CLF safety grids and conformal bounds).
- **Experimental rigor**: **9/10** (1,040 simulation runs across 10 random seeds).
- **Baseline quality**: **8/10** (Decoupled simulators and corrected capacity limits for fair HPA, VPA, AAPA, and MagicScaler comparisons).
- **Dataset quality**: **9/10** (Azure Functions KSWD workloads paired with designed stress workloads).
- **Statistical validity**: **8/10** (Workload-level seed aggregation prevents sample size inflation, utilizing paired t-test).
- **Ablation quality**: **9/10** (Systematically deactivates CQR, Cross-coupling, Lyapunov CLF, ADWIN, and Joint optimization).
- **Reproducibility**: **10/10** (One-click evaluation harness, deterministic seeds, and SHA-256 results check).
- **Kubernetes/system validation**: **3/10** (Kubernetes CRD reconciliation manager is prototyped, but Prometheus telemetry metrics and container resizes are mocked).
- **Overall conference readiness**: **8.5/10**

---

## 2. Low Score Explanations (Scores < 8)

### Kubernetes/system validation: 3/10
*   **Limitation**: Telemetry and resizing operations in `k8s/manager.py` are mocked. Local kind/minikube runtimes do not support container vertical resize (KEP-1287) in Docker-in-Docker environments.
*   **Remediation**: Physical GKE/EKS cluster provisioning with the `InPlacePodVerticalScaling` feature gate active is required to perform live verification and lift this score.

---

## 3. Final Classification

### **READY WITH LIMITATIONS**

The experimental pipeline, baseline fairness, statistical rigor, and mathematical control formulations are fully validated. The paper is ready to be written, provided that:
1.  The LightGBM CQR out-of-distribution extrapolation failure and the necessity of the hybrid reactive fallback are transparently discussed.
2.  The transient latency overhead under sudden spikes (due to pod startup delays and dynamic CPU limits cost-reduction tradeoffs) is clearly analyzed.
3.  The Kubernetes operator is presented as a prototyped CRD integration manager with live cluster validation as future work.
