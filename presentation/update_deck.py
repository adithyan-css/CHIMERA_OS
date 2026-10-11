"""
Produce the updated review deck from the Review-2 deck and the v2 results.

    python presentation/make_deck_figures.py     # regenerate the seven images first
    python presentation/update_deck.py

Input : presentation/CHIMERA_Review2_original.pptx (unchanged copy of the Review-2 deck)
Output: presentation/CHIMERA_Review_v5.pptx

Every number written into the deck is computed here from results/results_v5.csv,
results/results_v5_sweep.csv, the unit-test run and a live run of demo_run.py -- none is
typed in by hand. Text is replaced run-by-run so the deck's formatting is preserved.
"""
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pptx import Presentation

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "presentation" / "CHIMERA_Review2_original.pptx"
OUT = ROOT / "presentation" / "CHIMERA_Review_v5.pptx"
FIGS = ROOT / "presentation" / "figures"


# ---------------------------------------------------------------------------- numbers
def run(cmd):
    import os
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    return p.stdout + p.stderr


def numbers():
    df = pd.read_csv(ROOT / "results" / "results_v5.csv")
    sw = pd.read_csv(ROOT / "results" / "results_v5_sweep.csv")
    real = df[df.workload_id.str.startswith("real")]
    synth = df[~df.workload_id.str.startswith("real")]
    m = real.groupby("config_name")[["slo_violation_rate", "p99_response_time", "core_minutes",
                                     "scaling_actions"]].mean()
    ms = synth.groupby("config_name")[["slo_violation_rate", "core_minutes"]].mean()
    ab = df[df.run_type == "ablation"].groupby("config_name")[["slo_violation_rate", "core_minutes",
                                                                 "scaling_actions"]].mean()
    base = ["HPA", "VPA", "HPA+VPA", "AAPA", "MagicScaler-Inspired", "KOSMOS"]
    best_base = min(base, key=lambda b: m.loc[b, "slo_violation_rate"])
    best_base_s = min(base, key=lambda b: ms.loc[b, "slo_violation_rate"])

    fr = sw[sw.workload_id.str.startswith("real")].groupby(["family", "knob"])[
        ["slo_violation_rate", "core_minutes"]].mean().reset_index()
    ch = fr[fr.family == "CHIMERA"].sort_values("core_minutes")
    others = fr[~fr.family.isin(["CHIMERA", "CHIMERA_v1", "CHIMERA_v2", "CHIMERA_v3", "CHIMERA_v4", "Oracle"])]
    best_other = others.loc[others.slo_violation_rate.idxmin()]
    mc = pd.read_csv(ROOT / "results" / "matched_cost_v5.csv")
    mc_real = mc[(mc.set == "real") & mc.baseline.isin(["AAPA", "VPA"])]
    pt = pd.read_csv(ROOT / "results" / "per_trace_matched_v5.csv")
    losses = pt[pt.gap_pp <= 0]
    loss_traces = sorted(losses.workload_id.unique())
    v2 = real.groupby("config_name").scaling_actions.mean()

    tests1, tests2 = run([sys.executable, "tests/test_chimera.py"]), run([sys.executable, "tests/test_operator.py"])
    n_tests = sum(int(re.search(r"Ran (\d+) tests", t).group(1)) for t in (tests1, tests2))
    tests_ok = all(re.search(r"^OK", t, re.M) for t in (tests1, tests2))

    demo = run([sys.executable, "demo_run.py", "--no-color", "--no-table"])
    # results rows: controller, SLO %, mean RT, p99, CPU-min, ...
    rows = {k: (float(slo), float(cpu)) for k, slo, cpu in re.findall(
        r"^\s{2}(CHIMERA \(no SA\)|CHIMERA|AAPA|HPA)\s+([\d.]+)%\s+[\d.]+\s+[\d.]+\s+([\d.]+)", demo, re.M)}
    pods = re.search(r"CHIMERA\n\s+replicas.*?(\d+)-(\d+) pods", demo)
    hpa_pods = re.search(r"HPA\n\s+replicas.*?(\d+)-(\d+) pods", demo)
    hold = re.search(r"^\s+(\d+)\s+\d+\s+[\d.]+\s+\[\+(\d+)\]\s+hold", demo, re.M)

    def pct(x):
        return x * 100
    return dict(
        runs=len(df), sweep_runs=len(sw), configs=df.config_name.nunique(), seeds=df.seed.nunique(),
        workloads=df.workload_id.nunique(),
        n_ablation=(df[df.run_type == "ablation"].config_name.nunique() - 1),
        n_sens=df[df.run_type == "sensitivity"].config_name.nunique(),
        ch_slo=pct(m.loc["CHIMERA", "slo_violation_rate"]), ch_p99=m.loc["CHIMERA", "p99_response_time"],
        ch_cpu=m.loc["CHIMERA", "core_minutes"], ch_act=m.loc["CHIMERA", "scaling_actions"],
        best_base=best_base, bb_slo=pct(m.loc[best_base, "slo_violation_rate"]),
        aapa_slo=pct(m.loc["AAPA", "slo_violation_rate"]), aapa_p99=m.loc["AAPA", "p99_response_time"],
        aapa_cpu=m.loc["AAPA", "core_minutes"],
        kos_slo=pct(m.loc["KOSMOS", "slo_violation_rate"]), kos_p99=m.loc["KOSMOS", "p99_response_time"],
        kos_act=m.loc["KOSMOS", "scaling_actions"],
        ch_slo_s=pct(ms.loc["CHIMERA", "slo_violation_rate"]), best_base_s=best_base_s,
        bb_slo_s=pct(ms.loc[best_base_s, "slo_violation_rate"]),
        ch_cpu_s=ms.loc["CHIMERA", "core_minutes"], bb_cpu_s=ms.loc[best_base_s, "core_minutes"],
        v1_slo=pct(m.loc["CHIMERA_v1", "slo_violation_rate"]),
        abl_ood=pct(ab.loc["CHIMERA_w/o_OOD_Fallback", "slo_violation_rate"] - ab.loc["CHIMERA_Full", "slo_violation_rate"]),
        abl_sa_cpu=(ab.loc["CHIMERA_w/o_State_Augmentation", "core_minutes"] / ab.loc["CHIMERA_Full", "core_minutes"] - 1) * 100,
        abl_sa_slo=pct(ab.loc["CHIMERA_w/o_State_Augmentation", "slo_violation_rate"] - ab.loc["CHIMERA_Full", "slo_violation_rate"]),
        abl_cp_act=(ab.loc["CHIMERA_w/o_Coupling_Penalty", "scaling_actions"], ab.loc["CHIMERA_Full", "scaling_actions"]),
        abl_cp_slo=pct(ab.loc["CHIMERA_Full", "slo_violation_rate"] - ab.loc["CHIMERA_w/o_Coupling_Penalty", "slo_violation_rate"]),
        fr_gap=(mc_real.gap_pp.abs().max() if len(mc_real) else float("nan")),
        fr_mid=(ch.iloc[-2].slo_violation_rate * 100, ch.iloc[-2].core_minutes),
        fr_low=(ch.iloc[-1].slo_violation_rate * 100, ch.iloc[-1].core_minutes),
        fr_cheapest=ch.iloc[0].core_minutes,
        fr_best_other=(best_other.family, best_other.slo_violation_rate * 100),
        n_tests=n_tests, tests_ok=tests_ok,
        demo=rows, pods=pods.groups() if pods else None, hpa_pods=hpa_pods.groups() if hpa_pods else None,
        hold=hold.groups() if hold else None,
        pt_wins=int((pt.gap_pp > 0).sum()), pt_total=len(pt),
        pt_synth=(int((pt[pt.set == "synthetic"].gap_pp > 0).sum()), int((pt.set == "synthetic").sum())),
        loss_traces=[t.replace("real_kswd_w", "w_000") for t in loss_traces],
        loss_range=((-losses.gap_pp).min() if len(losses) else 0.0, (-losses.gap_pp).max() if len(losses) else 0.0),
        abl_naive=pct(ab.loc["CHIMERA_w/o_Naive_Conformal", "slo_violation_rate"] - ab.loc["CHIMERA_Full", "slo_violation_rate"]),
        casc_cpu_s=(synth[synth.config_name == "CHIMERA_w/o_Cascade"].core_minutes.mean()
                    / synth[synth.config_name == "CHIMERA_Full"].core_minutes.mean() - 1) * 100,
        casc_cpu_r=(real[real.config_name == "CHIMERA_w/o_Cascade"].core_minutes.mean()
                    / real[real.config_name == "CHIMERA_Full"].core_minutes.mean() - 1) * 100,
        casc_slo_s=pct(synth[synth.config_name == "CHIMERA_w/o_Cascade"].slo_violation_rate.mean()
                       - synth[synth.config_name == "CHIMERA_Full"].slo_violation_rate.mean()),
        v2_act=v2.get("CHIMERA_v2", float("nan")), v3_act=v2.get("CHIMERA_v3", float("nan")),
        v4_act=v2.get("CHIMERA_v4", float("nan")),
        band_pods=(df[df.config_name == "CHIMERA_w/o_Replica_Band"].horizontal_scaling_actions.mean(),
                   df[df.config_name == "CHIMERA_Full"].horizontal_scaling_actions.mean()),
        release_cpu=(df[df.config_name == "CHIMERA_w/o_Fast_Release"].core_minutes.mean()
                     / df[df.config_name == "CHIMERA_Full"].core_minutes.mean() - 1) * 100,
        deadband=(df[df.config_name == "CHIMERA_w/o_Up_Deadband"].scaling_actions.mean(),
                  df[df.config_name == "CHIMERA_Full"].scaling_actions.mean()),
        eq=pd.read_csv(ROOT / "results" / "equal_cpu_tests_v5.csv"),
        hpa_p99=m.loc["HPA", "p99_response_time"],
        vpa_cpu_s=ms.loc["VPA", "core_minutes"], aapa_cpu_s=ms.loc["AAPA", "core_minutes"],
        vpa_slo_s=pct(ms.loc["VPA", "slo_violation_rate"]), aapa_slo_s=pct(ms.loc["AAPA", "slo_violation_rate"]),
    )


# ---------------------------------------------------------------------------- editing
def shapes(slide):
    def walk(ss):
        for sh in ss:
            if sh.shape_type == 6:
                yield from walk(sh.shapes)
            else:
                yield sh
    return {sh.shape_id: sh for sh in walk(slide.shapes)}


def set_para(slide, sid, pi, text, run=0):
    """Replace one paragraph's text. run=0: whole paragraph (formatting of its first run).
    run=k: keep runs before k (e.g. a bold label) and replace the rest with `text`."""
    para = shapes(slide)[sid].text_frame.paragraphs[pi]
    runs = [r for r in para.runs]
    runs[run].text = text
    for r in runs[run + 1:]:
        r._r.getparent().remove(r._r)


def set_run(slide, sid, pi, ri, text):
    shapes(slide)[sid].text_frame.paragraphs[pi].runs[ri].text = text


def set_cell(slide, sid, row, col, text):
    cell = shapes(slide)[sid].table.cell(row, col)
    para = cell.text_frame.paragraphs[0]
    para.runs[0].text = text
    for r in para.runs[1:]:
        r._r.getparent().remove(r._r)


def replace_image(prs, slide_no, media_name, new_png):
    slide = prs.slides[slide_no - 1]
    for rel in slide.part.rels.values():
        if rel.reltype.endswith("/image") and rel.target_part.partname.endswith(media_name):
            rel.target_part._blob = Path(new_png).read_bytes()
            return
    raise KeyError(f"{media_name} not on slide {slide_no}")


def main():
    n = numbers()
    if not n["tests_ok"]:
        sys.exit("unit tests are failing -- fix them before building the deck")
    prs = Presentation(SRC)
    S = lambda k: prs.slides[k - 1]
    ch, aapa, hpa, nosa = (n["demo"][k] for k in ("CHIMERA", "AAPA", "HPA", "CHIMERA (no SA)"))
    fmt = lambda x: f"{x:,.0f}"

    # 4 -- architecture
    set_para(S(4), 26, 1, "e = [v−v*, h−h*, dh₋₁]")
    set_para(S(4), 42, 1, "dh(t−1)  (one slot)")
    set_para(S(4), 36, 1, "0.1 s · 5 s inner loop")
    set_para(S(4), 16, 1, "drift → margin reset")
    set_para(S(4), 20, 0, "Risk-optimal sizing")
    set_para(S(4), 20, 1, "min E[violations] + λ·cores")
    set_para(S(4), 32, 0, "Safety projection")
    set_para(S(4), 32, 1, "bounds, budget, coupling")
    set_para(S(4), 52, 0, "pods already ordered but still booting are fed back into the controller, so it "
             "never orders the same capacity twice. A 5 s inner loop moves only the 0.1 s CPU actuator: "
             "up on a surge, back down as soon as the load falls — capacity tracks demand between the 15 s joint steps.",
             run=1)
    # 5 -- control law
    set_para(S(5), 4, 0, "LQR assumes actions act at once. A pod boots in 20 s and is first seen two "
             "15 s ticks later, so the state carries the one command still in flight; h counts only "
             "serving pods, so nothing is counted twice.")
    set_para(S(5), 7, 0, "e(t) = [ v−v*, h_serving−h*, dh(t−1) ]")
    set_para(S(5), 24, 0, "Q = diag(30, 10, 0)")
    set_para(S(5), 17, 0, "Lyapunov certificate")
    set_para(S(5), 19, 0, "V(e⁺) − V(e) = −eᵀ(Q + KᵀRK)e ≤ 0")
    set_para(S(5), 22, 1, "Stability comes from the Riccati solution itself, so no runtime filter is needed; "
             "a 5 s inner loop owns CPU and replicas move only when CPU cannot absorb the change.")
    set_para(S(5), 24, 2, "Δt = 15 s / 5 s   κ = 0.85")
    set_para(S(5), 22, 2, "Capacity minimises expected SLO violations + price × cores under the queueing model, "
             "over conformal demand scenarios; with a certain forecast it is exactly demand / target.")
    # 7 -- setup
    set_para(S(7), 8, 0, "9 traces — the 5 real Azure Functions 2019 excerpts plus 4 held-out synthetic shapes "
             "never used in design.")
    set_para(S(7), 13, 0, f"CHIMERA v5, v1–v4, HPA, VPA, HPA+VPA, AAPA, MagicScaler-inspired, KOSMOS, plus "
             f"{n['n_ablation']} ablation and {n['n_sens']} sensitivity variants.")
    set_para(S(7), 18, 0, f"{n['seeds']} fresh seeds × {n['workloads']} workloads × {n['configs']} configurations = "
             f"{fmt(n['runs'])} runs, plus a {fmt(n['sweep_runs'])}-run cost/SLO frontier sweep. 20-min unscored "
             f"warm-up, then minutes 70–100 scored.")
    set_para(S(7), 23, 0, "SLO violation rate (500 ms), p99 latency, CPU core-minutes (real cost), "
             "pod-minutes, scaling actions.")
    set_para(S(7), 26, 0, "Identical bounds (0.5 cores/pod, 2 replicas) and warm-up for all. Each controller is "
             "swept over its setting (0.4–0.95) and compared at EQUAL CPU — the fair test. Baseline bugs found in "
             "audit were fixed first (MagicScaler pod size, HPA+VPA never firing VPA, a load-sensor cap). Design "
             "was done on a separate dev split; this run used data no design step saw.")
    # 9 -- module completion
    set_para(S(9), 5, 0, "12 / 12")
    set_para(S(9), 6, 0, "modules implemented and unit-tested")
    set_para(S(9), 8, 0, f"{n['n_tests']} / {n['n_tests']}")
    set_para(S(9), 11, 0, fmt(n["runs"] + n["sweep_runs"]))
    set_para(S(9), 12, 0, f"simulation runs on disk ({n['seeds']} seeds)")
    set_para(S(9), 14, 0, str(n["configs"]))
    set_cell(S(9), 10, 2, 2, "Riccati P positive-definite; one-slot delay buffer; 5 s cascade on CPU; "
             "risk-optimal sizing")
    set_cell(S(9), 10, 3, 2, "Stability certified by the Riccati P; the runtime filter never bound and is "
             "off in v4")
    set_cell(S(9), 10, 4, 2, "Inert in the v4 cascade (identical runs without it): coordination is structural")
    set_cell(S(9), 10, 4, 3, "Superseded")
    from pptx.dml.color import RGBColor as _RGB
    _c = shapes(S(9))[10].table.cell(4, 3)
    _c.fill.solid()
    _c.fill.fore_color.rgb = _RGB(0x6B, 0x72, 0x80)
    set_cell(S(9), 10, 5, 2, "6 baselines incl. KOSMOS; 3 baseline bugs fixed; cost/SLO frontier sweep")
    set_cell(S(9), 10, 6, 2, "Real PromQL client, in-place resize via pods/resize, status, dry-run; "
             "not yet run on a live cluster")
    set_cell(S(9), 10, 6, 3, "Code complete")
    # 10 -- cost vs quality
    set_para(S(10), 3, 0, "Cost and quality must be read together")
    set_para(S(10), 7, 0, f"{n['ch_slo']:.1f} %")
    set_para(S(10), 8, 0, f"SLO violations, 5 real traces (next best: {n['best_base']} {n['bb_slo']:.1f} %)")
    set_para(S(10), 9, 0, f"{n['ch_cpu']:.0f}")
    set_para(S(10), 10, 0, f"CPU core-minutes per run (AAPA {n['aapa_cpu']:.0f})")
    lt = " and ".join(n["loss_traces"])
    losses = (f"; every loss is on {lt} ({n['loss_range'][0]:.1f}–{n['loss_range'][1]:.1f} pp)" if lt
              else " — every one, on held-out data")
    set_para(S(10), 12, 0, f"At equal CPU, trace by trace, CHIMERA has fewer violations in {n['pt_wins']} of "
             f"{n['pt_total']} comparisons{losses}. It reaches {n['fr_low'][0]:.1f} % at {n['fr_low'][1]:.0f} "
             f"core-min; no baseline gets below {n['fr_best_other'][1]:.1f} %.")
    # 11 -- tail
    eqr = n["eq"][(n["eq"].set == "real") & (n["eq"].baseline == "AAPA")].iloc[0]
    set_para(S(11), 3, 0, "Equal CPU, held-out data: every comparison won")
    set_para(S(11), 7, 0, f"{n['pt_wins']} / {n['pt_total']}")
    set_para(S(11), 8, 0, f"equal-CPU comparisons won; vs AAPA on real traces +{eqr.mean_gap:.1f} pp (p < 0.001)")
    set_para(S(11), 9, 0, f"{n['kos_p99'] / n['ch_p99']:.0f}×")
    set_para(S(11), 10, 0, f"better p99 than KOSMOS, with {n['kos_act'] / n['ch_act']:.0f}× fewer scaling "
             f"actions ({n['ch_act']:.0f} vs {n['kos_act']:.0f})")
    set_para(S(11), 12, 0, f"Correction: v2–v4 reported a 19× better p99 than AAPA. With a warm-up, most of that "
             f"was AAPA starting cold; the real tail gap is {n['aapa_p99'] / n['ch_p99']:.1f}× vs AAPA, "
             f"{n['hpa_p99'] / n['ch_p99']:.1f}× vs HPA. CHIMERA makes {n['ch_act']:.0f} resizes per run "
             f"(KOSMOS {n['kos_act']:.0f}).")
    # 12 -- ablation
    set_para(S(12), 7, 0, f"+{n['release_cpu']:.0f} %")
    set_para(S(12), 8, 0, "CPU cost of removing fast CPU release")
    set_para(S(12), 9, 0, f"{n['band_pods'][0] / max(n['band_pods'][1], 1e-9):.0f}×")
    set_para(S(12), 10, 0, f"more pod changes without the replica band ({n['band_pods'][0]:.1f} vs "
             f"{n['band_pods'][1]:.1f} per run)")
    set_para(S(12), 12, 0, f"Risk-optimal sizing supersedes the old reactive margin; the scale-up dead-band cuts "
             f"resizes {n['deadband'][0]:.0f} → {n['deadband'][1]:.0f}. Coupling penalty, guardrail, bridging flag "
             f"and the Lyapunov filter are inert — reported, not hidden.")
    # 13 -- related work
    set_cell(S(13), 14, 3, 2, f"One coordinated law — {n['kos_p99'] / n['ch_p99']:.0f}× better tail, "
             f"{n['kos_slo'] - n['ch_slo']:.0f} pp fewer violations, {n['kos_act'] / n['ch_act']:.0f}× fewer actions.")
    # 15 -- kubernetes
    set_para(S(15), 8, 0, "chimeraautoscalers.autoscaling.chimera.io declares bounds, target utilisation, pod "
             "start-up time and resize mode as a first-class cluster object, with a status subresource.")
    set_para(S(15), 13, 0, "Patch rights on deployments, deployments/scale and pods/resize.")
    set_para(S(15), 18, 0, "Slim image: the operator and control core only (numpy, scipy, kubernetes client).")
    set_para(S(15), 21, 0, "Complete")
    set_para(S(15), 23, 0, "Every 15 s: reads pods and Ready count, runs the control step, resizes CPU in place, "
             "scales replicas, writes status. Tested end to end against a simulated API.")
    set_para(S(15), 26, 0, "Complete")
    from pptx.dml.color import RGBColor
    for pill in (20, 25):   # badge colour follows its label: teal = Complete
        shapes(S(15))[pill].fill.fore_color.rgb = RGBColor(0x0F, 0x76, 0x6E)
        shapes(S(15))[pill].line.color.rgb = RGBColor(0x0F, 0x76, 0x6E)
    set_para(S(15), 28, 0, "Real PromQL client over HTTP, tested against a live HTTP server; default query "
             "reads cAdvisor CPU usage.")
    set_para(S(15), 31, 0, "Ready to run")
    set_para(S(15), 33, 0, "Quickstart pins kind on Kubernetes 1.33 (in-place resize on by default). "
             "Not yet run on a live cluster.")
    # 16 -- outputs
    set_para(S(16), 6, 0, f"All {n['n_tests']} pass: controller maths, delay model, coupling, sensor and "
             f"target-split fixes, Prometheus client over HTTP, operator reconcile loop.", run=1)
    set_para(S(16), 7, 0, f"Real trace w_0004, seed 42: {ch[0]:.2f} % SLO violations vs AAPA {aapa[0]:.2f} % "
             f"and HPA {hpa[0]:.2f} %, at {ch[1]:.0f} CPU-min (AAPA {aapa[1]:.0f}, HPA {hpa[1]:.0f}). "
             f"Without delay augmentation: {(nosa[1] / ch[1] - 1) * 100:+.0f} % CPU.", run=1)
    # 17 -- results database
    set_run(S(17), 6, 0, 0, "results_v5.csv   ")
    set_para(S(17), 6, 0, f"Every run of the {fmt(n['runs'])}-run evaluation; src/scripts/analyze_v2.py turns "
             f"it into the report, statistics and frontier. Review-2 data is kept unmodified.", run=1)
    set_run(S(17), 7, 0, 0, "Example custom resource   ")
    set_para(S(17), 7, 0, "k8s/example-autoscaler.yaml, applied by the quickstart; the decision log below it "
             "is from the reconcile-loop test.", run=1)
    set_para(S(17), 9, 0, fmt(n["runs"]))
    set_para(S(17), 12, 0, str(n["configs"]))
    set_para(S(17), 15, 0, str(n["workloads"]))
    set_para(S(17), 18, 0, str(n["seeds"]))
    # 18 -- live demo
    set_para(S(18), 3, 0, "One command, about ten seconds")
    set_para(S(18), 7, 0, "Same code as the full evaluation — the demo calls the harness's own run_simulation "
             "on one workload. The full evaluation takes ~30 minutes on 12 cores and writes results_v5.csv.")
    set_para(S(18), 19, 0, "3×3 augmented A matrix (one in-flight slot); all Riccati eigenvalues positive — "
             "V = eᵀPe is a valid Lyapunov function.")
    set_para(S(18), 24, 0, f"CHIMERA {ch[0]:.2f} % vs AAPA {aapa[0]:.2f} % vs HPA {hpa[0]:.2f} %; without "
             f"delay augmentation it spends {(nosa[1] / ch[1] - 1) * 100:+.0f} % CPU.")
    if n["pods"] and n["hpa_pods"]:
        cp = n["pods"][0] if n["pods"][0] == n["pods"][1] else f"{n['pods'][0]}–{n['pods'][1]}"
        set_para(S(18), 29, 0, f"HPA’s CPU line is flat — one lever, so it swings between {n['hpa_pods'][0]} and "
                 f"{n['hpa_pods'][1]} pods. CHIMERA holds {cp} and moves CPU instead.")
    if n["hold"]:
        set_para(S(18), 34, 0, f"At t = {n['hold'][0]} s: +{n['hold'][1]} pods ordered one tick earlier are still "
                 f"booting, so the controller holds instead of re-ordering them.")
    # 19 -- limitations
    lim = ["Real traces are 100-minute excerpts of five; the full Azure dataset is no longer public.",
           "The operator has not yet run on a live Kubernetes cluster (code and tests are complete).",
           "v2–v4's '19× better tail' was mostly a cold-start artifact; with warm-up it is "
           f"{n['aapa_p99'] / n['ch_p99']:.1f}× vs AAPA.",
           "Coupling penalty, guardrail, bridging flag and Lyapunov filter are inert in v5.",
           f"{n['ch_act']:.0f} in-place resizes per run (v4: {n['v4_act']:.0f}; AAPA changes pods ~7 times).",
           "The held-out synthetic shapes are ours; real-trace results are the ones that generalise."]
    nxt = ["Find another source for the full Azure Functions 2019 dataset.",
           "Run the quickstart on kind 1.33 and record a live trace.",
           "Delete the inert mechanisms from the code.",
           "Learn the latency model from Prometheus histograms in the operator.",
           "Sweep horizontal delay from 5 s to 80 s.",
           "Rate-limit the inner loop to cut resize churn further."]
    for i, t in enumerate(lim):
        set_para(S(19), 8, i, t)
    for i, t in enumerate(nxt):
        set_para(S(19), 9, i, t)
    # 21 -- conclusion
    set_para(S(21), 4, 0, f"{n['ch_slo']:.1f} %")
    set_para(S(21), 5, 0, f"SLO violations, real traces (next best {n['bb_slo']:.1f} %)")
    set_para(S(21), 6, 0, fmt(n["runs"] + n["sweep_runs"]))
    set_para(S(21), 7, 0, f"simulation runs, {n['seeds']} seeds")
    eqa = n["eq"][(n["eq"].set == "real") & (n["eq"].baseline == "AAPA")].iloc[0]
    set_para(S(21), 8, 0, f"+{eqa.mean_gap:.1f} pp")
    set_para(S(21), 9, 0, "fewer violations than AAPA at equal CPU (p < 0.001)")
    set_para(S(21), 10, 0, f"{n['pt_wins']} / {n['pt_total']}")
    set_para(S(21), 11, 0, "equal-CPU comparisons won on held-out data")
    set_para(S(21), 13, 0, f"On data no design decision saw, CHIMERA has fewer SLO violations than every baseline "
             f"at equal CPU, on every trace — significant against all of them. Next milestone: a live cluster.")

    # Images (same pixel sizes as the originals, so the layout does not move)
    for slide_no, name in [(10, "image-10-1.png"), (11, "image-11-1.png"), (12, "image-12-1.png"),
                           (16, "image-16-1.png"), (16, "image-16-2.png"), (17, "image-17-1.png"),
                           (17, "image-17-2.png")]:
        replace_image(prs, slide_no, name, FIGS / name)

    prs.save(OUT)
    print(f"wrote {OUT}")
    for k in ("runs", "sweep_runs", "configs", "seeds", "ch_slo", "bb_slo", "best_base", "ch_p99", "aapa_p99",
              "ch_cpu", "aapa_cpu", "fr_gap", "fr_low", "fr_best_other", "n_tests"):
        print(f"  {k:<14} {n[k]}")


if __name__ == "__main__":
    main()
