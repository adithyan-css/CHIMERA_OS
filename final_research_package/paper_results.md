# Results

Source: `results/REPORT_v5.md` (3,510 main runs, 3,780 sweep runs, held-out evaluation).

## 1. Equal CPU: the primary comparison

Each baseline's SLO violation rate is interpolated at CHIMERA's CPU cost, trace by trace.
Positive gap = CHIMERA has fewer violations.

| Set | vs | Traces won | Mean gap (pp) | p (Holm) |
|---|---|---:|---:|---:|
| real | AAPA | 5/5 | +2.17 | <0.001 |
| real | HPA | 5/5 | +5.47 | <0.001 |
| real | HPA+VPA | 5/5 | +4.55 | <0.001 |
| real | MagicScaler-inspired | 5/5 | +5.45 | <0.001 |
| real | VPA | 3/3 | +3.56 | <0.001 |
| real | KOSMOS | 3/3 | +19.30 | <0.001 |
| synthetic | AAPA | 4/4 | +5.40 | <0.001 |
| synthetic | HPA | 4/4 | +8.04 | <0.001 |
| synthetic | HPA+VPA | 4/4 | +6.59 | <0.001 |
| synthetic | MagicScaler-inspired | 4/4 | +7.22 | <0.001 |
| synthetic | VPA | 3/3 | +12.67 | <0.001 |

Only traces where the two cost ranges overlap are compared, which is why VPA and KOSMOS
have fewer than five. Across all per-trace frontier comparisons CHIMERA wins 44 of 44.
The closest is AAPA on `real_kswd_w3` (+0.74 pp).

## 2. Default settings, five real traces

| Controller | SLO violations | p99 (s) | CPU (core-min) | Actions/run |
|---|---:|---:|---:|---:|
| **CHIMERA v5** | **6.13 %** | **0.83** | 244.3 | 62.1 |
| AAPA | 6.92 % | 0.91 | 236.8 | 7.4 |
| HPA+VPA | 14.06 % | 1.73 | 194.8 | 22.8 |
| HPA | 15.60 % | 3.65 | 190.8 | 18.0 |
| MagicScaler-inspired | 16.78 % | 2.69 | 181.1 | 19.2 |
| VPA | 19.40 % | 4.79 | 169.2 | 16.4 |
| KOSMOS | 19.76 % | 21.83 | 324.8 | 252.6 |

Against AAPA, the strongest baseline: 0.79 pp fewer violations (p = 0.012) for a CPU
difference of +7.5 core-min that is not significant (p = 0.215). At these settings CHIMERA
has the lower seed-mean on 3 of the 5 traces; the 5-of-5 result is the equal-CPU one.

## 3. Default settings, four held-out synthetic shapes

| Controller | SLO violations | p99 (s) | CPU (core-min) |
|---|---:|---:|---:|
| **CHIMERA v5** | **5.93 %** | **0.83** | 68.0 |
| AAPA | 7.02 % | 2.98 | 101.9 |
| HPA+VPA | 13.29 % | 3.22 | 65.2 |
| KOSMOS | 14.11 % | 22.47 | 168.3 |
| MagicScaler-inspired | 14.41 % | 7.24 | 63.2 |
| HPA | 15.13 % | 8.54 | 63.7 |
| VPA | 15.16 % | 2.55 | 78.7 |

Against AAPA: 1.09 pp fewer violations (p = 0.010) with 33 % less CPU (p < 0.001).

## 4. Progress across versions

| Version | Real SLO | Real CPU | Synthetic SLO | Synthetic CPU |
|---|---:|---:|---:|---:|
| v1 (Review 2) | 11.82 % | 227.0 | 14.27 % | 81.9 |
| v2 | 7.06 % | 235.7 | 6.91 % | 89.1 |
| v3 | 7.35 % | 227.8 | 6.93 % | 85.5 |
| v4 | 7.39 % | 230.5 | 6.73 % | 79.1 |
| **v5** | **6.13 %** | 244.3 | **5.93 %** | **68.0** |

All versions were re-run on the same held-out data. At equal CPU v5 beats v4 and v2 on 9
of 9 traces.

## 5. Tail latency

CHIMERA's p99 on the real traces is 0.83 s: 1.1x better than AAPA (0.91 s), 4.4x better
than HPA (3.65 s) and 26x better than KOSMOS (21.83 s).

Earlier versions of this work reported a roughly 19x advantage over AAPA. That figure was
mostly AAPA starting cold; with the warm-up protocol it is 1.1x. All documents use the
corrected figure.

## 6. Where CHIMERA is not better

- **Actuation count.** 62 in-place resizes per run, against about 7 pod changes for AAPA.
  Each resize is a 0.1 s in-place operation with no restart, but it is API traffic.
- **Lowest reachable violation rate.** At the expensive end of the frontier v2-v4 reach
  2.6 % on the real traces (at about 400 core-min); v5 reaches 4.0 % at 303. v5's sweep
  does not extend as far into high spend.
- **Without in-place resize** (`CHIMERA_NoInPlace`) the synthetic-trace violation rate
  rises to 10.19 %. The design depends on the fast actuator.
