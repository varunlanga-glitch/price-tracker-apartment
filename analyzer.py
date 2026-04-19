"""
Condo Tracker - Analyzer

Enriches raw scraped listings with:
  - Relist detection (same address, new MLS# = disguised price drop)
  - Effective DOM (cumulative days across all relists)
  - Price reduction % from original/first-list price
  - Deal score 0–100

Must be called after scrape_all() and before report generation.
"""

from datetime import date
from database import (
    get_conn, normalize_address, get_prior_address_mls,
    update_address_history
)
from config import SCORE_WEIGHTS, DOM_SWEET_SPOT_MIN, DOM_SWEET_SPOT_MAX


# ── Relist detection ──────────────────────────────────────────────────────────

def enrich_relists(listings: list[dict]) -> list[dict]:
    """
    For each listing, check if this address has had a prior (now inactive) MLS#.
    If so, mark as relist and compute effective_dom and real price reduction.
    """
    for lst in listings:
        addr_norm = lst.get("address_normalized") or normalize_address(lst.get("address_raw", ""))
        prior = get_prior_address_mls(addr_norm, lst["mls_number"])

        if prior:
            lst["is_relist"]       = 1
            lst["prior_mls_number"] = prior["mls_number"]

            # Effective DOM = days from original list date to today
            orig_date = date.fromisoformat(prior["first_seen_date"])
            lst["effective_dom"] = (date.today() - orig_date).days

            # Real price reduction vs original list price
            orig_price = prior.get("initial_price") or lst["price_original"] or lst["price"]
            if orig_price and orig_price > 0:
                reduction_pct = 100.0 * (orig_price - lst["price"]) / orig_price
                lst["price_reduction_pct"] = round(reduction_pct, 2)
                # Also update price_original so the report shows the real history
                lst["price_original"] = orig_price
        else:
            # No relist — compute reduction from price_original within this listing
            orig = lst.get("price_original") or lst["price"]
            if orig and orig > 0 and lst["price"] < orig:
                lst["price_reduction_pct"] = round(100.0 * (orig - lst["price"]) / orig, 2)

    return listings


# ── Deal scoring ──────────────────────────────────────────────────────────────

def _percentile_rank(value: float, values: list[float]) -> float:
    """Return 0–1 percentile rank (higher = more extreme high value)."""
    if not values or len(values) < 2:
        return 0.5
    below = sum(1 for v in values if v < value)
    return below / (len(values) - 1)


def compute_deal_scores(listings: list[dict]) -> list[dict]:
    """
    Compute a 0–100 deal score for each listing.
    Higher = better deal (cheaper per sqft, price drops, motivated seller, lower fees).
    """
    active = [l for l in listings if l["is_active"] and not l.get("is_senior_flagged")]

    # Pre-compute market arrays (exclude listings with missing data)
    psf_values  = [l["price_per_sqft"] for l in active if l["price_per_sqft"] > 0]
    year_values = [l["year_built"]      for l in active if l["year_built"]      > 0]

    # Monthly cost = maintenance fee + monthly property tax.
    # Scoring the combined figure is fairer than scoring the two fees separately —
    # a low-fee / high-tax condo and a high-fee / low-tax condo look equal this way.
    def _monthly_cost(lst: dict) -> float:
        fee  = lst.get("maintenance_fee") or 0.0
        tax  = lst.get("taxes_annual")    or 0.0
        return fee + tax / 12.0

    cost_values = [_monthly_cost(l) for l in active if _monthly_cost(l) > 0]

    for lst in listings:
        if lst.get("is_senior_flagged"):
            lst["deal_score"] = 0.0
            continue

        score_components = {}

        # 1. Price per sqft (lower = better deal → invert rank)
        psf = lst.get("price_per_sqft") or 0
        if psf > 0 and psf_values:
            rank = _percentile_rank(psf, psf_values)
            score_components["price_per_sqft"] = 1.0 - rank   # invert: cheapest = 1.0
        else:
            score_components["price_per_sqft"] = 0.5   # neutral if unknown

        # 2. Price reduction (cumulative % drop)
        reduction = lst.get("price_reduction_pct") or 0.0
        # Cap at 15% reduction = full score; linear below
        score_components["price_reduction"] = min(reduction / 15.0, 1.0)

        # 3. DOM / motivated seller signal
        eff_dom = lst.get("effective_dom") or lst.get("dom") or 0
        if eff_dom < DOM_SWEET_SPOT_MIN:
            # Fresh listing — no urgency signal but not bad
            dom_score = 0.3
        elif DOM_SWEET_SPOT_MIN <= eff_dom <= DOM_SWEET_SPOT_MAX:
            # Sweet spot: seller has been waiting, more negotiable
            dom_score = 0.3 + 0.7 * ((eff_dom - DOM_SWEET_SPOT_MIN) /
                                      (DOM_SWEET_SPOT_MAX - DOM_SWEET_SPOT_MIN))
        else:
            # Very stale — could mean overpriced, slight penalty
            dom_score = max(0.4, 1.0 - 0.003 * (eff_dom - DOM_SWEET_SPOT_MAX))
        score_components["dom"] = dom_score

        # 4. Monthly carrying cost — maintenance fee + monthly taxes (lower = better)
        mc = _monthly_cost(lst)
        if mc > 0 and cost_values:
            rank = _percentile_rank(mc, cost_values)
            score_components["monthly_cost"] = 1.0 - rank   # invert: cheapest = 1.0
        else:
            score_components["monthly_cost"] = 0.5   # neutral if unknown

        # 5. Year built (newer = better)
        yr = lst.get("year_built") or 0
        if yr > 0 and year_values:
            rank = _percentile_rank(yr, year_values)
            score_components["year_built"] = rank
        else:
            score_components["year_built"] = 0.5

        # Weighted composite
        raw_score = sum(SCORE_WEIGHTS[k] * score_components[k] for k in SCORE_WEIGHTS)
        lst["deal_score"] = round(raw_score * 100, 1)

    return listings


# ── Market median ─────────────────────────────────────────────────────────────

def market_summary(listings: list[dict]) -> dict:
    """Return a dict of market-level stats for the report header."""
    active = [l for l in listings if l["is_active"] and not l.get("is_senior_flagged")]
    if not active:
        return {}

    prices     = [l["price"]           for l in active if l["price"]           > 0]
    psf        = [l["price_per_sqft"]  for l in active if l["price_per_sqft"]  > 0]
    doms       = [l["dom"]             for l in active if l["dom"]             is not None]
    eff_doms   = [l["effective_dom"]   for l in active if l["effective_dom"]   is not None]
    relists    = sum(1 for l in active if l.get("is_relist"))
    drops_today= sum(1 for l in active if (l.get("price_reduction_pct") or 0) > 0)

    def median(lst):
        s = sorted(lst)
        n = len(s)
        if not n:
            return 0
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    return {
        "total_active":      len(active),
        "median_price":      median(prices),
        "avg_price":         sum(prices) / len(prices) if prices else 0,
        "min_price":         min(prices) if prices else 0,
        "max_price":         max(prices) if prices else 0,
        "median_psf":        median(psf),
        "avg_dom":           sum(doms) / len(doms) if doms else 0,
        "avg_effective_dom": sum(eff_doms) / len(eff_doms) if eff_doms else 0,
        "relist_count":      relists,
        "relist_pct":        round(100 * relists / len(active), 1) if active else 0,
        "with_price_drops":  drops_today,
        "run_date":          date.today().isoformat(),
    }


# ── Full pipeline ─────────────────────────────────────────────────────────────

def run_analysis(listings: list[dict]) -> tuple[list[dict], dict]:
    """
    Full enrichment pipeline.
    Returns (enriched_listings, market_summary_dict).
    """
    listings = enrich_relists(listings)
    listings = compute_deal_scores(listings)
    summary  = market_summary(listings)
    return listings, summary
