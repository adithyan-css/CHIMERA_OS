# Paper Evidence: Limitations

We identify three primary limitations of the CHIMERA autoscaling framework:
1.  **ML Predictor Extrapolation Boundaries**: Tree-based regression models (LightGBM) cannot extrapolate outside their training targets, making CQR bounds vulnerable under sudden spikes. This necessitates hybrid reactive fallbacks, which reduce the purely predictive nature of the framework.
2.  **Transient Latency under Spikes**: Due to container image pull and pod initialization delays (20.0s startup latency), dynamic controllers that scale CPU sizes dynamically to minimize cost will experience higher initial SLO violations during sudden spikes compared to static headroom allocations (such as HPA or AAPA which run with a high static capacity floor).
3.  **Kubernetes Operator Telemetry Integration**: While the operator CRD and reconciliation loops are fully prototyped, real-cluster telemetry metrics and in-place resizing actions are mocked due to KEP-1287 runtime limitations in local docker-in-docker environments.
