"""Streamlit demo: AFE pipeline dashboard + ad-hoc drafter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `src.*` imports work on Streamlit Cloud
# (where the package isn't pip-installed, just the deps from requirements.txt).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import __version__
from src.cost_db import COST_TEMPLATES, benchmark_summary, total_estimate
from src.drafter import AFEDiagnosis, run_drafter
from src.economics import simulate_economics
from src.models import AFEDiagnosis as AFEDiagnosisModel
from src.tracker import AFETracker, seed_demo_data


st.set_page_config(page_title="AFE Copilot", page_icon="📝", layout="wide")

_title_col, _badge_col = st.columns([0.8, 0.2])
with _title_col:
    st.title("AFE Copilot")
with _badge_col:
    st.markdown(
        f"<div style='text-align:right;margin-top:1.5rem;'>"
        f"<span style='background:#1F3A5F;color:#fff;padding:3px 10px;"
        f"border-radius:12px;font-size:0.85rem;font-weight:600;'>v{__version__}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
st.caption("Draft, track, and analyze AFEs — built for multi-rig E&P operators.")

with st.expander(f"🆕 What's new in v{__version__}"):
    st.markdown(
        "- **Monte-Carlo AFE economics** (P10/P50/P90 + tornado sensitivity)\n"
        "- **Validated one-click chain from Production Engineer Copilot** "
        "(schema validation, friendly errors)\n"
        "- **Contingency now computed from its stated %** (cost table can't drift from the math)\n"
        "- **docx generation decoupled from the Anthropic SDK**\n"
        "- **Fixed payout off-by-one**; variance no longer crashes on empty input; "
        "Word tables render bold (no literal `**`)"
    )

DB_PATH = Path("pipeline.sqlite")
if not DB_PATH.exists():
    seed_demo_data(DB_PATH)

tab_pipeline, tab_drafter, tab_benchmarks = st.tabs(["Pipeline", "Draft New AFE", "Cost Benchmarks"])

# ------------ Pipeline tab --------------------------------------------------
with tab_pipeline:
    df = AFETracker(DB_PATH).as_dataframe()
    if df.empty:
        st.info("No AFEs yet. Use the Draft tab.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("In-flight AFEs", int((df.status.isin(["draft", "engineering_review", "finance_review"])).sum()))
        col2.metric("Approved (not executed)", int((df.status == "approved").sum()))
        col3.metric("HIGH bottleneck risk", int((df.bottleneck_risk == "HIGH").sum()))
        col4.metric("Total $ in pipeline (M)", f"${df.total_cost_usd.sum() / 1e6:.1f}M")

        st.subheader("Pipeline status")
        status_counts = df.status.value_counts().reindex(
            ["draft", "engineering_review", "finance_review", "approved", "executed", "rejected"],
            fill_value=0,
        ).reset_index()
        status_counts.columns = ["status", "count"]
        fig = px.bar(status_counts, x="status", y="count",
                     color="status", text="count")
        fig.update_layout(showlegend=False, height=300, margin=dict(l=0, r=0, t=20, b=0))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Active AFEs")
        show = df[["afe_number", "well_id", "intervention", "rig_name",
                   "total_cost_usd", "status", "days_in_status", "bottleneck_risk"]].copy()
        show["total_cost_usd"] = show["total_cost_usd"].apply(lambda c: f"${c:,.0f}")
        st.dataframe(show, use_container_width=True, hide_index=True)

# ------------ Drafter tab ---------------------------------------------------
with tab_drafter:
    # ---- One-click chain from Production Engineer Copilot -------------------
    with st.expander("🔗 Chain from Production Engineer Copilot (paste diagnosis JSON)"):
        st.caption(
            "Paste a diagnosis exported by the Production Engineer Copilot (Project 1). "
            "It is validated before it can become an AFE — invalid fields are reported "
            "in plain English instead of a stack trace."
        )
        pe_upload = st.file_uploader("Upload PE-Copilot diagnosis .json", type=["json"],
                                     key="pe_copilot_upload")
        pe_text = st.text_area("…or paste the diagnosis JSON here", height=160,
                               key="pe_copilot_text")

        if st.button("Validate & load into drafter", key="pe_copilot_load"):
            raw = None
            if pe_upload is not None:
                raw = pe_upload.getvalue().decode("utf-8")
            elif pe_text.strip():
                raw = pe_text
            if not raw:
                st.warning("Paste JSON or upload a file first.")
            else:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as e:
                    st.error(f"That isn't valid JSON: {e}")
                else:
                    try:
                        diag = AFEDiagnosisModel.from_pe_copilot(payload)
                    except ValueError as e:
                        st.error("Diagnosis rejected:")
                        for line in str(e).splitlines():
                            st.markdown(line)
                    else:
                        st.session_state["pe_preset"] = {
                            "well_id": diag.well_id,
                            "api_number": diag.api_number,
                            "field": diag.field,
                            "operator": diag.operator,
                            "intervention": diag.intervention,
                            "primary_diagnosis": diag.primary_diagnosis,
                            "incremental_rate_bopd": diag.incremental_rate_bopd,
                            "expected_uplift_decline_per_yr": diag.expected_uplift_decline_per_yr,
                            "requested_by": diag.requested_by,
                        }
                        st.success(
                            f"Validated diagnosis for {diag.well_id} "
                            f"({diag.intervention}). Fields loaded below."
                        )

    st.subheader("Generate a new AFE")
    examples_dir = Path("examples")
    sample_files = sorted(examples_dir.glob("well_diagnosis*.json")) if examples_dir.exists() else []

    if sample_files:
        chosen = st.selectbox("Or load an example", ["(custom)"] + [str(p) for p in sample_files])
    else:
        chosen = "(custom)"

    if chosen != "(custom)":
        with open(chosen) as f:
            preset = json.load(f)
    elif "pe_preset" in st.session_state:
        # a validated diagnosis loaded from the Production Engineer Copilot
        preset = st.session_state["pe_preset"]
    else:
        preset = {}

    well_id = st.text_input("Well ID", value=preset.get("well_id", ""))
    api = st.text_input("API #", value=preset.get("api_number", ""))
    field = st.text_input("Field", value=preset.get("field", ""))
    operator = st.text_input("Operator", value=preset.get("operator", ""))
    intervention = st.selectbox("Intervention type", list(COST_TEMPLATES),
                                index=list(COST_TEMPLATES).index(preset.get("intervention", "acid_stimulation"))
                                if preset.get("intervention") in COST_TEMPLATES else 0)
    diagnosis_text = st.text_area("Primary diagnosis (free-form)",
                                  value=preset.get("primary_diagnosis", ""), height=120)
    incremental_rate = st.number_input("Incremental uplift (BOPD)", value=float(preset.get("incremental_rate_bopd", 100)))
    decline = st.number_input("Uplift decline (per year)", value=float(preset.get("expected_uplift_decline_per_yr", 0.6)))
    requested_by = st.text_input("Requested by", value=preset.get("requested_by", "Eric Diaz, Staff PE"))

    # ---- Monte-Carlo economics (pure numpy — no API key needed) -------------
    st.markdown("---")
    st.subheader("Probabilistic economics (Monte-Carlo)")
    st.caption(
        "10,000 trials over incremental rate (±30%), uplift decline (±0.15 abs), "
        "and realized price (~$12 sd). Treatment cost is the benchmark estimate for "
        "the selected intervention."
    )
    if st.button("Run Monte-Carlo NPV"):
        if incremental_rate <= 0:
            st.error("Incremental uplift must be greater than 0 to run economics.")
        else:
            treatment_cost = total_estimate(intervention)
            mc = simulate_economics(
                treatment_cost_usd=treatment_cost,
                incremental_rate_bopd=incremental_rate,
                uplift_decline_per_yr=decline,
            )
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("P10 NPV (downside)", f"${mc.npv_p10_usd/1e6:,.2f}M")
            m2.metric("P50 NPV (median)", f"${mc.npv_p50_usd/1e6:,.2f}M")
            m3.metric("P90 NPV (upside)", f"${mc.npv_p90_usd/1e6:,.2f}M")
            m4.metric("P(payout < 24 mo)", f"{mc.probability_of_payout*100:.0f}%")

            # Tornado chart: bars sorted by swing, centered on base NPV.
            items = sorted(mc.tornado.items(), key=lambda kv: kv[1]["swing"])
            labels = [k.replace("_", " ") for k, _ in items]
            lows = [v["low"] for _, v in items]
            highs = [v["high"] for _, v in items]
            base = mc.base_npv_usd
            fig_t = go.Figure()
            fig_t.add_trace(go.Bar(
                y=labels, x=[base - lo for lo in lows], base=lows,
                orientation="h", name="downside", marker_color="#C0504D",
                hovertemplate="low NPV: $%{base:,.0f}<extra></extra>",
            ))
            fig_t.add_trace(go.Bar(
                y=labels, x=[hi - base for hi in highs], base=base,
                orientation="h", name="upside", marker_color="#4F81BD",
                hovertemplate="high NPV: $%{x:,.0f}<extra></extra>",
            ))
            fig_t.add_vline(x=base, line_dash="dash", line_color="#1F3A5F",
                            annotation_text=f"base ${base/1e6:,.2f}M")
            fig_t.update_layout(
                barmode="overlay", height=300, showlegend=True,
                margin=dict(l=0, r=0, t=20, b=0),
                xaxis_title="NPV @ 10% (USD)",
                title="Tornado — NPV swing per variable",
            )
            st.plotly_chart(fig_t, use_container_width=True)

    if st.button("Draft AFE", type="primary"):
        if not well_id or not diagnosis_text:
            st.error("Well ID and primary diagnosis are required.")
        else:
            diagnosis = AFEDiagnosis(
                well_id=well_id, api_number=api, field=field, operator=operator,
                intervention=intervention, primary_diagnosis=diagnosis_text,
                incremental_rate_bopd=incremental_rate,
                expected_uplift_decline_per_yr=decline,
                requested_by=requested_by,
            )
            with st.spinner("Drafting AFE..."):
                markdown = run_drafter(diagnosis)
            st.markdown(markdown)
            st.download_button("Download .md", markdown, file_name=f"AFE_{well_id}_{intervention}.md")

# ------------ Benchmarks tab ------------------------------------------------
with tab_benchmarks:
    st.subheader("Reference cost per intervention (synthetic Permian benchmarks)")
    bench = benchmark_summary()
    bench_df = pd.DataFrame({"intervention": list(bench), "total_usd": list(bench.values())})
    bench_df["total_usd"] = bench_df["total_usd"].apply(lambda c: f"${c:,.0f}")
    st.dataframe(bench_df, use_container_width=True, hide_index=True)
