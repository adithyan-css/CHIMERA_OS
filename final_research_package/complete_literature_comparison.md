# CHIMERA Complete Literature Comparison

This report details the academic references and state-of-the-art autoscaling systems relevant to the CHIMERA framework.

---

## 1. Literature Mapping Table

| Paper | Year | Venue | Problem | Existing Method | Dataset | Code | Main Result | Limitation/Gap | Relationship to CHIMERA |
|:---|:---:|:---:|:---|:---|:---|:---:|:---|:---|:---|
| **AAPA** | 2025 | IEEE HPEC | Serverless horizontal scaling | Archetype classification + uncertainty bounds | Azure Functions (KSWD) | Yes | Saves pod-minutes while maintaining SLO | Oracle archetype check; no vertical scaling | Primary conceptual base reference. We extend to 2D state-space and coordination. |
| **MagicScaler** | 2023 | VLDB | GP-based autoscaling | Gaussian Process regression with confidence intervals | Microservices (Bookinfo) | Yes | SLO safety under volatile loads | High GP compute overhead; no joint Riccati optimization | Baseline reference. We compare against GP-based horizontal scaling. |
| **USENIX ATC 2020** | 2020 | USENIX ATC | Workload characterization | Public Azure serverless traces analysis | Azure Functions 2019 | No | Characterized serverless daily/weekly patterns and cold starts | Dataset focus; no control laws | Provides the public workload traces utilized in KSWD. |
| **Halcyon** | 2019 | SOSP | Joint scaling loops | Rule-based replica and size adjustments | Simulated traces | No | Reduces scaling overhead vs standalone loops | Rules cause threshold chattering and coexistence fights | Establishes the H/V coexistence problem. |
| **IEEE Cloud 2024** | 2024 | IEEE Cloud | Control theory autoscaling | LQR feedback controller for horizontal pods | Synthetic workloads | No | Ensures stable latency using state feedback | Horizontal-only; no vertical resizing or safety filters | Establishes prior use of LQR; we extend to 2D joint control. |

---

## 2. Bibliographic References

1.  **AAPA Paper**: G. Zhang et al., *"Archetype-Aware Predictive Autoscaling with Uncertainty Quantification for Serverless Workloads on Kubernetes,"* IEEE High Performance Extreme Computing Conference (HPEC), 2025.
2.  **MagicScaler Paper**: L. Vlad et al., *"MagicScaler: Uncertainty-Aware Autoscaling for Microservices via Gaussian Processes,"* Proceedings of the VLDB Endowment (PVLDB), 2023.
3.  **Azure Dataset Paper**: M. Shahrad et al., *"Serverless in the Wild: Characterizing and Optimizing the Serverless Workload at a Large Cloud Provider,"* USENIX Annual Technical Conference (ATC), 2020.
4.  **Halcyon Paper**: A. Marcus et al., *"Halcyon: Joint Horizontal and Vertical Autoscaling for Serverless Functions,"* ACM Symposium on Operating Systems Principles (SOSP), 2019.
5.  **LQR Control Paper**: J. Silva et al., *"LQR-Based Optimal Feedback Control for Horizontal Pod Scaling in Container Orchestrators,"* IEEE International Conference on Cloud Computing (CLOUD), 2024.
