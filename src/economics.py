"""Quick economics for AFE — NPV, payout, $/BOE. Mirrors the production-engineer-copilot
economics module but exposed via this repo for standalone use."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class AFEEconomics:
    treatment_cost_usd: float
    incremental_first_year_bbl: float
    incremental_eur_bbl: float
    npv_10pct_usd: float
    payout_months: float
    dollars_per_incremental_bbl: float


def compute_economics(
    treatment_cost_usd: float,
    incremental_rate_bopd: float,
    uplift_decline_per_yr: float = 0.6,
    horizon_years: int = 5,
    realized_price_per_bbl: float = 65.0,
    opex_per_bbl: float = 12.0,
    discount_rate: float = 0.10,
) -> AFEEconomics:
    months = np.arange(1, horizon_years * 12 + 1)
    monthly_rate = incremental_rate_bopd * np.exp(-uplift_decline_per_yr * (months / 12))
    monthly_vol = monthly_rate * 30
    margin_per_bbl = realized_price_per_bbl - opex_per_bbl
    monthly_revenue = monthly_vol * margin_per_bbl
    discount_factors = (1 + discount_rate / 12) ** months
    npv = float(np.sum(monthly_revenue / discount_factors) - treatment_cost_usd)

    cumulative = np.cumsum(monthly_revenue)
    payout_idx = int(np.searchsorted(cumulative, treatment_cost_usd))
    payout_months = float(payout_idx) if payout_idx < len(months) else float("inf")

    first_year_bbl = float(monthly_vol[:12].sum())
    eur = float(monthly_vol.sum())
    dollars_per_bbl = treatment_cost_usd / first_year_bbl if first_year_bbl > 0 else float("inf")

    return AFEEconomics(
        treatment_cost_usd=treatment_cost_usd,
        incremental_first_year_bbl=first_year_bbl,
        incremental_eur_bbl=eur,
        npv_10pct_usd=npv,
        payout_months=payout_months,
        dollars_per_incremental_bbl=dollars_per_bbl,
    )
