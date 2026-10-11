# Paper Evidence: Contributions

CHIMERA makes the following primary contributions:
1.  **Unified 2D Optimal Control Formulation**: We formulate horizontal and vertical autoscaling as a coupled 2D state-space tracking problem, solving the Discrete Algebraic Riccati Equation (DARE) online to compute optimal LQR feedback gains.
2.  **Explicit Cross-Coupling Penalty**: We introduce a mathematical cross-coupling cost term ($\Psi$) to penalize simultaneous opposing resizing commands ($dv \cdot dh < 0$) and maintain a balanced CPU-to-replica ratio, suppressing chattering.
3.  **Control Lyapunov Function (CLF) Safety Guardrail**: We apply a 2D Lyapunov stability constraint to filter unconstrained LQR nominal commands, projecting unsafe actions onto a stable boundary in less than 0.5ms using grid-search.
4.  **Distribution-Free Conformal Prediction with Fallback**: We integrate online-trained Conformalized Quantile Regression (CQR) to quantify prediction intervals. We address CQR out-of-distribution (OOD) extrapolation limitations by implementing a hybrid reactive-predictive fallback.
5.  **Extensive Simulation and Operator Prototyping**: We evaluate CHIMERA across 10 random seeds and 8 workloads, comparing it against HPA, VPA, uncoordinated coexistence, and MagicScaler. We prototype a Kubernetes operator implementing this control law.
