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

---

## 6. Remove the inert components (done 12 Oct 2026)

**Question.** The coupling penalty, the Lyapunov filter, the anti-chattering guardrail and
the vertical-bridging flag change no decision in v5. Can they come out?

**What was done.**

- `ChimeraController` gained a `lean` path: the LQR action is clipped to the physical
  bounds and the capacity budget, with no coupling penalty, no Lyapunov filter and no
  action grid (`src/core/chimera_controller.py`, `project_to_safe_set`).
- **Check before switching.** v5 was re-run on the held-out set through the lean path with
  the guardrail off (`CHIMERA_Lean`), and again with the bridging flag off as well
  (`CHIMERA_Lean_NoBridging`): 90 runs each (9 traces x 10 seeds).

| Variant | Runs identical to v5 (SLO, CPU, p99, action counts) |
|---|---:|
| v5, re-run | 90 / 90 (reproduces `results_v5.csv` exactly) |
| lean: penalty, filter, grid and guardrail removed | **90 / 90** |
| lean, bridging flag also off | **90 / 90** |

  Evidence: `results/lean_check.csv`.
- **Switched.** The reported controller (v6, see item 8) and the Kubernetes operator now
  run the lean path. The operator no longer evaluates the grid or the penalty at all.

**What was deliberately not deleted.**

- The legacy code path stays in the controller class. CHIMERA v1 to v5 and the ablation
  rows that *re-add* each component need it, and those rows are the evidence that the
  components are inert. The ablation now reads "+ coupling penalty", "+ Lyapunov filter",
  "+ anti-chattering guardrail" instead of "without".
- The operator keeps its own rule "never remove replicas while ordered ones are still
  booting". It is cheap, and the first live run showed that real boot times do not match
  the simulator's.
- The vertical-bridging flag stays on. It is inert in the lean path, but harmless, and the
  operator's fast loop sizes CPU for the replicas that are serving in any case.

**Side finding.** With the action grid in place, turning bridging off changed 3 % of v5
runs. In the lean path it changes none. The 3 % came from the grid's quantisation, not
from bridging.

**Bug found while switching the operator.** A unit test failed with the wider dead-band
and exposed a sizing flaw in the operator, not the test: on a freshly started operator, a
forecast upper bound from before a load jump was used as a demand scenario even though it
was below the demand observed now, and with few scenarios it pulled the allocation to
about 65 % of the observed demand. Fixed (`_risk` in `k8s/operator_core.py`: the bound is a
scenario only at or above the observed demand; fewer than five history steps fall back to
one zero step, as the simulator does), with a regression test. The simulator has the same
structure but is always started with a primed history, where the effect is diluted over 40
scenarios; it was left unchanged so the evaluated controller is not altered.

**Checks.** Both test suites pass (18 and 30 tests).

**Still open.** The legacy path is dead weight for anyone who only wants the final
controller. A release would split it into a separate module.

---

## 8. Reduce resize churn (done 12 Oct 2026)

**Question.** v5 makes about 60 in-place CPU resizes per 30-minute run. Can that be cut
without giving up the cost/SLO advantage?

**Dev study** (`src/scripts/churn_dev.py`, dev split only, 15 variants x 5 settings x 9
traces x 3 seeds; `results/churn_dev.csv`). Three inner-loop knobs were varied: the
scale-up dead-band, the release band and the release ticks. "Gap" is the SLO difference to
v5 at equal CPU on each variant's own frontier (positive = better than v5).

| Scale-up dead-band (release 15 %, 2 ticks) | Resizes/run | Gap to v5 at equal CPU (pp) | Traces not worse |
|---|---:|---:|---:|
| 10 % (v5) | 52.6 | 0.00 | 9/9 |
| 15 % | 43.0 | +0.18 | 9/9 |
| 20 % | 37.6 | +0.29 | 8/9 |
| 25 % | 32.7 | +0.25 | 8/9 |
| **30 %** | **29.0** | **+0.22** | **9/9** |
| 40 % | 23.0 | +0.18 | 7/9 |
| 50 % | 18.9 | -0.07 | 3/9 |

Widening the release band or adding release ticks also cut resizes but cost efficiency on
every trace (for example release 25 %, 3 ticks: 21.9 resizes, -0.84 pp, 0 of 9 traces not
worse), because CPU is then held longer.

**Rule, fixed before the held-out run:** the widest scale-up dead-band whose dev frontier
is no worse than that of v5 on at least 8 of 9 traces and on average. That is 30 %. The
default setting moves from 0.50 to 0.46 by the rule v5 itself used (the cheapest setting
whose dev SLO is no worse than the previous default: 5.06 % against 5.35 % for v5).

**The result is CHIMERA v6** = v5 + the lean path (item 6) + a 30 % scale-up dead-band.

**Confirmation on data the choice did not see.**

| Set | Resizes/run v5 -> v6 | SLO v5 -> v6 | CPU v5 -> v6 (core-min) | v6 against v5 at equal CPU |
|---|---|---|---|---|
| Held-out, 5 Azure traces | 62.1 -> 30.8 | 6.13 % -> 5.93 % | 244.3 -> 251.5 | +0.09 pp |
| Held-out, 4 synthetic shapes | 58.2 -> 34.6 | 5.93 % -> 5.63 % | 68.0 -> 71.8 | +0.89 pp |
| External, 10 web-server traces | 53.4 -> 26.1 | 6.41 % -> 6.18 % | 184.6 -> 190.2 | +0.01 pp (6 of 10 traces) |

Resizes are halved on all three sets. At equal CPU v6 is level with v5 on the real traces
and somewhat better on the synthetic shapes.

**Against the baselines, v6 on the held-out set** (`results/REPORT_v6.md`): 47 of 48
trace-baseline comparisons won at equal CPU (v5 with the corrected KOSMOS: 51 of 51). The
one loss is new: the MagicScaler-inspired scaler on `real_kswd_w2`, by 0.66 pp. All
baselines remain significantly behind on the pooled test (p < 0.001).

**What this changes in the claims.**

- "About 62 resizes per run" becomes about 31, against about 7 pod changes for AAPA and
  about 250 resizes for KOSMOS.
- "Wins every comparison on the held-out set" is no longer true for v6: 47 of 48.

**Honest accounting of the held-out set.** The held-out set has now been scored twice: once
for the v5 verdict and once for v6. The v6 change was chosen on dev by a rule written
before the run, and nothing was adjusted after it, but the set is no longer untouched. The
external traces (item 2) had never been used for anything when v6 was run on them.

**Still open.** The API-server and kubelet load of 30 resizes per half hour per workload
at fleet scale is still unmeasured.

---

## 2. More real traces (done 12 Oct 2026)

**Question.** The only real workloads were five 100-minute Azure Functions excerpts, reused
across every design round. Does the result hold on real traces no design decision saw?

**Data.** The Azure Functions 2019 archive is still not downloadable (HTTP 403 and 409 on
both known URLs). Two public web-server logs were downloaded from the Internet Traffic
Archive at LBNL, whose pages state "the traces may be freely redistributed":

| File | Size | Content |
|---|---:|---|
| `NASA_access_log_Jul95.gz` | 20.7 MB | NASA Kennedy Space Center WWW server, July 1995 |
| `clarknet_access_log_Aug28.gz` | 21.8 MB | ClarkNet, 28 Aug - 3 Sep 1995 |

`dataset/tools/ita_to_traces.py` turns them into ten 100-minute per-minute traces
(`dataset/external/`). The windows are fixed by calendar rule in that script (NASA: 14:00
on six July dates; ClarkNet: four set times), written before any controller ran on them.
The raw logs are not committed; the script rebuilds the traces from them.

**A first attempt was discarded, and why.** The first run scaled the request rates of both
sites by 5. On every ClarkNet window the horizontal-only baselines sat at their 30-replica
limit (900 pod-minutes in a 30-minute window) and could not add capacity, so the 56 of 58
"wins" CHIMERA scored there said nothing. Those results are kept in
`results/ext_first_attempt_capped/` and are not used. The scales were then set by a rule
(the busiest minute must fit what 30 half-core replicas serve at the cheapest sweep
setting: NASA x3, ClarkNet x1.5) and everything was re-run. This rescaling happened after
seeing the first results; it was done to remove a handicap on the baselines, not to help
CHIMERA, and the comparison became harder for CHIMERA as a result.

**Result** (`results/REPORT_ext.md`; 900 main runs, 3,150 sweep runs; CHIMERA v6 with
unchanged settings).

Equal CPU, trace by trace:

| vs | Traces won | Mean gap (pp) | p (Holm) |
|---|---:|---:|---:|
| AAPA | 10/10 | +2.00 | <0.001 |
| HPA | 10/10 | +4.53 | <0.001 |
| HPA+VPA | 10/10 | +3.89 | <0.001 |
| KOSMOS (re-tuned) | 10/10 | +1.27 | <0.001 |
| MagicScaler-inspired | 10/10 | +4.66 | <0.001 |
| VPA | 10/10 | +2.20 | <0.001 |

60 of 60 trace-baseline comparisons won.

Default settings:

| Controller | SLO violations | p99 (s) | CPU (core-min) | Actions/run |
|---|---:|---:|---:|---:|
| CHIMERA v6 | 6.18 % | 0.83 | 190.2 | 26.1 |
| AAPA | 7.45 % | 1.18 | 193.6 | 7.8 |
| KOSMOS | 4.22 % | 0.83 | 271.9 | 270.6 |
| HPA | 16.19 % | 2.93 | 148.9 | 18.9 |

**Reading.**

1. On ten real traces that no design round saw, CHIMERA has fewer violations than every
   baseline at equal CPU on every trace. This is the strongest single piece of evidence in
   the project, because nothing about the controller was chosen with these traces in view.
2. Against AAPA at default settings: 1.27 pp fewer violations with slightly less CPU.
3. **KOSMOS is the closest competitor** once properly tuned: only 1.27 pp behind at equal
   CPU, and at default settings it has fewer violations than CHIMERA (4.22 % against
   6.18 %) for 43 % more CPU and ten times the resizes.
4. Risk sizing adds little here: v6 against v4 (no risk sizing) at equal CPU is +0.24 pp,
   better on 7 of 10 traces. Consistent with item 1.

**Limits.**

- These are 1995 web-server logs, not serverless-function traces: real, public and
  untouched, but a different kind of workload from the Azure excerpts.
- Two sites, ten windows. A 100-minute window is still short.
- Rates are rescaled to fit the simulated cluster, as with the Azure excerpts.
- Still simulation.

---

## 3. Measure real performance on a cluster (done 12 Oct 2026; result is mixed)

**Question.** The live runs so far only showed that the operator works. How does CHIMERA
compare with the Kubernetes HPA on a real cluster, with latency measured at the client?

**What was built.**

- `k8s/replay/loadgen.py`: an open-loop load generator that runs in the cluster. It sends
  requests at the rate a trace dictates whether or not earlier ones have returned (Poisson
  arrivals), measures latency at the client, and exports its request counters to
  Prometheus.
- `k8s/replay/run_replay.py`: replays the same trace against each controller from the same
  starting state, samples allocated CPU every 5 s, and writes
  `results/replay/REPLAY_REPORT.md`.
- metrics-server on the cluster for the HPA; a Prometheus scrape job for the generator.

**Setup.** kind, one node, Kubernetes 1.33.1, on a 12-core laptop. Service:
`registry.k8s.io/hpa-example`, measured at 61 ms of CPU per request (from the container CPU
counter over 600 requests). SLO 0.30 s, which is 5 x the service time, the ratio the
simulator uses. Trace: 24 minutes, 12 from the Azure trace `w_0005` scaled to a mean of
0.6 cores, then a flash crowd to 3 cores. Three unscored lead-in minutes. Client timeout
10 s, counted as a violation. One run per controller, one seed.

**Result.**

| Controller | SLO violations | CPU (core-min) | Steady 16 min: violations / CPU | Spike 8 min: violations / CPU |
|---|---:|---:|---|---|
| HPA, 70 % target | 51.1 % | 72.8 | 23.7 % / 38.8 | 66.8 % / 34.0 |
| HPA, 50 % target | 35.5 % | 77.1 | 18.4 % / 40.4 | 45.2 % / 36.8 |
| CHIMERA, CPU signal | 33.2 % | 83.4 | 19.9 % / 32.9 | 41.0 % / 50.5 |
| CHIMERA, request-rate signal | 28.6 % | 81.8 | 23.5 % / 34.2 | 31.6 % / 47.7 |

Zero container restarts in all four runs. CHIMERA made 18 and 24 in-place resizes and 9
replica changes; the HPA made 12 and 9 replica changes.

**Reading. This does not confirm the simulator's margin, and it does not contradict it.**

1. **Steady load: CHIMERA is cheaper for about the same violations.** It used 15 to 19 %
   less CPU than either HPA setting. With the CPU signal it had fewer violations than the
   HPA at 70 % (19.9 % against 23.7 %) and slightly more than the HPA at 50 % (18.4 %).
2. **Spike: CHIMERA has fewer violations but spends more.** With the request-rate signal it
   cut spike violations to 31.6 % against 45.2 % for the better HPA setting, using 30 %
   more CPU in that phase. Part of that is structural: the HPA is limited to 10 pods of
   half a core (5 cores), while CHIMERA can also grow the pods and peaked near 15 cores for
   a 3-core demand, because a saturated CPU reading is boosted while a backlog drains.
3. **Overall: fewest violations, highest cost.** CHIMERA with the request-rate signal has
   6.9 points fewer violations than the HPA at 50 % for 6 % more CPU. With two HPA points
   there is no frontier to interpolate on, so an equal-cost win cannot be claimed from
   this run.
4. **Every controller violates heavily.** A 0.3 s objective on a 61 ms request is very
   tight for pods throttled at half a core: a single request already exceeds one CFS quota
   period. The simulator's 5 to 6 % violation rates are not what this service delivers at
   the same ratio. This is a statement about the simulator's fidelity as much as about any
   controller.

**Operator problems the replay exposed (all fixed, each with a regression test).**

- The demo service was OOM-killed under open-loop load (128 Mi; now 512 Mi). Closed-loop
  generators had hidden this.
- With the request-rate signal, a cost per request set 20 % too low made the operator
  believe it was at 75 % utilisation while the pods were saturated. The demand estimate is
  now never below what measured CPU implies.
- A cost per request given in the spec was being overwritten by online learning, and the
  learning itself is biased low while load rises (1-minute CPU rate against a 30-second
  request rate). A configured value is now fixed.
- A pod added while a backlog drained was removed 45 s later. Scale-in is now held for
  120 s after a scale-out.
- With the 30 % dead-band and a 70 % target the operator did not act until about 91 %
  utilisation. The replay runs CHIMERA at its evaluated default (0.46), where it acts at
  about 60 %.

**Limits. Treat the numbers as indicative only.**

- One run per controller: no repetition, no confidence interval. Differences of a few
  points could be noise.
- A laptop, one node, one synthetic CPU-bound service, one 24-minute trace.
- The HPA was run with stock behaviour settings and a 10-replica limit; a larger limit
  would have helped it in the spike.
- No comparison with VPA, AAPA or KOSMOS on the cluster: none of them has an operator here.

**Still open.** Repeat with several seeds and a third HPA setting to get an actual
frontier; use a service whose latency is not dominated by CFS throttling (or pods of at
least one core); let the simulator's slowdown law be fitted to measurements like these.

---

## 5. Speed up the operator's reaction (done 12 Oct 2026)

**Question.** In the first live runs the CPU allocation took 2 to 5 minutes to climb. Does
a request-rate signal fix that?

**What was done.** The operator already accepted a request-rate query; it had never been
run live. The replay (item 3) drives it from the load generator's request counter over a
30 s window, next to the default 1-minute CPU-usage rate, on the same trace.

**Result, flash crowd starting at minute 16** (`results/replay/timeline_*.csv`):

| Controller | Seconds until 2 cores allocated | Until 4 cores | Spike-phase violations |
|---|---:|---:|---:|
| HPA, 70 % | 60 | 105 | 66.8 % |
| CHIMERA, CPU signal | 45 | 80 | 41.0 % |
| CHIMERA, request-rate signal | 20 | 60 | 31.6 % |

(The HPA at 50 % already held 2 cores when the spike began; it reached 4 cores after 75 s.)

The request-rate signal reaches each allocation level 20 to 25 s sooner than the CPU
signal, and in the two minutes after the peak it had 19 % and 4 % violations against 53 %
and 16 %. The remaining delay is mostly the signal window: 30 s of rate history has to
fill before the estimate reflects a step.

**What it cost.** In steady load the request-rate run had more violations than the CPU run
(23.5 % against 19.9 %) for slightly more CPU. With one run each this may be noise; it may
also be that the rate signal, converted with a fixed cost per request, sizes a little
tighter than measured CPU does.

**Checks.** The three operator fixes this needed (see item 3) each have a regression test;
32 operator tests pass.

**Still open.**

- "Seconds" is not reached: 20 s to the first level is the 30 s window plus a 5 s loop. A
  shorter window, or an arrival-count surge test like the one the simulator uses, is the
  next step.
- In a real deployment the request rate would come from an ingress or service mesh, not
  from the load generator.
- The cost per request has to be configured; learning it online is biased while load is
  rising.

---

## 9. Write the manuscript (draft done 12 Oct 2026)

**What was done.** `paper/CHIMERA_paper.md`: one continuous draft with abstract,
introduction, related work, design, evaluation method, held-out results, generalisation
(external traces and other latency models), the real-cluster section, discussion and
limitations, conclusion, references, and a table mapping every results section to the file
its numbers come from.

**How it differs from the earlier section drafts.**

- Written for v6 and the corrected KOSMOS baseline; no claim rests on a v5-only number
  except Table 5, which says so.
- Novelty is narrowed to what the literature review supports: it states that Themis and
  Ship et al. come first on "vertical first" and on joint optimisation.
- Negative results are in the body, not an appendix: the inert components, the
  model-dependence of risk pricing, KOSMOS winning at default settings, the discarded first
  external-trace run, and the inconclusive live comparison.

**Checks.** Each number in Tables 1 to 6 was copied from the generated reports named in
the source table and re-read against them once after writing.

**What it is not.**

- Not formatted for a venue; no figures are placed in the text and there is no
  architecture figure.
- Related work is built from abstracts (item 7).
- Not proof-read by a second person.
- `final_research_package/` still holds the v5 section drafts; the manuscript supersedes
  them.

---

## Summary of the pass

| # | Improvement | Outcome |
|---|---|---|
| 1 | Unknown latency model | Advantage over baselines holds in 6 of 8 scenarios fully, 37-38 of 42 in the other two; risk pricing is model-dependent |
| 2 | More real traces | Ten new real traces; 60 of 60 comparisons won at equal CPU; a first unfair attempt discarded |
| 3 | Real performance on a cluster | Measured; fewest violations at highest cost against the HPA; inconclusive at matched cost |
| 4 | Recheck KOSMOS | Baseline was mis-tuned; corrected; advantage over it shrinks from 19.3 to 5.8 points (held-out) |
| 5 | Faster operator reaction | Request-rate signal is 20-25 s faster than the CPU signal after a spike; not yet "seconds" |
| 6 | Remove inert components | Out of the decision path; 90 of 90 check runs identical |
| 7 | Literature review | Ten papers verified; two close prior works added; two unverifiable references removed; abstract-level only |
| 8 | Reduce churn | Resizes halved (62 to 31 per run) with no loss at equal CPU |
| 9 | Manuscript | Complete draft, unformatted |
| 10 | AWS run | Hosted on one free-tier EC2 instance; all five checks pass |

Bugs found and fixed along the way, each with a regression test: a pod counted twice when
it boots faster than assumed; sizing below observed demand on a cold start; the rate-based
demand estimate falling below measured CPU; a configured cost per request overwritten by
learning; scale-in straight after scale-out. Baseline and protocol errors found and
corrected: stale KOSMOS gains; external traces scaled past the baselines' replica limit.

---

# Second pass (12 Oct 2026): the four weak points

## W4. Real serverless traces (done)

**Weak point.** The fresh real traces were 1995 web-server logs, not serverless workloads.

**What was done.** The Azure Functions archives are still not downloadable (2019 and 2021:
HTTP 403 and 409). The Huawei production FaaS release is: `requests_minute.zip` (40.2 MB,
CC BY 4.0, from the links in github.com/sir-lab/data-release; Huawei private cloud, 200
functions, requests per minute over 141 days; Joosen et al., SoCC 2023).
`dataset/tools/huawei_to_traces.py` builds ten traces by a rule written before any run:
the first released day with index 30 or more, minutes 600-699, and the ten lowest function
IDs with no missing minute and at least 60 requests per minute. The functions differ in
volume by five orders of magnitude, so each window is scaled so that its busiest minute is
12 requests/second, the largest peak the horizontal-only baselines can serve at the
cheapest sweep setting (the lesson of the first web-trace attempt, applied up front).

**Result** (`results/REPORT_hw.md`; 900 main runs, 3,150 sweep runs; unchanged controllers).

| vs | Traces won at equal CPU | Mean gap (pp) | p (Holm) |
|---|---:|---:|---:|
| AAPA | 10/10 | +2.02 | <0.001 |
| HPA | 10/10 | +3.99 | <0.001 |
| HPA+VPA | 10/10 | +2.72 | <0.001 |
| KOSMOS | 10/10 | +2.38 | <0.001 |
| MagicScaler-style | 10/10 | +4.37 | <0.001 |
| VPA | 9/9 | +1.77 | <0.001 |

57 of 57 per-trace frontier comparisons won. Default settings: CHIMERA 6.06 % violations at
251 core-min; AAPA 9.18 % at 230; KOSMOS 6.16 % at 364 (not significantly different from
CHIMERA in violations, p = 0.41, for 45 % more CPU).

**Reading.** With the web traces this makes twenty real traces from three sites that no
design step used: 117 of 117 comparisons won at equal CPU. Risk sizing again adds little
(v6 against v4: +0.17 pp, better on 5 of 9 traces).

**Limits.** One 100-minute window per function, one day, one provider. Rates are rescaled
to a common peak, so absolute volumes are not the production ones. Still simulation.

## W2. Literature review (done, to a documented but not systematic standard)

**Weak point.** Abstract-level only; no systematic search.

**What was done.**

- Full texts read for the points being compared: Themis (arXiv HTML), Ship et al. and
  MagicScaler (PDF text extracted and searched), AAPA (arXiv HTML).
- The search protocol is written down: every query, and the screening criterion
  (`final_research_package/literature.md`, "Search protocol").
- Two further targeted searches for 2025-2026 work on in-place resize and on conformal
  methods in autoscaling.

**What it changed.**

1. **BACC (arXiv, May 2026) already uses adaptive conformal inference inside a horizontal
   autoscaler.** CHIMERA can no longer be described as the first conformal autoscaler; the
   paper says so.
2. Themis, read in full: no model of replicas in flight, no prediction intervals, decisions
   by dynamic programming over profiled latency. The comparison in the paper is now
   specific.
3. Ship et al., read in full: no replica start-up delay, no in-place resize, demand rates
   given; a planned allocation, not a feedback loop.
4. MagicScaler, read in full for these points: instances only; no vertical scaling or
   start-up time in the text.
5. AAPA: full author list retrieved; no venue is stated on the arXiv record, so the "HPEC
   2025" venue in the August files is unconfirmed and was dropped.
6. Added: Autothrottle (NSDI 2024), MAS-H2 (2026), the Huawei trace paper.

**Limits.** Still one search engine and no citation chasing; KOSMOS's full text could not
be read by machine (the comparison rests on its abstract and project README); Autopilot,
SHOWAR, Autothrottle and BACC are abstract-level. The file lists exactly what remains.

## W3. Manuscript (done)

**Weak point.** An unformatted draft with no figures placed.

**What was done.** `paper/chimera.tex` and `paper/chimera.pdf`: six pages in the IEEE
conference format, with an architecture figure, a three-panel frontier figure, a
robustness figure, six tables, numbered references and cross-references. Figures are
generated from the result files (`paper/make_figures.py`); the real-cluster paragraph and
table are generated from the replay results (`paper/make_replay_tex.py`), so the text
cannot drift from the data. LaTeX (MiKTeX) was installed to build it. The pages were
rendered and inspected: no unresolved references, two small overfull boxes.

**What it is not.** The author block is a placeholder. The class is generic IEEEtran, not
a specific venue's template or page limit. Related work for five of the cited papers is
abstract-level (W2). It has not been proof-read by anyone else. The markdown draft was
removed in favour of this one.
