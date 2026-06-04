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

# --- Self-heal stale bytecode / module cache (Streamlit Cloud) --------------
# Streamlit reuses the container across redeploys; a cached .pyc or already-imported
# OLD module can lack symbols added in a newer commit, surfacing as a startup
# ImportError for a name that exists in the source. Purge src/ bytecode + evict
# cached src modules so every submodule reloads from CURRENT source (no-op when clean).
import shutil as _shutil
for _pycache in (REPO_ROOT / "src").rglob("__pycache__"):
    _shutil.rmtree(_pycache, ignore_errors=True)
for _name in [m for m in sys.modules if m == "src" or m.startswith("src.")]:
    del sys.modules[_name]

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import __version__
from src.cost_db import COST_TEMPLATES, benchmark_summary, cost_rollup, total_estimate
from src.drafter import AFEDiagnosis, MissingAPIKey, run_drafter
try:
    from src.economics import jib_split, price_sensitivity, simulate_economics
    _MC_AVAILABLE = True
except Exception as _mc_err:  # never let an optional analytics import take down the app
    simulate_economics = None
    price_sensitivity = None
    jib_split = None
    _MC_AVAILABLE = False
    _MC_IMPORT_ERROR = repr(_mc_err)
from src.models import AFEDiagnosis as AFEDiagnosisModel
from src.tracker import AFETracker, seed_demo_data
from src.variance import analyze_variance, demo_variance_data


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
        "- **Working-interest / NRI net economics** + a **JIB partner-allocation** preview "
        "(NPV was implicitly assuming 100% WI/NRI — now you see the operator's net position)\n"
        "- **Tangible vs. intangible (IDC) cost split** on every estimate (the tax view finance asks for)\n"
        "- **Authority-limit approval routing** ($ value → required approver) + **AFE-supplement** "
        "flagging when actuals overrun the policy threshold\n"
        "- **Variance analyzer wired into the app** (the advertised feature is now a tab) — "
        "and it no longer hides 100%-unbudgeted overruns\n"
        "- **Price-deck sensitivity** ($/bbl strip) and a **true effective-10% discount** "
        "(was 10.47% from monthly compounding)\n"
        "- **Immutable audit trail** of every status change; graceful no-API-key behavior"
    )

DB_PATH = Path("pipeline.sqlite")
if not DB_PATH.exists():
    seed_demo_data(DB_PATH)

tab_pipeline, tab_drafter, tab_variance, tab_benchmarks = st.tabs(
    ["Pipeline", "Draft New AFE", "Variance", "Cost Benchmarks"])

# ------------ Pipeline tab --------------------------------------------------
with tab_pipeline:
    df = AFETracker(DB_PATH).as_dataframe()
    if df.empty:
        st.info("No AFEs yet. Use the Draft tab.")
    else:
        in_flight_mask = df.status.isin(["draft", "engineering_review", "finance_review"])
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("In-flight AFEs", int(in_flight_mask.sum()))
        col2.metric("Approved (not executed)", int((df.status == "approved").sum()))
        col3.metric("HIGH bottleneck risk", int((df.bottleneck_risk == "HIGH").sum()))
        # In-flight $ only — exclude executed (spent) and rejected (dead) AFEs.
        col4.metric("In-flight $ (M)", f"${df.loc[in_flight_mask, 'total_cost_usd'].sum() / 1e6:.1f}M")

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
                   "total_cost_usd", "status", "days_in_status", "bottleneck_risk",
                   "required_approver"]].copy()
        show["total_cost_usd"] = show["total_cost_usd"].apply(lambda c: f"${c:,.0f}")
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption("`required_approver` is the delegation-of-authority level the AFE's $ value "
                   "needs (PE < $50k · Eng Mgr < $250k · Ops Mgr < $1MM · VP above).")

        with st.expander("🧾 Audit trail (immutable status-change log)"):
            ev = AFETracker(DB_PATH).events()
            if ev.empty:
                st.caption("No events recorded yet.")
            else:
                st.dataframe(ev[["ts", "afe_number", "from_status", "to_status", "actor", "note"]],
                             use_container_width=True, hide_index=True)

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

    # ---- Net economics & price deck (deterministic — no API key needed) -----
    st.markdown("---")
    st.subheader("Net economics, price deck & partner split")
    rollup = cost_rollup(intervention)
    gc1, gc2, gc3 = st.columns(3)
    gc1.metric("AFE total (gross)", f"${rollup['total']:,.0f}")
    gc2.metric("Tangible (capitalized)", f"${rollup['tangible']:,.0f}")
    gc3.metric("Intangible (IDC)", f"${rollup['intangible']:,.0f}")

    wc1, wc2, wc3 = st.columns(3)
    working_interest = wc1.number_input("Working interest (WI)", 0.0, 1.0, 1.0, 0.05,
                                        help="Operator's share of COST.")
    net_revenue_interest = wc2.number_input("Net revenue interest (NRI)", 0.0, 1.0, 0.80, 0.01,
                                            help="Operator's share of REVENUE after royalty.")
    realized_price = wc3.number_input("Realized price ($/bbl)", 20.0, 150.0, 65.0, 1.0)

    if _MC_AVAILABLE and incremental_rate > 0:
        from src.economics import compute_economics as _ce
        net = _ce(rollup["total"], incremental_rate, uplift_decline_per_yr=decline,
                  realized_price_per_bbl=realized_price,
                  working_interest=working_interest, net_revenue_interest=net_revenue_interest)
        nc1, nc2, nc3 = st.columns(3)
        nc1.metric("Gross NPV @ 10%", f"${net.npv_10pct_usd/1e6:,.2f}M")
        nc2.metric("Net NPV to operator", f"${net.net_npv_10pct_usd/1e6:,.2f}M",
                   help="WI% of cost, NRI% of revenue — what the operator actually books.")
        nc3.metric("Payout", f"{net.payout_months:.0f} mo" if net.payout_months != float('inf') else "—")

        deck = price_sensitivity(rollup["total"], incremental_rate,
                                 uplift_decline_per_yr=decline,
                                 working_interest=working_interest,
                                 net_revenue_interest=net_revenue_interest)
        deck_df = pd.DataFrame(deck)
        deck_df = deck_df.assign(
            **{"Realized $/bbl": deck_df["realized_price"].map(lambda v: f"${v:,.0f}"),
               "Gross NPV": deck_df["npv_usd"].map(lambda v: f"${v/1e6:,.2f}M"),
               "Net NPV": deck_df["net_npv_usd"].map(lambda v: f"${v/1e6:,.2f}M"),
               "Payout (mo)": deck_df["payout_months"].map(
                   lambda v: f"{v:.0f}" if v != float('inf') else "—")})
        st.caption("Price-deck sensitivity (NPV at a fixed rate across a realized-price strip):")
        st.dataframe(deck_df[["Realized $/bbl", "Gross NPV", "Net NPV", "Payout (mo)"]],
                     use_container_width=True, hide_index=True)

        if working_interest < 1.0:
            partners = {"Operator (you)": working_interest,
                        "Non-op partner(s)": round(1.0 - working_interest, 4)}
            jib = pd.DataFrame(jib_split(rollup["total"], partners))
            jib["net_cost_usd"] = jib["net_cost_usd"].map(lambda v: f"${v:,.0f}")
            jib["working_interest"] = jib["working_interest"].map(lambda v: f"{v:.0%}")
            st.caption("JIB cost allocation (gross AFE billed by working interest):")
            st.dataframe(jib[["partner", "working_interest", "net_cost_usd"]],
                         use_container_width=True, hide_index=True)

    # ---- Monte-Carlo economics (pure numpy — no API key needed) -------------
    st.markdown("---")
    st.subheader("Probabilistic economics (Monte-Carlo, gross)")
    st.caption(
        "10,000 trials over incremental rate (±30%), uplift decline (±0.15 abs), "
        "and realized price (~$12 sd). Treatment cost is the benchmark estimate for "
        "the selected intervention."
    )
    if not _MC_AVAILABLE:
        st.info("Probabilistic economics is temporarily unavailable in this build; "
                "the rest of the app is unaffected.")
    elif st.button("Run Monte-Carlo NPV"):
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
            try:
                with st.spinner("Drafting AFE..."):
                    markdown = run_drafter(diagnosis)
                st.markdown(markdown)
                st.download_button("Download .md", markdown, file_name=f"AFE_{well_id}_{intervention}.md")
            except MissingAPIKey:
                st.warning("Set `ANTHROPIC_API_KEY` to draft the AFE narrative. Everything else on "
                           "this page — cost tables, tangible/intangible split, net economics, "
                           "price deck, and Monte-Carlo — works without a key.")

# ------------ Variance tab --------------------------------------------------
with tab_variance:
    st.subheader("Actual-vs-AFE variance (closed-out AFEs)")
    st.caption("Demo actuals for two closed AFEs — including a 100%-unbudgeted 'Fishing' line "
               "and a rig overrun that trips the supplemental-AFE policy (>10%).")
    afe_df, actuals_df = demo_variance_data()
    vs = analyze_variance(afe_df, actuals_df)

    v1, v2, v3, v4 = st.columns(4)
    v1.metric("AFEs analyzed", vs.n_afes)
    v2.metric("Portfolio variance", f"{vs.overall_variance_pct:+.1f}%")
    v3.metric("Over budget", vs.over_budget_count)
    v4.metric("Total actual", f"${vs.total_actual_usd/1e6:,.2f}M")

    if vs.worst_offender_category:
        pct = f" ({vs.worst_offender_pct:+.0f}%)" if vs.worst_offender_pct is not None else " (unbudgeted)"
        st.markdown(f"**Worst-offender category:** {vs.worst_offender_category} — "
                    f"**${vs.worst_offender_overrun_usd:,.0f}** overrun{pct}")
    if vs.unbudgeted_categories:
        st.warning("Unbudgeted actuals (no AFE line existed): "
                   + ", ".join(vs.unbudgeted_categories))
    if vs.supplement_required_afes:
        st.error("⚠️ Supplemental AFE required (actuals exceed AFE by >10%): "
                 + ", ".join(vs.supplement_required_afes))

    merged = afe_df.merge(actuals_df, on=["afe_number", "category"], how="outer").fillna(0)
    merged["variance_usd"] = merged["actual_usd"] - merged["line_total_usd"]
    merged = merged.sort_values("variance_usd", ascending=False)
    disp = merged.copy()
    for c in ("line_total_usd", "actual_usd", "variance_usd"):
        disp[c] = disp[c].apply(lambda v: f"${v:,.0f}")
    disp.columns = ["AFE", "Category", "AFE budget", "Actual", "Variance"]
    st.dataframe(disp, use_container_width=True, hide_index=True)

# ------------ Benchmarks tab ------------------------------------------------
with tab_benchmarks:
    st.subheader("Reference cost per intervention (synthetic Permian benchmarks)")
    rows = []
    for interv in COST_TEMPLATES:
        r = cost_rollup(interv)
        rows.append({"intervention": interv, "total_usd": r["total"],
                     "tangible_usd": r["tangible"], "intangible_usd": r["intangible"]})
    bench_df = pd.DataFrame(rows)
    for c in ("total_usd", "tangible_usd", "intangible_usd"):
        bench_df[c] = bench_df[c].apply(lambda v: f"${v:,.0f}")
    bench_df.columns = ["Intervention", "Total", "Tangible (capex)", "Intangible (IDC)"]
    st.dataframe(bench_df, use_container_width=True, hide_index=True)
    st.caption("Tangible = capitalized equipment (depreciated); Intangible = IDC "
               "(rig, services, labor, chemicals — currently expensed).")
