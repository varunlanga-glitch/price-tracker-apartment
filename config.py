"""
Condo Tracker - Configuration
Edit this file to change search parameters.
"""

# ── Search Areas ──────────────────────────────────────────────────────────────
# Each area is a dict with a label and lat/lon bounding box.
# Coordinates are approximate; the scraper post-filters by city name too.
SEARCH_AREAS = [
    {
        "label": "Abbotsford",
        "lat_min": 49.00,
        "lat_max": 49.10,
        "lon_min": -122.42,
        "lon_max": -122.05,
    },
    {
        "label": "Mission",
        "lat_min": 49.09,
        "lat_max": 49.22,
        "lon_min": -122.38,
        "lon_max": -122.05,
    },
    {
        "label": "Langley",
        "lat_min": 49.05,
        "lat_max": 49.16,
        "lon_min": -122.72,
        "lon_max": -122.38,
    },
]

# ── Price Filter ──────────────────────────────────────────────────────────────
PRICE_MIN = 150_000
PRICE_MAX = 450_000

# ── Bedrooms ──────────────────────────────────────────────────────────────────
BED_MIN = 1
BED_MAX = 1   # Change to 2 to include 2-bedroom units

# ── Age Filter (for highlighting only — NOT a hard exclusion) ────────────────
# Buildings older than this many years are INCLUDED in results but highlighted
# in yellow in the Excel report so you can filter them out in Excel if you want.
# The 55+ senior-community exclusion is the only HARD exclude — this is soft.
# Set to 0 to disable highlighting entirely.
MAX_BUILDING_AGE_YEARS = 15   # highlight buildings built before (current year - 15)

# ── 55+ / Senior Detection Keywords ──────────────────────────────────────────
# Any listing whose remarks or zoning contain these phrases is excluded.
# Kept tight — only phrases that unambiguously mean a 55+ community.
# "adult living", "retirement", "no children", etc. are intentionally excluded
# because they also appear in normal condo descriptions.
SENIOR_KEYWORDS = [
    "55+", "55 +", "55plus", "55 plus",
    "55 and over", "55 years and",
    "age restricted", "age restriction",
    "seniors only", "senior community",
    "senior living", "seniors living",
]

# ── Deal Score Weights (must sum to 1.0) ─────────────────────────────────────
# monthly_cost = maintenance_fee + (taxes_annual / 12) — true carrying cost.
# Using combined monthly cost is fairer than scoring the two fees separately
# because a high-tax low-fee condo and a low-tax high-fee condo look equivalent.
SCORE_WEIGHTS = {
    "price_per_sqft":   0.35,   # cheaper per sqft vs area = higher score
    "price_reduction":  0.20,   # cumulative drop from first list price
    "dom":              0.20,   # longer DOM = more motivated seller (up to a point)
    "monthly_cost":     0.15,   # maintenance fee + monthly property taxes (lower = better)
    "year_built":       0.10,   # newer = better
}

# DOM thresholds for scoring
DOM_SWEET_SPOT_MIN = 21    # starts showing motivated-seller signal
DOM_SWEET_SPOT_MAX = 90    # above this score starts declining (overpriced?)

# ── API Settings ──────────────────────────────────────────────────────────────
RECORDS_PER_PAGE = 200
REQUEST_DELAY_SECONDS = 1.5   # be polite to realtor.ca

# ── File Paths ────────────────────────────────────────────────────────────────
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(BASE_DIR, "condo_tracker.db")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)
