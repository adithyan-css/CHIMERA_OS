# Code Fixes Log

Changes made to `src/core/chimera_controller.py`, `src/core/chimera_simulator.py`, and
`src/scripts/run_full_evaluation.py` during the novelty-audit / verification pass, with
rationale. Full detail and empirical impact of each fix is in
`CHIMERA_CODE_VERIFICATION_ADDENDUM.md` (Sections 4-8). Git commit `3b999c5` contains
the actual diff.

1. **target_utilization asymmetry (0.60 -> 0.70).** `ChimeraSimulator` was constructed
   with `target_utilization=0.60` while every baseline used `0.70`, giving CHIMERA an
   unearned headroom advantage. Fixed to match.

2. **v_thresh quantization (0.15 -> 0.05).** The Lyapunov safety filter's corrections to
   the vertical actuator were smaller than the actuation-quantization threshold, so the
   filter's effect on applied actions was almost always dropped. Loosened the threshold
   so genuine corrections survive quantization.

3. **vertical_scale_cooldown (15.0 -> 60.0, now configurable).** Tuned alongside (2).

4. **use_scale_down_guardrail wiring bug.** This flag was read from the ablation config
   dict everywhere except the one place it mattered: the `ChimeraController(...)`
   constructor call inside `run_simulation()` never passed it through, so
   `CHIMERA_w/o_Anti_Chattering` silently ran identically to `CHIMERA_Full` regardless of
   the ablation config. Fixed by adding
   `use_scale_down_guardrail=config.get('use_scale_down_guardrail', True)` to the
   constructor call.

5. **New `decoupled_safety` flag on `ChimeraController`.** Implements a true structural
   decoupling (independent per-axis Lyapunov certificates, no joint capacity constraint,
   kappa/eta auto-zeroed) for a KOSMOS-style baseline, used to directly test whether the
   joint LQR/DARE solve provides coordination benefit beyond the safety-projection
   penalty term. Adds the `CHIMERA_Decoupled` config. Result: negative (see addendum
   Section 8) - reported honestly, not hidden.

6. **CHIMERA_DEBUG_LYAP instrumentation hook (env-gated, no-op by default).** Added to
   `chimera_simulator.py` to trace the Lyapunov filter's raw pre-quantization action
   whenever its activation counter fires, used to resolve why `CHIMERA` and
   `CHIMERA_w/o_Lyapunov` appeared bit-identical on an earlier (later discarded)
   incremental dataset. Does not affect behavior unless the env var is set.

All results reported in the final novelty audit and paper draft come from a clean,
single-pass 504-row rerun (21 configs x 8 workloads x 3 seeds) generated entirely under
this code state, replacing an earlier incrementally-checkpointed dataset that mixed rows
from pre- and post-fix code (a real methodological issue found and corrected during this
pass - see addendum Section 6).

7. **KOSMOS baseline (`src/core/kosmos_simulator.py`, NEW).** Faithful reimplementation
   of Baresi et al. (ICSOC 2021): vertical PI controller in inverse-response-time space,
   KOSMOS's own 5s vertical / 300s horizontal periods, KHPA saturation + UTIL heuristics,
   proportional Contention Manager. PI gains tuned by grid sweep on a held-out workload
   subset and a disjoint seed (DC=0.8, BC=0.15, alpha=0.70) so the baseline competes at
   its best. Fidelity check: reproduces 49.5% pod-minute reduction vs HPA, inside the
   paper's own reported 44-57% range. Also supports `use_inplace_resize=False` to test
   the actuation-cost sensitivity hypothesis (result: hypothesis rejected, see addendum
   Section 10d).

Headline outcome: CHIMERA loses to KOSMOS in aggregate (-6.6pp) but wins on the four
externally-sourced real Azure traces (+4.9pp, p=0.007, d=-1.45) with a 6.4x better p99
tail and 19x less actuation churn, while collapsing on the four self-authored synthetic
stress traces (-18.1pp). See addendum Section 10d.

8. **Restored `w_0003` to the real-trace set.** `get_remediation_workloads()` silently
   excluded it with no documented reason; it is a legitimate non-duplicate trace
   (corr with w_0001 = -0.03). The headline real-trace win survives its restoration
   (CHIMERA vs KOSMOS: -3.81pp, p=0.014/0.035, d=-1.07 at n=15). Use the 5-trace
   numbers; the 4-trace numbers should not appear in the paper.

KNOWN BLOCKING LIMITATION: all five real traces are 100-minute EXCERPTS of 14-day
traces (dataset/EXAMPLE_DATA_NOTE.md). The full KSWD dataset is 10,148 workloads and
requires downloading Azure Functions 2019 + running dataset/tools/azure_to_k8s_converter.py
on a machine with open internet access (the Azure blob host is not reachable from the
dev environments used here). See addendum Section 10e.
