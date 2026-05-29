"""Smoke tests for cost templates, economics, and tracker state machine."""
from datetime import date
import tempfile

from src.cost_db import COST_TEMPLATES, total_estimate, benchmark_summary
from src.economics import compute_economics
from src.risk_register import RISK_TEMPLATES, lookup_risks
from src.tracker import AFETracker, AFERecord


def test_every_intervention_has_costs_and_risks():
    for intervention in COST_TEMPLATES:
        assert total_estimate(intervention) > 0, f"{intervention} has zero cost"
        assert lookup_risks(intervention), f"{intervention} has no risks"


def test_benchmark_summary_returns_all():
    bench = benchmark_summary()
    assert set(bench) == set(COST_TEMPLATES)
    assert all(v > 0 for v in bench.values())


def test_economics_positive_npv_for_acid():
    econ = compute_economics(treatment_cost_usd=180_000, incremental_rate_bopd=130)
    assert econ.npv_10pct_usd > 0
    assert econ.payout_months < 12
    assert econ.incremental_first_year_bbl > 0


def test_tracker_upsert_and_advance():
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
        tracker = AFETracker(tmp.name)
        rec = AFERecord(
            afe_number="AFE-T-001", well_id="TEST-1H", intervention="acid_stimulation",
            total_cost_usd=180_000, status="draft",
            created_date=date.today().isoformat(), last_updated=date.today().isoformat(),
        )
        tracker.upsert(rec)
        tracker.advance("AFE-T-001", "engineering_review", note="moved to review")
        df = tracker.as_dataframe()
        assert len(df) == 1
        assert df.iloc[0]["status"] == "engineering_review"
        assert df.iloc[0]["notes"] == "moved to review"
