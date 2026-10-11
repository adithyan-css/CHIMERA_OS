# CHIMERA Final Results Consistency Report

This report verifies that all numerical values reported in the CHIMERA documentation, tables, and publications align perfectly with the raw simulation dataset (`results/results.csv`).

---

## 1. Master Baseline Comparison Table
Averaged across all 10 seeds and 8 workloads:

| Autoscaler Policy | SLO Violation Rate (%) | Mean Response Time (ms) | P99 Response Time (ms) | Resource Footprint (Pod-Min) | Scaling Churn (Actions) | Coupling Penalty Rate | Extended REI |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **HPA** | 7.01% | 196.1 | 1223.4 | 81.23 ± 25.91 | 2.44 | 0.0000 | **0.9208 ± 0.0465** |
| **VPA** | 14.01% | 304.8 | 2335.4 | 150.00 ± 0.00 | 3.74 | 0.0000 | **0.8924 ± 0.0853** |
| **HPA+VPA** | 14.33% | 394.2 | 3805.7 | 165.06 ± 97.39 | 7.64 | 0.0000 | **0.8721 ± 0.0406** |
| **AAPA** | 4.20% | 160.7 | 871.5 | 102.76 ± 35.10 | 3.16 | 0.0000 | **0.9180 ± 0.0276** |
| **MagicScaler-Inspired** | 8.11% | 219.5 | 1699.7 | 80.08 ± 24.82 | 3.21 | 0.0000 | **0.9122 ± 0.0538** |
| **Joint-LQR-Lyapunov (Unconstrained)** | 25.78% | 1643.8 | 13306.9 | 98.21 ± 51.26 | 2.58 | 0.0086 | **0.8354 ± 0.0943** |
| **CHIMERA** | 25.77% | 1643.7 | 13306.3 | 98.21 ± 51.26 | 2.58 | 0.0086 | **0.8354 ± 0.0943** |

---

## 2. Ablation Comparison Table
Averaged across all 10 seeds and 8 workloads:

| Configuration | SLO Violation Rate (%) | Mean Response Time (ms) | P99 Response Time (ms) | Resource Footprint (Pod-Min) | Scaling Churn (Actions) | Coupling Penalty Rate | REI | Delta REI |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **CHIMERA Full** | 25.77% | 1643.7 | 13306.3 | 98.21 | 2.58 | 0.0086 | **0.8354 ± 0.0943** | +0.0000 |
| **w/o Lyapunov** | 25.77% | 1643.7 | 13306.3 | 98.21 | 2.58 | 0.0086 | **0.8354 ± 0.0943** | +0.0000 |
| **w/o Coupling_Penalty** | 25.64% | 1508.0 | 12502.7 | 98.38 | 2.58 | 0.0000 | **0.8373 ± 0.0985** | +0.0019 |
| **w/o CQR** | 30.72% | 1883.0 | 23056.3 | 81.18 | 4.72 | 0.0101 | **0.8226 ± 0.0910** | -0.0128 |
| **w/o ADWIN_Drift** | 25.77% | 1643.7 | 13306.3 | 98.21 | 2.58 | 0.0086 | **0.8354 ± 0.0943** | +0.0000 |
| **w/o Joint_H_V** | 7.14% | 229.0 | 1811.2 | 98.32 | 0.75 | 0.0106 | **0.9211 ± 0.0461** | +0.0857 |

---

## 3. Cell Consistency Verification (Phase 2)
An automated assertion check was run inside `src/scripts/generate_report.py` to compare overlapping configurations:
*   **Target**: Check that the `CHIMERA` baseline results are identical to the `CHIMERA_Full` ablation results.
*   **Result**: **PASSED**. No cell deviation was found (assert array almost equal at $10^{-6}$ decimal place).
*   **Data Integrity Check**: 100% of the numbers reported in these tables map directly to the database of 1,040 rows in `results.csv`.
