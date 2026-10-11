# Related work

Checked on 12 Oct 2026 against the publishers' or authors' own pages (links below). Titles,
authors, venues and years are as listed there. "What it does" is summarised from abstracts
unless noted; the full texts were not read for this pass, so re-check any specific number
before quoting it.

## Joint horizontal and vertical scaling

| Work | Venue | What it does | Relation to CHIMERA |
|---|---|---|---|
| Baresi, Hu, Quattrocchi, Terracciano. *KOSMOS: Vertical and Horizontal Resource Autoscaling for Kubernetes* | ICSOC 2021, LNCS, pp. 821-829 | Control-theoretic planners resize each container on the fly; a per-node component resolves contention; a heuristic controller handles replicas | Closest prior system and a baseline here. KOSMOS separates the two loops by timescale; CHIMERA plans both from one target with an explicit model of in-flight replicas |
| Rzadca et al. *Autopilot: Workload Autoscaling at Google* | EuroSys 2020 | Fleet-scale horizontal and vertical scaling from usage history and tuned heuristics, to reduce the slack between limits and usage | Shows both axes are scaled together in production; objective is slack and OOM rate, not a latency SLO |
| Baarzi, Kesidis. *SHOWAR: Right-Sizing and Efficient Scheduling of Microservices* | SoCC 2021, pp. 427-441 | Vertical sizing from the variance of historical usage; horizontal scaling with control-theoretic ideas on kernel metrics; affinity hints for the scheduler | Two separate mechanisms per axis; no in-place resize, no delay model |
| Ship, Shindin, Wang, Arroyo, Tantawi. *Optimizing Simultaneous Autoscaling for Serverless Cloud Computing* | IEEE CLOUD 2024 (arXiv:2310.19013) | Optimal control policy for resources per replica and number of replicas, from a fluid model solved as a separated continuous linear program; evaluated in a simpy simulation | Closest in spirit: one optimisation over both axes. Differs in method (fluid LP against delay-augmented LQR plus cascade) and has no uncertainty-priced sizing |
| Razavi, Salmani, Muhlhauser, Koldehofe, Wang. *A Tale of Two Scales: Reconciling Horizontal and Vertical Scaling for Inference Serving Systems* (Themis) | arXiv:2407.14843 | In-place vertical scaling absorbs a surge first, then horizontal scaling takes over once the workload settles | Same "fast actuator first" idea as CHIMERA's bridging, for inference serving. **Must be cited and differentiated** |
| Razavi, Ghafouri, Muhlhauser, Jamshidi, Wang. *Sponge: Inference Serving with Dynamic SLOs Using In-Place Vertical Scaling* | EuroMLSys 2024 | In-place vertical scaling to meet changing SLOs | Vertical only |
| Hsieh, Chou. *Towards Serverless Optimization with In-place Scaling* | arXiv:2311.09526 | Studies the Kubernetes v1.27 in-place resize feature for serverless cold starts | Evidence for the actuator CHIMERA relies on |

## Predictive and uncertainty-aware scaling

| Work | Venue | What it does | Relation to CHIMERA |
|---|---|---|---|
| Authors at George Washington University, Workday and Youngstown State University (take the names from the arXiv record). *AAPA: An Archetype-Aware Predictive Autoscaler with Uncertainty Quantification for Serverless Workloads on Kubernetes* | arXiv:2507.05653 | Classifies workloads into four archetypes and applies a scaling strategy per archetype with calibrated confidence; releases a simulator and dataset | Base simulator and the strongest baseline here; horizontal only |
| Pan et al. *MagicScaler: Uncertainty-aware, Predictive Autoscaling* | PVLDB 16(12), pp. 3808-3821, 2023 | Multi-scale attentive Gaussian-process demand forecast feeding a scaler whose loss trades running cost against QoS-violation risk | The "MagicScaler-inspired" baseline is our reimplementation of the idea, not the authors' code. Its cost-against-risk loss is the nearest prior to CHIMERA's risk sizing; horizontal only |
| Qiu, Banerjee, Jha, Kalbarczyk, Iyer. *FIRM: An Intelligent Fine-Grained Resource Management Framework for SLO-Oriented Microservices* | OSDI 2020 | Finds the microservice causing an SLO violation and reprovisions it with reinforcement learning | Fine-grained resource control for latency SLOs; different mechanism |

## Building blocks

| Work | Venue | Used for |
|---|---|---|
| Shahrad et al. *Serverless in the Wild: Characterizing and Optimizing the Serverless Workload at a Large Cloud Provider* | USENIX ATC 2020 | Source of the Azure Functions 2019 traces |
| Romano, Patterson, Candes. *Conformalized Quantile Regression* | NeurIPS 2019 | Forecast intervals |
| Bifet, Gavalda. *Learning from Time-Changing Data with Adaptive Windowing* | SDM 2007 | ADWIN drift detection |
| Kubernetes. *In-Place Pod Resize* (KEP-1287) | alpha in v1.27, beta and on by default in v1.33, stable in v1.35 | The fast vertical actuator |
| Kubernetes Horizontal Pod Autoscaler and Vertical Pod Autoscaler | Kubernetes project | Baselines |

The three "building blocks" paper entries (Shahrad, Romano, Bifet) were not re-verified
online in this pass; they are standard references, but check the details before submission.

## Positioning, after this review

1. **"Vertical first, then horizontal" is not new.** Themis (2024) does it for inference
   serving, and KOSMOS (2021) makes vertical the primary loop. CHIMERA should not claim
   the idea. What it adds is (a) a single delay-augmented control law that knows which
   replicas are still booting, (b) hand-back of CPU as replicas arrive and a replica band
   that avoids replica changes CPU can absorb, and (c) capacity sized by pricing SLO risk
   over conformal demand scenarios.
2. **Joint optimisation of both axes is not new either.** Ship et al. (CLOUD 2024) pose it
   as an optimal control problem solved with a fluid LP. The paper should compare
   formulations and say why a feedback law plus cascade suits a 0.1 s / 20 s actuator
   pair.
3. **Risk-priced sizing has a precedent** in MagicScaler's cost-against-violation loss.
   CHIMERA's version differs in using an explicit latency model, conformal scenarios, and
   a price that reduces to a utilisation target when demand is certain.
4. **Conformal prediction in autoscaling appears to be little explored**; a search found
   conformal methods for VM right-sizing and runtime prediction but no conformal
   autoscaler. This is a search result, not proof of absence: do a proper database search
   (IEEE Xplore, ACM DL, Scopus) before writing "first".

## References removed from the August draft

- "Halcyon: Joint Horizontal and Vertical Autoscaling for Serverless Functions",
  SOSP 2019: no such paper was found in two searches.
- "LQR-Based Optimal Feedback Control for Horizontal Pod Scaling in Container
  Orchestrators", IEEE CLOUD 2024: no such paper was found. The real CLOUD 2024 paper on
  optimal control of both axes is Ship et al., above.
- The August MagicScaler entry had the wrong authors, title and method description
  ("L. Vlad et al.", Gaussian processes on Bookinfo). The correct record is above; the
  authors evaluated on Alibaba Cloud clusters.

## Still to do for a submission-grade review

- Read the full text of KOSMOS, Themis, Ship et al. and MagicScaler, not only abstracts.
- Systematic search for 2024-2026 work on in-place resize and hybrid scaling; the feature
  became stable in December 2025 and new work is likely.
- Surveys to mine for missed work, e.g. "Auto-scaling Approaches for Microservice
  Applications: A Survey and Taxonomy" (arXiv:2507.17128).

## Sources

- KOSMOS: https://re.public.polimi.it/handle/11311/1203576
- Autopilot: https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/
- SHOWAR: https://pure.psu.edu/en/publications/showar-right-sizing-and-efficient-scheduling-of-microservices/
- Ship et al.: https://arxiv.org/pdf/2310.19013
- Themis: https://arxiv.org/pdf/2407.14843
- Sponge and Themis author records: https://dblp.org/pid/281/7200
- In-place scaling for serverless: https://arxiv.org/pdf/2311.09526
- AAPA: https://arxiv.org/pdf/2507.05653
- MagicScaler: https://www.vldb.org/pvldb/vol16/p3808-yang.pdf
- FIRM: https://www.usenix.org/conference/osdi20/presentation/qiu
- In-place pod resize status: https://kubernetes.io/blog/2025/12/19/kubernetes-v1-35-in-place-pod-resize-ga
- Conformal right-sizing (context only): https://arxiv.org/pdf/2607.24773
- Microservice autoscaling survey: https://arxiv.org/pdf/2507.17128
