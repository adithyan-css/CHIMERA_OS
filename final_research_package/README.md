# CHIMERA research package

Paper material for CHIMERA v5, rewritten on 11 Oct 2026. Every simulation number traces to
`results/REPORT_v5.md`; every live-cluster statement to `results/live/LIVE_REPORT.md`.

| File | Paper section |
|---|---|
| `paper_problem_and_gap.md` | Introduction: problem, gap, what is no longer claimed |
| `paper_contributions.md` | Contributions, each with its evidence |
| `paper_methodology.md` | Method |
| `paper_experimental_setup.md` | Setup, splits, statistics |
| `paper_results.md` | Results |
| `paper_ablation.md` | Ablation, including the inert mechanisms |
| `paper_live_validation.md` | Live-cluster validation |
| `paper_discussion_and_limitations.md` | Discussion, limitations, threats to validity |
| `paper_conclusion.md` | Conclusion and future work |
| `literature.md` | Related work; two unverifiable August references removed |

`archive_2026-08/` holds the August package. It describes the pre-fix system (CHIMERA at
25.8 % violations, a mocked operator, the coupling penalty and Lyapunov filter as headline
contributions) and must not be used as a source.

## Readiness

| Area | State |
|---|---|
| Method and implementation | complete |
| Simulation evidence | complete: held-out evaluation, equal-CPU frontier, ablation |
| Baselines | corrected and tuned; KOSMOS reimplemented and checked against its paper |
| Live system | functional validation on Kubernetes 1.33 (kind); no performance measurement |
| Real-trace breadth | **weak**: five 100-minute excerpts, reused across design rounds |
| Related work | needs a proper literature pass; see `literature.md` |
| Paper text | section drafts as bullet-level prose; not yet a formatted manuscript |

The two items a reviewer is most likely to press on are the small set of real traces and
the absence of measured performance on a real cluster. Both are stated in the limitations.
