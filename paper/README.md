# Paper

`chimera.pdf` is the manuscript in IEEE conference format, built from `chimera.tex`.

Rebuild (needs LaTeX with the IEEEtran class, and the Python packages in
`requirements.txt`), from the repository root:

```bash
python paper/make_figures.py
```

```bash
python paper/make_replay_tex.py
```

```bash
cd paper && pdflatex chimera.tex && pdflatex chimera.tex
```

- `make_figures.py` draws the figures in `figures/` from the result files.
- `make_replay_tex.py` writes `replay_numbers.tex`, the real-cluster paragraph and table,
  from `results/replay3/`. Its wording follows the measured outcome.
- Every table in the paper is copied from a generated report: `results/REPORT_v6.md`
  (held-out set), `results/REPORT_hw.md` (serverless traces), `results/REPORT_ext.md`
  (web traces), `results/robustness/ROBUSTNESS.md`.

Before submitting: put in the author block, check the venue's page limit and template
(this is the generic IEEEtran conference style), and re-check the reference details marked
in `final_research_package/literature.md`.
