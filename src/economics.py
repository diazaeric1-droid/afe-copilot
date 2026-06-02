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
    days_per_month = 365.25 / 12  # avoid the 360-day-year undercount
    months = np.arange(1, horizon_years * 12 + 1)
    monthly_rate = incremental_rate_bopd * np.exp(-uplift_decline_per_yr * (months / 12))
    monthly_vol = monthly_rate * days_per_month
    margin_per_bbl = realized_price_per_bbl - opex_per_bbl
    monthly_revenue = monthly_vol * margin_per_bbl
    discount_factors = (1 + discount_rate / 12) ** months
    npv = float(np.sum(monthly_revenue / discount_factors) - treatment_cost_usd)

    # cumulative[i] is cumulative net revenue at the END of month i+1, so the
    # recovery month is the 1-based (payout_idx + 1).
    cumulative = np.cumsum(monthly_revenue)
    payout_idx = int(np.searchsorted(cumulative, treatment_cost_usd))
    payout_months = float(payout_idx + 1) if payout_idx < len(months) else float("inf")

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


# ---------- Monte-Carlo NPV ---------------------------------------------------

@dataclass
class MonteCarloResult:
    """Distributional NPV outcome from simulate_economics."""
    n_trials: int
    npv_p10_usd: float          # conservative downside (10th percentile)
    npv_p50_usd: float          # median
    npv_p90_usd: float          # optimistic upside (90th percentile)
    npv_mean_usd: float
    probability_of_payout: float  # P(NPV > 0 AND payout within 24 months)
    tornado: dict[str, dict[str, float]]  # var -> {"low": npv, "high": npv, "swing": abs}
    base_npv_usd: float


def _npv_vectorized(
    treatment_cost_usd: float,
    incremental_rate_bopd,        # scalar or np.ndarray
    uplift_decline_per_yr,        # scalar or np.ndarray
    realized_price_per_bbl,       # scalar or np.ndarray
    horizon_years: int = 5,
    opex_per_bbl: float = 12.0,
    discount_rate: float = 0.10,
):
    """Vectorized NPV reusing the exact deterministic math from compute_economics.

    Each input draw can be a scalar or a 1-D array of length n; broadcasting over
    the monthly time axis yields an (n_draws,) NPV vector (or a scalar). This is
    the same formula as compute_economics, just batched for Monte-Carlo speed.
    """
    days_per_month = 365.25 / 12
    months = np.arange(1, horizon_years * 12 + 1)               # (T,)
    rate = np.atleast_1d(np.asarray(incremental_rate_bopd, dtype=float))[:, None]      # (n,1)
    decline = np.atleast_1d(np.asarray(uplift_decline_per_yr, dtype=float))[:, None]   # (n,1)
    price = np.atleast_1d(np.asarray(realized_price_per_bbl, dtype=float))[:, None]    # (n,1)

    monthly_rate = rate * np.exp(-decline * (months[None, :] / 12))      # (n,T)
    monthly_vol = monthly_rate * days_per_month
    margin_per_bbl = price - opex_per_bbl                                # (n,1)
    monthly_revenue = monthly_vol * margin_per_bbl                       # (n,T)
    discount_factors = (1 + discount_rate / 12) ** months               # (T,)
    npv = np.sum(monthly_revenue / discount_factors, axis=1) - treatment_cost_usd
    return npv  # (n,)


def _payout_within(
    treatment_cost_usd: float,
    incremental_rate_bopd: float,
    uplift_decline_per_yr: float,
    realized_price_per_bbl: float,
    months_cap: int,
    horizon_years: int = 5,
    opex_per_bbl: float = 12.0,
) -> np.ndarray:
    """Boolean array: did the (undiscounted) cumulative net revenue recover the
    treatment cost within months_cap, per draw? Mirrors compute_economics payout."""
    days_per_month = 365.25 / 12
    months = np.arange(1, horizon_years * 12 + 1)
    rate = np.atleast_1d(np.asarray(incremental_rate_bopd, dtype=float))[:, None]
    decline = np.atleast_1d(np.asarray(uplift_decline_per_yr, dtype=float))[:, None]
    price = np.atleast_1d(np.asarray(realized_price_per_bbl, dtype=float))[:, None]
    monthly_vol = rate * np.exp(-decline * (months[None, :] / 12)) * days_per_month
    monthly_revenue = monthly_vol * (price - opex_per_bbl)
    cumulative = np.cumsum(monthly_revenue, axis=1)             # (n,T)
    cap = min(months_cap, len(months))
    # recovered within cap months if cumulative at month `cap` >= cost
    return cumulative[:, cap - 1] >= treatment_cost_usd


def simulate_economics(
    treatment_cost_usd: float,
    incremental_rate_bopd: float,
    uplift_decline_per_yr: float = 0.6,
    realized_price_per_bbl: float = 65.0,
    horizon_years: int = 5,
    opex_per_bbl: float = 12.0,
    discount_rate: float = 0.10,
    n_trials: int = 10_000,
    rate_rel_spread: float = 0.30,        # incremental_rate_bopd ±30% (uniform)
    decline_abs_spread: float = 0.15,     # uplift_decline_per_yr ±0.15 abs (uniform)
    price_sd: float = 12.0,               # realized_price normal sd (~$12)
    payout_cap_months: int = 24,
    seed: int | None = 42,
) -> MonteCarloResult:
    """Monte-Carlo NPV over the three biggest AFE uncertainties.

    Draws ~n_trials samples of:
      - incremental_rate_bopd  ~ Uniform(base*(1-rel), base*(1+rel))
      - uplift_decline_per_yr  ~ Uniform(base-abs, base+abs), clipped to (0, 2)
      - realized_price_per_bbl ~ Normal(base, price_sd), clipped at $1 floor

    Each draw reuses the deterministic NPV math (see _npv_vectorized). Returns
    P10/P50/P90 NPV, P(payout within `payout_cap_months`), and a tornado dict
    holding each variable's NPV swing when moved to its low/high while the others
    sit at base — the classic single-variable sensitivity view.
    """
    rng = np.random.default_rng(seed)

    rate_lo = incremental_rate_bopd * (1 - rate_rel_spread)
    rate_hi = incremental_rate_bopd * (1 + rate_rel_spread)
    rate_draws = rng.uniform(rate_lo, rate_hi, n_trials)

    decline_lo = uplift_decline_per_yr - decline_abs_spread
    decline_hi = uplift_decline_per_yr + decline_abs_spread
    decline_draws = np.clip(rng.uniform(decline_lo, decline_hi, n_trials), 1e-6, 2.0)

    price_draws = np.clip(rng.normal(realized_price_per_bbl, price_sd, n_trials), 1.0, None)

    npvs = _npv_vectorized(
        treatment_cost_usd, rate_draws, decline_draws, price_draws,
        horizon_years=horizon_years, opex_per_bbl=opex_per_bbl, discount_rate=discount_rate,
    )

    paid = _payout_within(
        treatment_cost_usd, rate_draws, decline_draws, price_draws,
        months_cap=payout_cap_months, horizon_years=horizon_years, opex_per_bbl=opex_per_bbl,
    )
    prob_payout = float(np.mean((npvs > 0) & paid))

    p10, p50, p90 = (float(x) for x in np.percentile(npvs, [10, 50, 90]))

    # Tornado: move ONE variable to its low/high (others at base value).
    base = float(_npv_vectorized(
        treatment_cost_usd, incremental_rate_bopd, uplift_decline_per_yr, realized_price_per_bbl,
        horizon_years=horizon_years, opex_per_bbl=opex_per_bbl, discount_rate=discount_rate,
    )[0])

    def _one(rate, decl, price) -> float:
        return float(_npv_vectorized(
            treatment_cost_usd, rate, decl, price,
            horizon_years=horizon_years, opex_per_bbl=opex_per_bbl, discount_rate=discount_rate,
        )[0])

    # use the same low/high anchors as the draws (decline low = lower decline = higher NPV)
    decl_lo_anchor = max(uplift_decline_per_yr - decline_abs_spread, 1e-6)
    decl_hi_anchor = min(uplift_decline_per_yr + decline_abs_spread, 2.0)
    price_lo_anchor = max(realized_price_per_bbl - 1.2816 * price_sd, 1.0)   # ~P10 of normal
    price_hi_anchor = realized_price_per_bbl + 1.2816 * price_sd             # ~P90 of normal

    tornado: dict[str, dict[str, float]] = {}
    for name, lo_vals, hi_vals in (
        ("incremental_rate_bopd",
         (rate_lo, uplift_decline_per_yr, realized_price_per_bbl),
         (rate_hi, uplift_decline_per_yr, realized_price_per_bbl)),
        ("uplift_decline_per_yr",
         (incremental_rate_bopd, decl_hi_anchor, realized_price_per_bbl),   # high decline -> low NPV
         (incremental_rate_bopd, decl_lo_anchor, realized_price_per_bbl)),  # low decline -> high NPV
        ("realized_price_per_bbl",
         (incremental_rate_bopd, uplift_decline_per_yr, price_lo_anchor),
         (incremental_rate_bopd, uplift_decline_per_yr, price_hi_anchor)),
    ):
        low_npv = _one(*lo_vals)
        high_npv = _one(*hi_vals)
        tornado[name] = {
            "low": low_npv,
            "high": high_npv,
            "swing": abs(high_npv - low_npv),
        }

    return MonteCarloResult(
        n_trials=n_trials,
        npv_p10_usd=p10,
        npv_p50_usd=p50,
        npv_p90_usd=p90,
        npv_mean_usd=float(np.mean(npvs)),
        probability_of_payout=prob_payout,
        tornado=tornado,
        base_npv_usd=base,
    )
