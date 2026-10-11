# Related work

## Verified references

| Work | Venue | What it does | Relation to CHIMERA |
|---|---|---|---|
| AAPA: archetype-aware predictive autoscaling | arXiv:2507.05653 | Horizontal scaling from workload archetypes with uncertainty | Base simulator and strongest baseline; CHIMERA adds the vertical actuator and joint control |
| KOSMOS (Baresi et al.) | ICSOC 2021 | Vertical PI control at 5 s plus horizontal heuristics at 300 s, with in-place resize | Closest joint scaler; reimplemented as a baseline |
| MagicScaler | VLDB 2023 | Uncertainty-aware predictive horizontal scaling | Baseline ("MagicScaler-inspired": our reimplementation of the idea, not the authors' code) |
| Serverless in the Wild (Shahrad et al.) | USENIX ATC 2020 | Azure Functions 2019 trace characterisation | Source of the real traces |
| Kubernetes HPA, VPA | Kubernetes project | Stock horizontal and vertical autoscalers | Baselines |
| KEP-1287, in-place pod resize | Kubernetes project | Change CPU of a running pod without restart; on by default from 1.33 | The fast actuator CHIMERA relies on |
| Conformalized quantile regression (Romano et al.) | NeurIPS 2019 | Distribution-free prediction intervals | Forecast intervals |
| ADWIN (Bifet and Gavalda) | SDM 2007 | Adaptive-window drift detection | Regime-change detection |

Check author lists, titles and page numbers against the published versions before
submission; this table was not built from the papers' own bibliographic records.

## References removed

The August literature table (`archive_2026-08/complete_literature_comparison.md`) also
listed:

- "Halcyon: Joint Horizontal and Vertical Autoscaling for Serverless Functions",
  SOSP 2019;
- "LQR-Based Optimal Feedback Control for Horizontal Pod Scaling in Container
  Orchestrators", IEEE CLOUD 2024.

Neither could be confirmed to exist, and the MagicScaler entry there carried an author
list and title that do not match the VLDB paper. Do not cite these two unless you locate
the actual papers.

## Positioning

- Against **horizontal-only predictive scalers**: CHIMERA spends uncertainty on a 0.1 s
  actuator instead of on standing replicas.
- Against **KOSMOS**: both use in-place resize. KOSMOS separates the loops by timescale
  (5 s vertical, 300 s horizontal) with independent controllers; CHIMERA plans both from
  one target with an explicit model of in-flight replicas, and sizes capacity by risk.
  KOSMOS makes about 253 resizes per run in our simulator against CHIMERA's 62.
- Against **HPA+VPA**: one controller, so the loops cannot fight.
