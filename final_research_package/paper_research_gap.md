# Paper Evidence: Research Gap

Prior academic approaches to joint horizontal-vertical autoscaling suffer from three primary limitations:
1.  **Lack of Mathematical Coordination**: Existing joint scalers rely on rule-based heuristics, serialized loop execution, or decoupled scaling thresholds. They do not formulate H/V scaling as a coupled multi-input multi-output (MIMO) optimal control problem, which leads to suboptimal cost-performance tradeoffs under highly dynamic microservice workloads.
2.  **Absence of Stability and Convergence Guarantees**: Heuristic joint scalers lack formal mathematical guarantees regarding state-space tracking stability. Without formal stability criteria, they are susceptible to trajectory overshoot during transient workload surges.
3.  **Heuristic Uncertainty Representation**: Predictive autoscalers rely on point forecasts or Gaussian variance assumptions that fail to capture the distribution-free nature of serverless and microservice traffic fluctuations. 

CHIMERA bridges this gap by combining 2D LQR optimal control with an explicit cross-coupling penalty, a Control Lyapunov Function (CLF) safety projection filter, and Conformalized Quantile Regression (CQR) prediction intervals.
