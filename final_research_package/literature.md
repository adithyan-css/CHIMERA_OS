# Related work

Second pass, 12 Oct 2026. Bibliographic details are from the publishers' or authors' own
pages. The "depth" column says how far each paper was read: **full** = the full text was
read for the points compared here; **abstract** = abstract and metadata only.

## Search protocol

Searches run (web search, English), then abstracts screened for: autoscaling of
containerised or serverless services on CPU, with either both scaling axes, in-place
resize, uncertainty-aware sizing, or a control-theoretic method.

| Query | Purpose |
|---|---|
| KOSMOS vertical and horizontal resource autoscaling for Kubernetes Baresi ICSOC 2021 | verify baseline |
| MagicScaler uncertainty-aware predictive autoscaling VLDB 2023 authors | verify baseline |
| arXiv 2507.05653 AAPA archetype-aware predictive autoscaler | verify base paper |
| joint horizontal vertical autoscaling Kubernetes in-place pod resize microservices 2024 2025 | new work |
| in-place pod resize autoscaling paper 2025 2026 hybrid horizontal vertical Kubernetes SLO microservices arXiv | new work |
| "Halcyon" joint horizontal vertical autoscaling serverless SOSP 2019 | verify a suspect reference |
| LQR optimal control horizontal pod autoscaling Kubernetes IEEE CLOUD 2024 | verify a suspect reference |
| conformal prediction autoscaling cloud resource provisioning uncertainty | novelty of conformal use |
| autoscaling "conformal prediction" Kubernetes / serverless / cloud capacity provisioning 2024-2026 | novelty of conformal use |
| SHOWAR; Autopilot; FIRM; "Optimizing simultaneous autoscaling"; "A Tale of Two Scales"; "Towards Serverless Optimization with In-place Scaling" | verify individual papers |
| public serverless function invocation trace dataset (Huawei, Azure 2021) | datasets |

This is a documented targeted search, not a systematic review: it used one search engine,
no citation chasing, and no database with complete coverage (IEEE Xplore, ACM DL, Scopus).
Do that before claiming completeness.

## Joint horizontal and vertical scaling

| Work | Venue | Depth | What it does | How CHIMERA differs |
|---|---|---|---|---|
| Baresi, Hu, Quattrocchi, Terracciano. *KOSMOS: Vertical and Horizontal Resource Autoscaling for Kubernetes* | ICSOC 2021, LNCS, pp. 821-829 | abstract + project README | Per-container control-theoretic planners resize containers on the fly; a per-node component resolves contention; a heuristic controller scales replicas; requires KEP-1287 | Two separately designed loops against one plan with an explicit in-flight model. Reimplemented as a baseline from the paper's description; the control law and periods are not in the README and were not re-checked against the paper's full text |
| Razavi, Salmani, Muhlhauser, Koldehofe, Wang. *A Tale of Two Scales: Reconciling Horizontal and Vertical Scaling for Inference Serving Systems* (Themis) | arXiv:2407.14843 | **full** | Two stages: in-place vertical scaling absorbs a surge, then horizontal scaling moves to 1-core instances once the load is stable. Decisions from an integer program solved by dynamic programming over SLO, batch size and cores; LSTM forecast with a 10 s horizon; vertical change under 100 ms, instance start 5-6 s. Per-request end-to-end latency SLO. Evaluated on two servers with Twitter traces against horizontal-only and vertical-only baselines; reports over 10x fewer SLO violations | Same "fast actuator first" idea, for ML inference pipelines with batching. Themis has no model of replicas in flight (start-up delay is handled by the transition procedure), no prediction intervals, and plans by enumeration over profiled latency. CHIMERA is a feedback law with an in-flight state, hands CPU back as replicas arrive, holds replicas inside a band, and sizes by risk over conformal scenarios |
| Ship, Shindin, Wang, Arroyo, Tantawi. *Optimizing Simultaneous Autoscaling for Serverless Cloud Computing* | IEEE CLOUD 2024, arXiv:2310.19013 | **full** | Applications as graphs of functions in a multi-class queueing network; a fluid approximation solved as a separated continuous linear program gives resources per replica and number of replicas over fixed time intervals. Evaluated in a simpy simulation in abstract time units against threshold autoscaling: 15 % to over 300 % better response time and failures | Planned allocation from a fluid model, with re-computation, against closed-loop feedback every 5 s. The paper has no replica start-up delay, no in-place resize latency, and no forecast uncertainty; demand rates are given |
| Rzadca et al. *Autopilot: Workload Autoscaling at Google* | EuroSys 2020 | abstract | Horizontal and vertical scaling from usage history and tuned heuristics to reduce slack between limits and usage; 23 % slack against 46 % for manual limits | Fleet-scale right-sizing for slack and OOM rate, not a latency objective |
| Baarzi, Kesidis. *SHOWAR: Right-Sizing and Efficient Scheduling of Microservices* | SoCC 2021, pp. 427-441 | abstract | Vertical sizing from variance of historical usage; horizontal scaling with control-theoretic ideas on kernel metrics; affinity hints | Separate mechanism per axis; no in-place resize |
| Razavi, Ghafouri, Muhlhauser, Jamshidi, Wang. *Sponge: Inference Serving with Dynamic SLOs Using In-Place Vertical Scaling* | EuroMLSys 2024 | abstract | In-place vertical scaling to follow changing SLOs | Vertical only |
| Wang, Li, Liang, Wu, Yan. *Autothrottle: A Practical Bi-Level Approach to Resource Management for SLO-Targeted Microservices* | NSDI 2024 | abstract | An application-level learned controller sets per-service CPU-throttle targets; per-service heuristic controllers meet them. Up to 26 % CPU saved against the best baseline | Vertical CPU only, for multi-service applications; a bi-level structure like CHIMERA's cascade, with learning in place of a model |
| Hsieh, Chou. *Towards Serverless Optimization with In-place Scaling* | arXiv:2311.09526 | abstract | Studies Kubernetes v1.27 in-place resize for serverless cold starts | Evidence for the actuator |
| Hamzeh, Vahdatian. *MAS-H2: A Hierarchical Multi-Agent System for Holistic Cloud-Native Autoscaling* | arXiv:2603.07607 (2026) | abstract | Three layers of agents plan pod and node scaling jointly from forecasts; GKE prototype against HPA and Cluster Autoscaler | Pods and nodes, not replicas and pod size |

## Predictive and uncertainty-aware scaling

| Work | Venue | Depth | What it does | How CHIMERA differs |
|---|---|---|---|---|
| Zhang, Vippagunta, Nandagopal, Raman, Xu, Pfeiffer, Chatterjee, Tan, Guo, Jiang. *AAPA: An Archetype-Aware Predictive Autoscaler with Uncertainty Quantification for Serverless Workloads on Kubernetes* | arXiv:2507.05653 (no venue stated on v3) | **full** | A LightGBM classifier assigns each 60-minute window to SPIKE, PERIODIC, RAMP or STATIONARY; each archetype has a CPU target and cooldown; beta-calibrated confidence widens margins. simpy simulator, 500 ms SLO, Azure Functions 2019. Up to 50 % fewer SLO violations than HPA at 2-8x the resource use under spikes | Base simulator and strongest horizontal baseline. Horizontal only |
| Pan, Wang, Zhang, Yang, Cheng, Chen, Guo, Wen, Tian, Dou, Zhou, Yang, Zhou, Yang. *MagicScaler: Uncertainty-aware, Predictive Autoscaling* | PVLDB 16(12), pp. 3808-3821, 2023 | **full** (for the points here) | A multi-scale attentive Gaussian-process predictor gives demand with uncertainty; the scaler minimises a loss with stochastic constraints trading instance running cost against QoS-violation risk. Evaluated on three Alibaba Cloud clusters. The text has no mention of vertical scaling or instance start-up time | The nearest precedent for risk-priced sizing. Number of instances only. Our "MagicScaler-style" baseline implements the idea (uncertainty-aware replica choice), not the authors' predictor or code |
| Liu, Li, Farkiani, Crowley. *BACC: Budget-Aware Calibration and Control for Horizontal Autoscaling* | arXiv:2606.20575 (May 2026) | abstract | Wraps any forecaster with adaptive conformal inference and steers provisioning with a PI controller on the consumption of a violation budget. Five Azure Functions traces; simulation and Kubernetes replay; compliance gap about 0.4 pp | **Conformal prediction inside an autoscaler's loop already exists.** BACC is horizontal only and targets a compliance level; CHIMERA uses conformal scenarios to price risk across two actuators |
| Qiu, Banerjee, Jha, Kalbarczyk, Iyer. *FIRM: An Intelligent Fine-Grained Resource Management Framework for SLO-Oriented Microservices* | OSDI 2020 | abstract | Localises the microservice causing an SLO violation and reprovisions it with reinforcement learning | Different mechanism |

## Workloads and building blocks

| Work | Venue | Depth | Used for |
|---|---|---|---|
| Joosen, Hassan, Asenov, Singh, Darlow, Wang, Barker. *How Does It Function? Characterizing Long-term Trends in Production Serverless Workloads* | SoCC 2023 | abstract + data release notes | Source of the Huawei production serverless traces (check the author list against the ACM record) |
| Shahrad et al. *Serverless in the Wild* | USENIX ATC 2020 | not re-verified | Source of the Azure Functions 2019 traces |
| Internet Traffic Archive, NASA-HTTP and ClarkNet-HTTP | LBNL | data pages read | Web-server traces |
| Romano, Patterson, Candes. *Conformalized Quantile Regression* | NeurIPS 2019 | not re-verified | Forecast intervals |
| Bifet, Gavalda. *Learning from Time-Changing Data with Adaptive Windowing* | SDM 2007 | not re-verified | ADWIN |
| Kubernetes KEP-1287, In-Place Update of Pod Resources | alpha v1.27, on by default from v1.33, stable v1.35 (Dec 2025) | Kubernetes blog read | The fast actuator. One arXiv paper gives the beta release as v1.31; the Kubernetes blog says v1.33 |

## What CHIMERA can and cannot claim

1. **Not new:** using in-place resize before scaling out (Themis; KOSMOS makes vertical
   primary); optimising both axes together (Ship et al.); trading cost against violation
   risk under forecast uncertainty (MagicScaler); conformal calibration in an autoscaler
   (BACC, for the horizontal axis).
2. **What is specific to CHIMERA**, as far as this search found:
   - a feedback law whose state carries replicas in flight, for the pair of a sub-second
     and a tens-of-seconds actuator;
   - handing CPU back as ordered replicas arrive, and a replica band that leaves the
     replica count alone while CPU resizing can absorb the change;
   - conformal demand scenarios used to price risk in a capacity rule that reduces to a
     utilisation target when demand is certain;
   - an evaluation with a dev/held-out split, equal-cost frontier comparison, and reported
     negative results.
3. **Phrase claims as "to our knowledge".** The search was targeted, and work on in-place
   resize is appearing quickly now that the feature is stable.

## References removed from the August draft

- "Halcyon: Joint Horizontal and Vertical Autoscaling for Serverless Functions",
  SOSP 2019: not found in two searches.
- "LQR-Based Optimal Feedback Control for Horizontal Pod Scaling in Container
  Orchestrators", IEEE CLOUD 2024: not found. The real CLOUD 2024 paper on optimal control
  of both axes is Ship et al.
- The August MagicScaler entry had the wrong authors, title and description.

## Still to do

- Full text of KOSMOS (the PDF was not machine-readable from the repository page) and of
  Autopilot, SHOWAR, Autothrottle and BACC.
- A database search with citation chasing.
- Check the Joosen et al. author list and the three "not re-verified" entries.

## Sources

- KOSMOS: https://re.public.polimi.it/handle/11311/1203576 and https://github.com/lterrac/system-autoscaler
- Themis: https://arxiv.org/html/2407.14843v1
- Ship et al.: https://arxiv.org/pdf/2310.19013
- Autopilot: https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/
- SHOWAR: https://pure.psu.edu/en/publications/showar-right-sizing-and-efficient-scheduling-of-microservices/
- Sponge and Themis author records: https://dblp.org/pid/281/7200
- Autothrottle: https://arxiv.org/abs/2212.12180
- In-place scaling for serverless: https://arxiv.org/pdf/2311.09526
- MAS-H2: https://arxiv.org/abs/2603.07607
- AAPA: https://arxiv.org/html/2507.05653v3
- MagicScaler: https://www.vldb.org/pvldb/vol16/p3808-yang.pdf
- BACC: https://arxiv.org/abs/2606.20575
- FIRM: https://www.usenix.org/conference/osdi20/presentation/qiu
- Huawei traces: https://github.com/sir-lab/data-release
- In-place pod resize status: https://kubernetes.io/blog/2025/12/19/kubernetes-v1-35-in-place-pod-resize-ga
- Microservice autoscaling survey: https://arxiv.org/html/2507.17128
- Predictive autoscaling taxonomy: https://arxiv.org/abs/2606.07046
