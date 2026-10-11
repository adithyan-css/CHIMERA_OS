# CHIMERA: 5-Minute Spoken Explanation

This document serves as a natural spoken transcript to explain the CHIMERA project in approximately 5 minutes.

---

## "What is the problem we are solving?"
When you run a microservice in production on Kubernetes, you want to make sure it always has enough CPU and memory to handle user requests without crashing or violating Service Level Objectives (SLOs). Kubernetes gives you two ways to scale resources:
1.  **Horizontal Pod Autoscaling (HPA)**: Adding more copies of your application container.
2.  **Vertical Pod Autoscaling (VPA)**: Resizing the CPU and memory limits of each pod.

The catch is that in standard Kubernetes, HPA and VPA operate completely independently. HPA might see high CPU utilization and add pods, while VPA sees low load per pod and shrinks their size. This uncoordinated behavior causes scaling fights, chattering, resource wastage, and performance drops.

---

## "What is CHIMERA?"
CHIMERA is a joint horizontal-vertical autoscaler. Instead of running HPA and VPA as separate loops, we model the resource state as a unified 2D vector of CPU limits and pod replicas. 

We solve the Discrete Algebraic Riccati Equation online to compute optimal control gains using an LQR feedback law. To prevent horizontal and vertical resizing commands from conflicting, we introduce an explicit cross-coupling cost penalty during optimization projection. We also apply a Control Lyapunov Function (CLF) safety filter to restrict aggressive transitions under sudden drifts, ensuring the system remains stable.

---

## "How does the prediction and fallback work?"
To actuate resources proactively, we predict traffic demand using Conformalized Quantile Regression (CQR). We train LightGBM models online to predict quantiles and apply conformal calibration on validation partitions. 

However, ML models suffer from out-of-distribution (OOD) failure during extreme spikes. To keep the system safe, we deploy a hybrid fallback:
\[\text{target\_demand} = \max(\text{current\_load}, U_t)\]
This lets CHIMERA proactively scale using CQR under normal conditions, but immediately fall back to reactive load scaling during sudden surges. ADWIN tracks concept drifts and adjusts Lyapunov safety parameters dynamically.

---

## "What experiments did we run and what did we achieve?"
We evaluated CHIMERA across 10 random seeds and 8 workloads (including 4 real Azure Functions traces and 4 synthetic stress traces). 

1.  **Fixed the units and capacity scaling**: Aligned CQR targets to RPS, which fixed the $0\%$ empirical coverage (now achieving $74\%-96\%$ coverage) and reduced the resource footprint from 494.28 to **98.21 pod-minutes**.
2.  **Fair Baselines**: Corrected HPA and AAPA capacity assumptions to pod sizes of 1.5 cores instead of default values.
3.  **Active safety guardrails**: Lyapunov safety constraints triggered (1.1 activations average) under drift, and cross-coupling penalties suppressed chattering (13.8 activations average) under overload spikes.

---

## "What are the limitations and what is next?"
The primary limitation is the lack of physical Kubernetes cluster execution; the Prometheus metric query loop is currently mocked in our CRD reconciliation operator manager.

For our conference submission, we should characterize CHIMERA as a joint performance-cost tradeoff. It reduces resource footprint under nominal loads, but the 20-second pod startup delay means it incurs a transient latency penalty compared to horizontal-only HPA which runs with high static headroom.
The next step is to freeze the implementation and deploy the operator manager on a real GKE/EKS cluster to validate KEP-1287 in-place container resizing.
