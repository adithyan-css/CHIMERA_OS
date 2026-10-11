# Paper Evidence: Discussion

## 1. Conformal Coverage & Out-of-Distribution Workloads
Our validation shows that Conformalized Quantile Regression (CQR) is highly effective for in-distribution traffic profiles (e.g. `w_0004`, `w_0005`, achieving $90\%+$ coverage). However, under sudden, high-amplitude spikes, LightGBM models fail to extrapolate because gradient-boosted trees are fundamentally bounded by the range of their training targets. This highlights a critical limitation of machine learning in autoscaling: pure predictive control cannot guarantee safety under OOD workload shifts. The integration of a hybrid reactive fallback is necessary to bridge this gap in production environments.

## 2. Control Lyapunov Function (CLF) safety activations
Lyapunov activations are passive by design: they remain dormant ($0.0$) under nominal loads when the optimal LQR feedback controller is already safe. Under abrupt drifts (`synthetic_drift`), the safety filter actively triggers (**$1.1$ activations** average) to clamp nominal commands, proving its utility as a mathematical safety guardrail.

## 3. Cross-Coupling Penalty activations
Under extreme spikes (`synthetic_extreme_overload`), the coupling penalty triggers (**$13.8$ activations** average). This prevents uncoordinated chattering ($dv \cdot dh < 0$), keeping the scaling churn low compared to uncoordinated coexistence loops.
