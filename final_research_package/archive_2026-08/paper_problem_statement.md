# Paper Evidence: Problem Statement

Autoscaling containerized microservices in cloud environments requires dynamically adjusting both the horizontal dimension (pod replica counts, $h_t$) and the vertical dimension (CPU core limits per pod, $v_t$). 

In standard container orchestrators like Kubernetes, Horizontal Pod Autoscalers (HPA) and Vertical Pod Autoscalers (VPA) operate as independent, uncoordinated control loops. HPA adjusts replicas based on average pod utilization metrics, while VPA resizes CPU core allocations in response to historical pod resource usage. Because these loops are uncoordinated and execute asynchronously, they frequently trigger conflicting scaling decisions:
*   An HPA scale-up event dilutes individual pod utilization, causing VPA to scale down pod sizes.
*   A VPA scale-up event increases pod capacities, reducing average utilization and triggering an HPA scale-down.

This interference generates **resource allocation chattering**, response time violations, and cluster instability, degrading Service Level Objectives (SLOs) while inflating cloud infrastructure costs.
