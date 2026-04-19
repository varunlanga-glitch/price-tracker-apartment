"""
diagnose.py — Pinpoints which API parameter triggers 'not authorized'.

Usage:  python diagnose.py

Runs inside a real Playwright browser so Cloudflare is not the issue.
Each test adds one parameter at a time to find which one breaks the call.
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Run:  pip install playwright && playwright install chromium")
    sys.exit(1)

API_URL = "https://api2.realtor.ca/Listing.svc/PropertySearch_Post"

# Abbotsford bounding box
BBOX = {
    "LatitudeMax": "49.10", "LatitudeMin": "49.00",
    "LongitudeMax": "-122.05", "LongitudeMin": "-122.42",
}

BASE = {
    "CultureId": "1", "ApplicationId": "1",
    "RecordsPerPage": "5", "MaximumResults": "5",
    "TransactionTypeId": "2",
    "CurrentPage": "1", "Version": "7.0",
    **BBOX,
}

tests = [
    ("Baseline (no type filter)",
     {}),
    ("+ PropertySearchTypeId=1 (Residential)",
     {"PropertySearchTypeId": "1"}),
    ("+ PropertySearchTypeId=3 (Condo/Strata)",
     {"PropertySearchTypeId": "3"}),
    ("+ BuildingTypeId=17 (Apartment)",
     {"PropertySearchTypeId": "1", "BuildingTypeId": "17"}),
    ("+ OwnershipTypeGroupId=2 (Condo/Strata)",
     {"PropertySearchTypeId": "1", "BuildingTypeId": "17", "OwnershipTypeGroupId": "2"}),
    ("+ BedRange=1-1",
     {"PropertySearchTypeId": "1", "BuildingTypeId": "17",
      "OwnershipTypeGroupId": "2", "BedRange": "1-1"}),
    ("+ PriceMin/Max",
     {"PropertySearchTypeId": "1", "BuildingTypeId": "17",
      "OwnershipTypeGroupId": "2", "BedRange": "1-1",
      "PriceMin": "150000", "PriceMax": "450000"}),
    ("Full payload (what run.py now sends)",
     {"PropertySearchTypeId": "1", "BuildingTypeId": "17",
      "OwnershipTypeGroupId": "2", "BedRange": "1-1",
      "PriceMin": "150000", "PriceMax": "450000",
      "SortBy": "6", "SortOrder": "D"}),
]

def run_test(page, label, extra):
    payload = {**BASE, **extra}
    try:
        resp = page.request.post(
            API_URL,
            form=payload,
            headers={
                "Referer": "https://www.realtor.ca/map",
                "Origin":  "https://www.realtor.ca",
                "Accept":  "application/json, text/plain, */*",
            },
            timeout=20000,
        )
        status = resp.status
        if status != 200:
            print(f"  [HTTP {status}]  {label}")
            return

        data = resp.json()
        err    = data.get("ErrorCode", {})
        paging = data.get("Paging", {})
        results = data.get("Results", []) or []
        total  = paging.get("TotalRecords", "?")

        if err.get("Id") not in (200, "200", None, ""):
            print(f"  [API ERR {err.get('Id')}]  {label}")
            print(f"    → {err.get('Description')}")
        else:
            first = ""
            if results:
                r = results[0]
                b = r.get("Building", {}) or {}
                p = r.get("Property", {}) or {}
                first = (f"  beds={b.get('Bedrooms','?')} "
                         f"type={b.get('Type','?')} "
                         f"own={p.get('OwnershipType','?')} "
                         f"price=${float(p.get('PriceUnformattedValue') or 0):,.0f}")
            print(f"  [OK]  {label}")
            print(f"    TotalRecords={total} | page={len(results)}{first}")
    except Exception as e:
        print(f"  [EXC]  {label}: {e}")

with sync_playwright() as pw:
    print("Launching browser...")
    browser = pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    ctx  = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-CA",
    )
    page = ctx.new_page()

    print("Loading realtor.ca...")
    page.goto("https://www.realtor.ca/", wait_until="networkidle", timeout=60000)
    time.sleep(3)
    print(f"Page: {page.url}\n")
    print("=" * 60)

    for label, extra in tests:
        run_test(page, label, extra)
        time.sleep(2)

    browser.close()

print("=" * 60)
print("Done. Paste the output above to identify the breaking parameter.")
