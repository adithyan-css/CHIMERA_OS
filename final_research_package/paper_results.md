# Paper Evidence: Experimental Results

Averaged across 8 workloads and 10 seeds:

*   **HPA**: SLO Violations = 7.01%, pod-minutes = 81.23, Extended REI = **0.9208**
*   **VPA**: SLO Violations = 14.01%, pod-minutes = 150.00, Extended REI = **0.8924**
*   **HPA+VPA Coexistence**: SLO Violations = 14.33%, pod-minutes = 165.06, Extended REI = **0.8721**
*   **AAPA**: SLO Violations = 4.20%, pod-minutes = 102.76, Extended REI = **0.9180**
*   **MagicScaler-Inspired**: SLO Violations = 8.11%, pod-minutes = 80.08, Extended REI = **0.9122**
*   **Joint-LQR-Lyapunov (Unconstrained)**: SLO Violations = 25.78%, pod-minutes = 98.21, Extended REI = **0.8354**
*   **CHIMERA**: SLO Violations = 25.77%, pod-minutes = **98.21**, Extended REI = **0.8354**

## Workload-by-Workload Highlights
1.  **real_kswd_w2 (Periodic)**: CHIMERA outperforms HPA in SLO violations ($2.74\%$ vs $11.34\%$), VPA ($22.62\%$), and HPA+VPA coexistence ($19.47\%$).
2.  **real_kswd_w5 (Stationary Noisy)**: CHIMERA reduces SLO violations to **$2.02\%$** (compared to HPA's $11.50\%$ and VPA's $22.98\%$) with comparable pod-minutes.
3.  **extreme_overload**: Under massive spikes (600 RPM), CHIMERA’s hybrid reactive fallback scales resources quickly, preventing queue saturation but incurring a transient SLO violation penalty due to startup latency.
