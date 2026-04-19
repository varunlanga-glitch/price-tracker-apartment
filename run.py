#!/usr/bin/env python3
"""
Condo Tracker — Daily Run Script
=================================
Run this once a day:   python run.py

What it does:
  1. Scrapes realtor.ca for 1BR condos in Abbotsford, Mission, Langley (< $450K)
  2. Saves results to condo_tracker.db (SQLite — persists between runs)
  3. Detects relists (same address, new MLS# = disguised price drop)
  4. Computes deal scores 0–100
  5. Generates reports/condo_tracker_YYYY-MM-DD.xlsx

First run: no history yet, so relist detection and trend charts are limited.
After 2–3 weeks: data becomes powerful — real DOM, price trends, relist patterns.
"""

import sys
import os
import time
from datetime import date

# ── Dependency check ───────────────────────────────────────────────────────────
def _check_deps():
    missing = []
    for pkg in ("playwright", "openpyxl"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n  [!] Missing packages: {', '.join(missing)}")
        print(f"  Run these commands first:\n")
        print(f"      pip install playwright openpyxl")
        print(f"      playwright install chromium\n")
        sys.exit(1)

_check_deps()

# ── ensure imports work from this directory ────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH, REPORTS_DIR
from database import (
    init_db, upsert_listing, record_snapshot,
    update_address_history, mark_inactive_listings,
    get_active_listings
)
from scraper import scrape_all
from analyzer import run_analysis
from report import generate_report


def banner(msg: str):
    width = 60
    print("\n" + "═" * width)
    print(f"  {msg}")
    print("═" * width)


def run(test_mode=False):
    today = date.today().isoformat()
    start = time.time()

    banner(f"Condo Tracker  —  {today}{' [TEST MODE - Abbotsford only]' if test_mode else ''}")

    # ── 1. Initialize DB ───────────────────────────────────────────────────────
    print("\n[1/5] Initializing database...")
    init_db()
    print(f"      DB path: {DB_PATH}")

    # ── 2. Scrape realtor.ca ───────────────────────────────────────────────────
    print("\n[2/5] Scraping realtor.ca...")
    import config as _cfg
    scrape_areas = ([a for a in _cfg.SEARCH_AREAS if a["label"] == "Abbotsford"]
                    if test_mode else None)
    raw_listings = scrape_all(areas=scrape_areas)

    if not raw_listings:
        print("\n  [!] No listings returned. This may be a temporary API issue.")
        print("      Check your internet connection and try again in a few minutes.")
        print("      If this persists, realtor.ca may have changed their API.")
        sys.exit(1)

    print(f"\n      Scraped {len(raw_listings)} listings total")

    # ── 3. Analyze (relist detection + deal scoring) ───────────────────────────
    print("\n[3/5] Analyzing listings...")
    enriched, summary = run_analysis(raw_listings)

    # ── 4. Save to DB ──────────────────────────────────────────────────────────
    print("\n[4/5] Saving to database...")
    active_mls = set()

    for lst in enriched:
        if lst.get("is_senior_flagged"):
            continue   # Don't persist 55+ listings

        mls = lst["mls_number"]
        active_mls.add(mls)

        lst.pop("_property_id", None)   # safety — never write internal keys to DB
        lst.pop("_raw_id", None)
        upsert_listing(lst)
        record_snapshot(
            mls_number=mls,
            snapshot_date=today,
            price=lst.get("price") or 0,
            maintenance_fee=lst.get("maintenance_fee") or 0,
            dom=lst.get("dom") or 0,
        )
        update_address_history(
            address_norm=lst["address_normalized"],
            mls_number=mls,
            first_seen=lst["first_seen_date"],
            price=lst.get("price") or 0,
        )

    # Mark anything we didn't see today as inactive
    mark_inactive_listings(active_mls, today)

    # Re-fetch from DB (includes all enriched + computed fields) for report
    db_listings = get_active_listings()
    if not db_listings:
        db_listings = [l for l in enriched if not l.get("is_senior_flagged")]

    print(f"      Saved {len(active_mls)} active listings")

    # ── 5. Generate Excel report ───────────────────────────────────────────────
    print("\n[5/5] Generating Excel report...")
    report_path = generate_report(db_listings, summary, today)

    elapsed = time.time() - start
    banner(f"Done in {elapsed:.1f}s")

    # ── Summary printout ───────────────────────────────────────────────────────
    print(f"""
  Active listings : {summary.get('total_active', len(db_listings))}
  Median price    : ${summary.get('median_price', 0):>10,.0f}
  Median $/sqft   : ${summary.get('median_psf', 0):>10,.0f}
  Avg DOM         : {summary.get('avg_dom', 0):>10.1f} days
  Relists found   : {summary.get('relist_count', 0):>10} ({summary.get('relist_pct', 0):.1f}% of active)
  With price drops: {summary.get('with_price_drops', 0):>10}
  55+ excluded    : {sum(1 for l in enriched if l.get('is_senior_flagged')):>10}

  Report saved to:
  {report_path}
""")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Condo Tracker — daily realtor.ca scraper")
    parser.add_argument("--test", action="store_true",
                        help="Scrape Abbotsford only (faster, to verify API is working)")
    args = parser.parse_args()
    run(test_mode=args.test)
