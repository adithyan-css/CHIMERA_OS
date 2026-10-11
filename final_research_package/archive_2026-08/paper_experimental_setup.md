# Paper Evidence: Experimental Setup

We evaluate CHIMERA using an extended discrete-event SimPy simulator matching production Kubernetes orchestration latencies and capacities:
*   **Sampling Interval**: 15 seconds.
*   **Actuation Latency**:
    - Horizontal scaling delay ($\tau$): 20.0 seconds (container image pull and pod initialization).
    - Vertical in-place resizing (KEP-1287): 0.1 seconds.
    - Vertical rolling restart (fallback): 30.0 seconds per pod.
*   **Capacity Boundaries**:
    - CPU cores per pod ($v_t$): $[0.5, 4.0]$ cores.
    - Pod replicas ($h_t$): $[2, 30]$ replicas.
    - Total CPU capacity limit: $120.0$ cores.
*   **Trace Preprocessing**: Scaled KSWD workload traces by $15.0$ to push demands above minimum capacity. Divided by $60.0$ to map RPM to RPS.
*   **Data Partitioning**: 50% train (0-50m), 20% validation (50-70m), 30% test evaluation (70-100m).
*   **Response Time SLO**: 500 milliseconds.
*   **Uncertainty UQ**: Conformalized Quantile Regression (CQR) with 90% target coverage.
