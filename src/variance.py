"""Variance analysis: actual cost vs. AFE, with breakdown by category, vendor, rig."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class VarianceSummary:
    n_afes: int
    total_afe_usd: float
    total_actual_usd: float
    overall_variance_pct: float
    over_budget_count: int
    worst_offender_category: str | None
    worst_offender_pct: float


def analyze_variance(afe_df: pd.DataFrame, actuals_df: pd.DataFrame) -> VarianceSummary:
    """Compute portfolio variance + worst-offender category.

    afe_df columns: afe_number, category, line_total_usd
    actuals_df columns: afe_number, category, actual_usd
    """
    merged = afe_df.merge(actuals_df, on=["afe_number", "category"], how="outer").fillna(0)
    merged["variance_usd"] = merged["actual_usd"] - merged["line_total_usd"]
    merged["variance_pct"] = (merged["variance_usd"] / merged["line_total_usd"].replace(0, pd.NA)) * 100

    by_afe = merged.groupby("afe_number").agg(
        afe_total=("line_total_usd", "sum"),
        actual_total=("actual_usd", "sum"),
    )
    by_afe["pct"] = (by_afe["actual_total"] - by_afe["afe_total"]) / by_afe["afe_total"].replace(0, pd.NA) * 100

    by_cat = merged.groupby("category").agg(
        afe_total=("line_total_usd", "sum"),
        actual_total=("actual_usd", "sum"),
    )
    by_cat["pct"] = (by_cat["actual_total"] - by_cat["afe_total"]) / by_cat["afe_total"].replace(0, pd.NA) * 100
    by_cat = by_cat.dropna().sort_values("pct", ascending=False)

    worst_cat = by_cat.index[0] if not by_cat.empty else None
    worst_pct = float(by_cat["pct"].iloc[0]) if not by_cat.empty else 0.0

    total_afe = float(by_afe["afe_total"].sum())
    total_actual = float(by_afe["actual_total"].sum())
    overall_pct = ((total_actual - total_afe) / total_afe * 100) if total_afe else 0.0

    return VarianceSummary(
        n_afes=int(by_afe.shape[0]),
        total_afe_usd=total_afe,
        total_actual_usd=total_actual,
        overall_variance_pct=float(overall_pct),
        over_budget_count=int((by_afe["pct"] > 0).sum()),
        worst_offender_category=worst_cat,
        worst_offender_pct=worst_pct,
    )
