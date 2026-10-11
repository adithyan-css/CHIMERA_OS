# Improvement pass: audit log

One section per improvement, written when that improvement was finished. Each records
what was done, the evidence, what it changes in the project's claims, how it was checked,
and what is still open. Sections are in the order the work was done, not the order of the
original list.

---

## 4. Recheck KOSMOS (done 12 Oct 2026)

**Question.** The v5 report shows KOSMOS at 19.8 % SLO violations and a 21.8 s p99 on the
real traces while using more CPU than any other controller. Is that the method or our
implementation?

**Finding: our tuning.** Two hypotheses were tested.

1. *Setpoint mismatch (partly true, not the cause).* KOSMOS regulates the mean response
   time to `alpha x SLO`; the evaluation counts individual requests above the SLO. The v5
   sweep only went down to `alpha = 0.5`. Extending it to 0.15
   (`src/scripts/kosmos_recheck.py`) barely helped: at `alpha = 0.15` the measured mean
   response time was 0.60 s against a setpoint of 0.075 s, at 456 core-min. The loop was
   not reaching its own setpoint at any alpha.
2. *Stale gains (the cause).* The gains `DC = 0.8, BC = 0.15` were tuned in August, before
   the load-sensor fix (`CODE_FIXES_LOG.md` item 9), when overload was hidden from every
   controller. The error `1/(alpha SLO) - 1/RT` ranges from about -7 to +2.9, so a
   proportional gain of 0.8 moves the allocation across its whole 0.5-4 core range in one
   5 s tick: the vertical loop was bang-bang.

**Fix.** Gains re-tuned by grid search on the **dev split only**
(`src/scripts/kosmos_retune.py`: 5 DC x 5 BC x 3 alpha, dev window, dev-only synthetic
traces, dev seeds; `results/kosmos_retune_dev.csv`). Chosen: `DC = 0.03, BC = 0.15`, with
`alpha = 0.25` as the default and alpha 0.15-0.70 as the frontier sweep. The control
structure is unchanged. The August gains stay available as `KOSMOS_AugustGains`.

Dev split, mean over 9 traces:

| Gains | alpha | SLO violations | mean RT (s) | CPU (core-min) |
|---|---:|---:|---:|---:|
| August (0.8, 0.15) | 0.70 | 16.4 % | 1.01 | 254 |
| August (0.8, 0.15) | 0.25 | 12.6 % | 0.82 | 323 |
| **Re-tuned (0.03, 0.15)** | **0.25** | **5.9 %** | **0.25** | 230 |
| AAPA, for scale | - | 7.1 % | 0.32 | 161 |

**Held-out result** (`results/REPORT_v5b.md`: the v5 runs with only the KOSMOS rows
replaced; CHIMERA and every other baseline are the same runs as in v5).

| Real traces, default settings | SLO violations | p99 (s) | CPU (core-min) |
|---|---:|---:|---:|
| KOSMOS, August gains | 19.76 % | 21.83 | 325 |
| KOSMOS, re-tuned | 8.71 % | 3.87 | 327 |
| CHIMERA v5 | 6.13 % | 0.83 | 244 |

On the held-out synthetic shapes re-tuned KOSMOS has **fewer** violations than CHIMERA at
default settings (3.42 % against 5.93 %) while using about twice the CPU (132 against 68
core-min). At equal CPU CHIMERA wins on 9 of 9 traces, mean gap +6.0 pp (p < 0.001).

**What this changes in the claims.**

| Claim in the v5 documents | Corrected |
|---|---|
| "26x better p99 than KOSMOS" | 4.7x (0.83 s against 3.87 s) |
| "+19.3 pp over KOSMOS at equal CPU" (3 traces overlapped) | +6.0 pp, on all 9 traces |
| "44 of 44 trace-baseline wins" | 51 of 51 (KOSMOS now overlaps CHIMERA's cost range on every trace) |
| KOSMOS loses everywhere | At default settings KOSMOS has fewer violations on the synthetic shapes, at twice the CPU |

**Checks.**

- The re-tuned loop reaches its setpoint: dev mean response time 0.25 s at a 0.125 s
  setpoint is not exact, but it is the same order, against 0.82 s for the August gains.
- Tuning used the dev split only; the held-out set was run once with the chosen gains.
- CHIMERA rows in `results_v5b.csv` are byte-for-byte the v5 rows.

**Still open.**

- The paper's PI structure was reimplemented from its description; it has not been
  checked against the authors' code.
- Re-tuned KOSMOS still changes CPU on about 250 of 360 ticks. Its paper does not use a
  dead-band, so none was added.
- The August fidelity check ("49.5 % pod-minute reduction against HPA") was made with the
  old gains and the old simulator and has not been repeated.

---

## 7. Literature review (done 12 Oct 2026, with stated limits)

**What was done.** Every reference in `final_research_package/literature.md` was looked up
online and the table rebuilt from the publishers' or authors' pages: 10 papers verified
(title, authors, venue, year), 5 of them new to the project.

**Findings that change the paper.**

1. **Both suspect references are confirmed missing.** No "Halcyon" paper at SOSP 2019 and
   no LQR horizontal-scaling paper at IEEE CLOUD 2024 could be found.
2. **The August MagicScaler entry was wrong** in authors, title and description. Correct
   record: Pan et al., PVLDB 16(12), 2023, evaluated on Alibaba Cloud clusters.
3. **Two close prior works were missing and weaken a novelty claim.**
   - *Themis* (Razavi et al., arXiv:2407.14843): in-place vertical scaling absorbs a surge
     first, then horizontal scaling takes over. This is the same idea as CHIMERA's
     vertical bridging. CHIMERA cannot claim "fast actuator first" as new.
   - *Ship et al.*, IEEE CLOUD 2024: optimal control of resources per replica and replica
     count together. CHIMERA cannot claim to be the first joint optimal-control
     formulation.
4. **MagicScaler's loss already trades cost against violation risk**, so risk-priced
   sizing has a precedent to cite and differentiate from.
5. **In-place pod resize became stable in Kubernetes 1.35** (December 2025); the documents
   said "beta, on by default from 1.33", which is true but dated.

The corrected positioning is written into `literature.md` ("Positioning, after this
review").

**Checks.** Each entry carries a source link. Entries not re-verified in this pass
(Shahrad, Romano, Bifet) are marked as such in the file.

**Limits: this is not yet a submission-grade review.**

- Abstract-level only; no full texts were read.
- A handful of targeted searches, not a systematic database search. Work from 2025-2026 on
  in-place resize is likely to exist and to have been missed.
- "No conformal autoscaler found" is a search result, not evidence of absence.
- The AAPA author names were not retrieved; the file says so.

---

## 10. AWS run (done 12 Oct 2026)

**What was done.** The operator is hosted on AWS and validated there.

- **Plan change.** The EKS plan was dropped: EKS charges for the control plane, the
  account is on the AWS Free plan (credits only), and its instance-type and 5-vCPU limits
  would have refused the nodes. The first EKS attempt failed at the Docker login step
  before creating anything billable; the empty image repository it made was deleted.
- **What runs instead.** One free-tier-eligible `t3.small` in ap-south-1 running k3s 1.33
  (`aws/ec2/`). First boot installs k3s, builds the operator image from the public
  repository and applies the same manifests as the local cluster.
- **Security.** No inbound ports; access through Systems Manager only; IMDSv2 required;
  the instance role has only `AmazonSSMManagedInstanceCore`.

**Evidence.** `results/live_ec2/LIVE_REPORT.md`: all five checks pass on Kubernetes
v1.33.13+k3s2 (in-place resize 0.25 to 0.37 cores with 0 restarts, scale-out 2 to 3 without
flapping, CPU release, status write-back, Prometheus data in 132 of 132 samples).

**What did not go smoothly.**

- The first validation run on AWS failed checks 2 and 4. Cause: two load generators
  produced only 0.37 cores of load against 0.5 allocated, which is inside the controller's
  10 % dead-band, so no resize was due. The run is kept
  (`results/live_ec2/run1_insufficient_load/`). Five generators exercised everything.
- The demo had to be shrunk to fit 2 vCPUs (pods 250m-500m, at most 3 replicas).

**Cost.** Usage is drawn from the Free plan's credits. Estimate: about 22 USD of credits a
month for the instance, a 20 GB disk and a public IPv4 address. The instance was switched
to `standard` CPU-credit mode after validation so it cannot accrue burst charges; in that
mode it is throttled if it stays above its baseline. Check the Billing console for the
real figure.

**Still open.**

- No budget alert is configured yet: it needs an email address to notify.
- The instance is single-node and has no restart automation beyond k3s itself; it is a
  demonstration host, not a production deployment.
- `aws/eks/` is kept for a paid account and has never run to completion.

---

## 1. A latency model the controller does not know (done 12 Oct 2026)

**Question.** CHIMERA v5 sizes capacity with `p(u) = exp(-k(1-u))`, which is exactly the
law the reported simulator generates violations from (exponential service times, slowdown
`1/(1-u)`). Does its advantage survive when the simulator follows a different law?

**What was done.** The simulator gained selectable service-time distributions
(exponential, lognormal, gamma, bimodal; all with mean 0.1 s) and slowdown laws (`inverse`,
`kingman`, `steep`): `--service-dist`, `--slowdown-model`. The held-out frontier sweep was
re-run in eight scenarios (`src/scripts/robustness.py`, 2,205 runs each, 5 seeds). CHIMERA
was not re-tuned for any of them. Full tables: `results/robustness/ROBUSTNESS.md`.

**Result.**

| Scenario | Trace-baseline wins at equal CPU | Mean gap (pp) | v5 against v4 (traces won, gap pp) |
|---|---:|---:|---|
| reported (exponential, inverse) | 42/42 | +5.90 | 9/9, +1.12 |
| lognormal service times | 42/42 | +5.41 | 9/9, +0.91 |
| gamma service times | 41/41 | +7.27 | 9/9, +1.01 |
| kingman slowdown | 42/42 | +4.49 | 9/9, +0.47 |
| steep slowdown | 42/42 | +3.30 | 9/9, +0.09 |
| lognormal + steep | 42/42 | +3.07 | 9/9, +0.09 |
| bimodal service times | 38/42 | +4.40 | 3/9, -0.06 |
| bimodal + kingman | 37/42 | +2.87 | 1/9, -0.17 |

**Reading.**

1. **The advantage over the baselines is not an artefact of the matching model.** It holds
   on every trace against every baseline in six of the eight scenarios, with a smaller
   margin when the slowdown law changes (+3.1 to +4.5 pp against +5.9).
2. **Under bimodal service times CHIMERA is no longer ahead of AAPA everywhere.** It loses
   to AAPA at equal CPU on 4 of 9 traces (by 0.08 to 0.72 pp) and to VPA on one. It still
   wins against HPA, HPA+VPA and the MagicScaler-inspired scaler on every trace.
3. **Risk sizing is the model-dependent part.** v5 is v4 plus risk sizing. Its gain over
   v4 is about +1 pp while the slowdown law matches, falls to +0.09 pp under the steep
   law, and turns slightly negative under bimodal service times. The rest of the design
   (cascade, joint control, replica band), which v4 already has, is what carries the
   robust part of the advantage.

**What this changes in the claims.**

- "Wins every trace-baseline comparison" must be stated for the reported latency model,
  with the robustness table next to it.
- Risk-optimal sizing should be presented as helping when the latency model is roughly
  right (about +1 pp), and as neutral to slightly harmful otherwise. It is not the main
  source of the advantage.

**Checks.**

- The `reported` scenario reproduces the v5 result with the same pipeline (42/42).
- Each distribution's mean was verified numerically (0.100 s in all four).
- Both test suites pass after the simulator change; the default model is unchanged, and
  `results_v5.csv` was not regenerated.

**Still open.**

- All scenarios are still analytic slowdown laws in the same simulator. A real service has
  none of these exactly; that is what the live replay (item 3) is for.
- KOSMOS was not included in the robustness sweep.
- `k` is still learned as SLO / mean service time. A model-free alternative (learning the
  violation curve from observed latencies) is the natural fix for point 3 and has not been
  tried.
