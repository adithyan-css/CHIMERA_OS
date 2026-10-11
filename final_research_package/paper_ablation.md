# Ablation

Source: `results/REPORT_v5.md`. Each row removes ("w/o") or re-adds ("+") one mechanism of
CHIMERA v5. "Identical" is the share of runs whose SLO, CPU, p99 and action counts match
the full controller exactly; 100 % means the mechanism changes nothing.

## Mechanisms that matter

| Variant | Real SLO | Real CPU | Synth. SLO | Synth. CPU | Resizes/run | Pod changes/run |
|---|---:|---:|---:|---:|---:|---:|
| **Full v5** | 6.13 % | 244.3 | 5.93 % | 68.0 | 60.4 | 0.4 |
| w/o fast release | 2.33 % | 455.3 | 2.57 % | 176.8 | 1.1 | 0.4 |
| w/o joint H+V (horizontal only) | 5.04 % | 264.3 | 4.74 % | 105.6 | 12.4 | 12.4 |
| w/o cascade | 5.90 % | 252.9 | 6.30 % | 72.6 | 35.9 | 0.4 |
| w/o inner loop owning CPU | 5.90 % | 248.5 | 5.75 % | 68.4 | 87.2 | 0.4 |
| w/o scale-up dead-band | 5.93 % | 247.9 | 5.73 % | 68.7 | 81.5 | 0.4 |
| w/o replica band | 6.12 % | 246.8 | 5.88 % | 70.3 | 67.1 | 7.6 |
| w/o CQR | 6.51 % | 235.1 | 5.83 % | 68.0 | 66.1 | 0.4 |

Reading the table:

- **Fast release** is the largest single effect: without it CPU nearly doubles on the real
  traces and is 2.6x on the synthetic ones. The lower violation rate in that row is bought
  with that CPU, so it is a different operating point, not a better controller.
- **Joint control** saves 8 % CPU on the real traces and 36 % on the synthetic ones
  compared with horizontal-only, and cuts pod changes from 12.4 to 0.4 per run.
- **Inner loop owning CPU** and the **scale-up dead-band** each remove about a quarter of
  the resizes at almost no cost in SLO.
- **Replica band** cuts pod changes from 7.6 to 0.4 per run.
- **CQR** shifts the cost/SLO trade-off (0.38 pp fewer violations for 4 % more CPU on the
  real traces).

Several rows show a lower violation rate than the full controller at a higher CPU cost.
Rows are single operating points; whether a variant is better or worse is decided on the
frontier, which is why the main comparison is at equal CPU.

## Small effects

| Variant | Real SLO | Real CPU | Identical |
|---|---:|---:|---:|
| w/o ADWIN drift | 6.26 % | 239.5 | 8 % |
| w/o ADWIN margin reset | 6.24 % | 240.2 | 8 % |
| + online conformal recalibration | 6.21 % | 241.1 | 0 % |
| w/o reactive conformal margin | 6.12 % | 244.4 | 0 % |
| w/o OOD fallback | 6.12 % | 244.4 | 2 % |
| w/o state augmentation | 6.14 % | 244.6 | 67 % |
| legacy (two-slot) delay model | 6.14 % | 244.1 | 61 % |

Risk sizing supersedes the reactive margin and the OOD fallback: both mattered in v2-v4
and change almost nothing in v5.

The in-flight state has little effect in v5 because the replica band makes replica
changes rare (0.4 per run), so there is seldom anything in flight. It mattered in v2,
before the band existed.

## Inert mechanisms (negative results)

| Variant | Identical |
|---|---:|
| w/o coupling penalty | 100 % |
| legacy coupling form | 100 % |
| + Lyapunov runtime filter | 100 % |
| w/o anti-chattering guardrail | 100 % |
| w/o vertical bridging flag | 97 % |

- The **coupling penalty** penalises opposing moves of the two actuators. With the inner
  loop owning CPU and the replica band holding replicas, opposing moves do not arise.
- The **Lyapunov filter** enforces a decrease of `V = e'Pe` that the LQR law already
  guarantees. It fires 0.01 times per run when enabled.
- The **anti-chattering guardrail** is subsumed by the replica band.

These were headline contributions of the August draft. They are kept in the code for
ablation and are not claimed as contributions.
