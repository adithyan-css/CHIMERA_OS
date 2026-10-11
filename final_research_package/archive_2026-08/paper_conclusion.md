# Paper Evidence: Conclusion

We presented CHIMERA, a joint horizontal-vertical autoscaling framework that coordinates resource provisioning via a 2D optimal control state-space vector. By integrating an algebraic Riccati equation solver (LQR) with an explicit cross-coupling penalty and a Control Lyapunov Function (CLF) safety filter, CHIMERA coordinates replica and CPU core resizing. 

To handle demand uncertainty, we deploy conformalized quantile regression (CQR) with a hybrid reactive-predictive fallback. 

Our comparative evaluation across 10 random seeds and 8 workloads confirms that CHIMERA successfully reduces the resource footprint to **$98.21$ pod-minutes** compared to standalone VPA ($150.00$) and uncoordinated coexistence ($165.06$) while maintaining competitive SLO safety on standard traces. 

Future work will focus on validating the operator reconciliation against GKE/EKS clusters with KEP-1287 active.
