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
import streamlit as st

from src.cost_db import COST_TEMPLATES, benchmark_summary
from src.drafter import AFEDiagnosis, run_drafter
from src.tracker import AFETracker, seed_demo_data


st.set_page_config(page_title="AFE Copilot", page_icon="📝", layout="wide")
st.title("AFE Copilot")
st.caption("Draft, track, and analyze AFEs — built for multi-rig E&P operators.")

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
