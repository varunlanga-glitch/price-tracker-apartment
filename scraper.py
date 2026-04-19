"""
Condo Tracker - realtor.ca Scraper

Uses Playwright (real Chromium browser) to call the realtor.ca API.
All API fetches run inside the browser via JavaScript, so Cloudflare
bot protection sees a real Chrome browser — no more 403s.

One-time setup:
    pip install playwright
    playwright install chromium
"""

import re
import time
import json
from datetime import date, datetime

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

from config import (
    SEARCH_AREAS, PRICE_MIN, PRICE_MAX, BED_MIN, BED_MAX,
    RECORDS_PER_PAGE, REQUEST_DELAY_SECONDS,
    SENIOR_KEYWORDS, MAX_BUILDING_AGE_YEARS,
)
from database import normalize_address

CURRENT_YEAR = date.today().year
MIN_YEAR_BUILT = CURRENT_YEAR - MAX_BUILDING_AGE_YEARS if MAX_BUILDING_AGE_YEARS > 0 else 0
API_URL    = "https://api2.realtor.ca/Listing.svc/PropertySearch_Post"
DETAIL_URL = "https://api2.realtor.ca/Listing.svc/PropertyDetails"


# ── Playwright browser fetch ──────────────────────────────────────────────────

def _browser_post(page, payload: dict) -> dict | None:
    """
    POST to the realtor.ca API using Playwright's page.request API.
    This uses the browser's cookie jar (including Cloudflare cf_clearance)
    without going through JavaScript — avoids CSP and 'Failed to fetch' errors.
    """
    try:
        resp = page.request.post(
            API_URL,
            form=payload,
            headers={
                "Referer":         "https://www.realtor.ca/map",
                "Origin":          "https://www.realtor.ca",
                "Accept":          "application/json, text/plain, */*",
                "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
            },
            timeout=30000,
        )
        if resp.status == 403:
            return {"_error": 403}
        if not resp.ok:
            return {"_error": resp.status}
        return resp.json()
    except Exception as e:
        print(f"  [WARN] request.post exception: {e}")
        return None


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_price(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(re.sub(r"[^\d.]", "", str(value)) or 0)


def _parse_sqft(value) -> float:
    if value is None:
        return 0.0
    m = re.search(r"[\d,]+\.?\d*", str(value).replace(",", ""))
    return float(m.group()) if m else 0.0


# .NET ticks epoch offset: ticks from 0001-01-01 to Unix epoch 1970-01-01
_DOTNET_TICKS_OFFSET = 621_355_968_000_000_000


def _parse_date(ms_date_str) -> str:
    """Parse realtor.ca date values robustly.

    Handles:
      - /Date(1712345678000)/   → Unix milliseconds wrapped in MS JSON format
      - 639120519381470000      → .NET ticks (100-ns intervals since 0001-01-01)
                                  realtor.ca's InsertedDateUTC uses this format
      - 1712345678000           → plain Unix milliseconds (13 digits)
      - 2024-04-05              → ISO date string
      - 2024-04-05T12:00:00Z   → ISO datetime string
      - anything else           → today's date
    """
    if not ms_date_str:
        return date.today().isoformat()
    s = str(ms_date_str).strip()

    # 1. Microsoft /Date(ms)/ wrapper
    m = re.search(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", s)
    if m:
        try:
            ts = int(m.group(1)) / 1000
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except (OSError, ValueError, OverflowError):
            pass

    # 2. Pure digit string — distinguish .NET ticks vs Unix ms vs Unix s
    if re.fullmatch(r"\d+", s):
        try:
            val = int(s)
            if val > 1e17:
                # .NET ticks: 100-nanosecond intervals since 0001-01-01
                unix_seconds = (val - _DOTNET_TICKS_OFFSET) / 10_000_000
            elif val > 1e10:
                # Unix milliseconds
                unix_seconds = val / 1000
            else:
                # Unix seconds
                unix_seconds = float(val)
            return datetime.utcfromtimestamp(unix_seconds).strftime("%Y-%m-%d")
        except (OSError, ValueError, OverflowError):
            pass

    # 3. ISO date / datetime string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS...)
    m2 = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m2:
        try:
            date.fromisoformat(m2.group(1))
            return m2.group(1)
        except ValueError:
            pass

    return date.today().isoformat()


def _parse_year(value) -> int:
    if not value:
        return 0
    m = re.search(r"\d{4}", str(value))
    return int(m.group()) if m else 0


def _parse_fee(value) -> float:
    """Parse a fee value, stripping currency symbols and /Monthly suffixes."""
    if value is None:
        return 0.0
    # Strip trailing text like " /Monthly", "/mo", etc.
    cleaned = re.sub(r"\s*/.*$", "", str(value)).strip()
    return _parse_price(cleaned)


def _deep_find_fields(obj, result: dict, depth: int = 0) -> None:
    """Recursively search a parsed JSON object for maintenance fee,
    year built, and taxes fields — used to mine realtor.ca __NEXT_DATA__."""
    if depth > 12 or obj is None:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower().replace("_", "").replace("-", "")
            # Maintenance / strata / condo fee
            if any(s in kl for s in ("maintenancefee", "condofee", "stratafee",
                                      "monthlyfee", "condominiumfee")):
                if v and str(v).strip() not in ("0", "0.0", "None", ""):
                    result.setdefault("maintenanceFee", str(v))
            # Year built
            if any(s in kl for s in ("yearbuilt", "constructeddate", "builtyear",
                                      "yearofconstruction")):
                if v:
                    result.setdefault("yearBuilt", str(v))
            # Age of building
            if any(s in kl for s in ("ageofbuilding", "buildingage", "buildingyear")):
                if v is not None:
                    val_str = f"{v} Years" if isinstance(v, (int, float)) else str(v)
                    result.setdefault("ageText", val_str)
            # Annual taxes
            if any(s in kl for s in ("annualtax", "propertytax", "taxamount",
                                      "annualtaxamount")):
                if v and str(v).strip() not in ("0", "0.0", "None", ""):
                    result.setdefault("taxes", str(v))
            # Recurse into nested dicts/lists
            if isinstance(v, (dict, list)):
                _deep_find_fields(v, result, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _deep_find_fields(item, result, depth + 1)


def _is_senior_listing(raw: dict) -> bool:
    text_fields = [
        raw.get("PublicRemarks", ""),
        raw.get("ZoningDescription", ""),
        raw.get("AdditionalInformationIndicator", ""),
        (raw.get("Building", {}) or {}).get("Name", "") or "",
        (raw.get("Property", {}) or {}).get("ZoningType", "") or "",
    ]
    combined = " ".join(str(f) for f in text_fields if f).lower()
    return any(kw in combined for kw in SENIOR_KEYWORDS)


# ── Debug helper (runs once per session on the first listing) ─────────────────

def _debug_first_listing(raw: dict):
    """Print key raw fields so we can confirm API field names for fees/dates."""
    prop     = raw.get("Property", {}) or {}
    building = raw.get("Building", {}) or {}
    print("\n  ── DEBUG: first raw listing field dump ──")
    print(f"  Building keys : {sorted(building.keys())}")
    print(f"  Property keys : {sorted(prop.keys())}")
    print(f"  Top-level keys: {sorted(k for k in raw if k not in ('Property','Building','Individual','Media'))}")
    # Specific values we care about
    for label, val in [
        ("Building.YearBuilt",       building.get("YearBuilt")),
        ("Building.ConstructedDate", building.get("ConstructedDate")),
        ("Property.MaintenanceFee",  prop.get("MaintenanceFee")),
        ("Property.CondomFees",      prop.get("CondomFees")),
        ("Property.StrataFee",       prop.get("StrataFee")),
        ("Property.Taxes",           prop.get("Taxes")),
        ("InsertedDateUTC",          raw.get("InsertedDateUTC")),
        ("ListingContractDate",      raw.get("ListingContractDate")),
    ]:
        print(f"    {label:<35} = {val!r}")
    print("  ── end debug ──\n")


# ── Listing parser ────────────────────────────────────────────────────────────

def _parse_listing(raw: dict, area_label: str) -> dict | None:
    prop     = raw.get("Property", {}) or {}
    building = raw.get("Building", {}) or {}
    address  = prop.get("Address", {}) or {}

    mls = raw.get("MlsNumber", "").strip()
    if not mls:
        return None

    # ── Building type safety filter ───────────────────────────────────────────
    # API-level params (PropertySearchTypeId=3, BuildingTypeId=17, OwnershipTypeGroupId=2)
    # handle the heavy lifting. Just hard-exclude obvious mismatches as a safety net.
    btype    = (building.get("Type") or "").lower()
    own_type = (prop.get("OwnershipType") or "").lower()
    HARD_EXCLUDE = ("single family", "detached", "vacant land", "agriculture")
    if any(t in f"{btype} {own_type}" for t in HARD_EXCLUDE):
        return None

    # ── 55+ check ────────────────────────────────────────────────────────────
    is_senior = _is_senior_listing(raw)

    # ── Core fields ───────────────────────────────────────────────────────────
    address_raw  = address.get("AddressText", "")
    address_norm = normalize_address(address_raw)

    price        = _parse_price(prop.get("PriceUnformattedValue") or prop.get("Price"))
    sqft         = _parse_sqft(building.get("SizeInterior"))

    # YearBuilt: realtor.ca search results often omit this; try several field names
    year_built   = (_parse_year(building.get("YearBuilt"))
                    or _parse_year(building.get("ConstructedDate"))
                    or _parse_year(raw.get("YearBuilt"))
                    or 0)

    # Maintenance fee: field name varies across listing types
    maint_fee    = _parse_fee(
        prop.get("MaintenanceFee")
        or prop.get("CondomFees")
        or prop.get("StrataFee")
        or raw.get("MaintenanceFee")
        or 0
    )

    taxes_annual = _parse_fee(prop.get("Taxes") or prop.get("AnnualTaxAmount") or 0)

    try:
        bedrooms = int(building.get("Bedrooms") or building.get("BedroomsTotal") or 0)
    except (ValueError, TypeError):
        bedrooms = 0

    try:
        bathrooms = float(building.get("BathroomTotal") or 0)
    except (ValueError, TypeError):
        bathrooms = 0.0

    try:
        parking = int(prop.get("ParkingSpaceTotal") or raw.get("ParkingSpaceTotal") or 0)
    except (ValueError, TypeError):
        parking = 0

    try:
        stories = int(building.get("StoriesTotal") or 0)
    except (ValueError, TypeError):
        stories = 0

    # ── Bedroom filter (client-side — BedRange triggers Cloudflare 403) ──────
    if bedrooms > 0 and (bedrooms < BED_MIN or bedrooms > BED_MAX):
        return None

    # Age filter is no longer a hard exclusion — older buildings are included
    # but highlighted in the Excel report (see MAX_BUILDING_AGE_YEARS in config.py).

    # ── City / postal code ────────────────────────────────────────────────────
    city_part   = address_raw.split("|")[1] if "|" in address_raw else ""
    city        = city_part.split(",")[0].strip() if city_part else area_label
    postal_code = ""
    m_postal = re.search(r"[A-Z]\d[A-Z]\s*\d[A-Z]\d", address_raw)
    if m_postal:
        postal_code = m_postal.group().replace(" ", "")

    price_per_sqft = round(price / sqft, 2) if sqft > 0 and price > 0 else 0.0

    # InsertedDateUTC is in .NET ticks — parse it to get the feed insertion date.
    # TimeOnRealtor is the exact integer realtor.ca displays as "X days on market".
    inserted_raw = raw.get("InsertedDateUTC") or ""
    first_seen   = _parse_date(inserted_raw)
    today_str    = date.today().isoformat()

    # Use TimeOnRealtor directly (matches what realtor.ca shows on the listing page).
    # Fall back to computing from the parsed insertion date if not present.
    time_on_realtor = raw.get("TimeOnRealtor")
    if time_on_realtor is not None:
        try:
            dom = max(0, int(time_on_realtor))
        except (ValueError, TypeError):
            dom = max(0, (date.today() - date.fromisoformat(first_seen)).days)
    else:
        dom = max(0, (date.today() - date.fromisoformat(first_seen)).days)

    individuals = raw.get("Individual", []) or []
    agent_name  = individuals[0].get("Name", "") if individuals else ""
    brokerage   = ""
    if individuals:
        org = (individuals[0].get("Organization") or {})
        brokerage = org.get("Name", "")

    rel_url     = raw.get("RelativeDetailsURL", "") or raw.get("RelativeURLEn", "")
    listing_url = f"https://www.realtor.ca{rel_url}" if rel_url else ""
    remarks     = (raw.get("PublicRemarks") or "")[:300]
    photo_count = int(raw.get("PhotoCount") or 0)
    has_vt      = 1 if raw.get("HasVirtualTour") else 0

    # Extract the numeric property ID from the listing URL.
    # listing_url is like https://www.realtor.ca/real-estate/29299151/some-title
    _pid_match   = re.search(r"/real-estate/(\d+)", listing_url)
    _property_id = _pid_match.group(1) if _pid_match else ""

    # Also keep the raw "Id" field from the search results — may differ from the
    # URL-embedded ID and might be what PropertyDetails actually expects.
    _raw_id = str(raw.get("Id") or "").strip()

    return {
        "mls_number":         mls,
        "address_normalized": address_norm,
        "address_raw":        address_raw,
        "city":               city,
        "province":           "BC",
        "postal_code":        postal_code,
        "latitude":           float(address.get("Latitude") or 0),
        "longitude":          float(address.get("Longitude") or 0),
        "building_type":      building.get("Type") or "Apartment",
        "ownership_type":     prop.get("OwnershipType") or "Condominium",
        "sqft":               sqft,
        "bedrooms":           bedrooms,
        "bathrooms":          bathrooms,
        "parking_spaces":     parking,
        "year_built":         year_built,
        "stories":            stories,
        "price":              price,
        "price_original":     price,
        "maintenance_fee":    maint_fee,
        "taxes_annual":       taxes_annual,
        "price_per_sqft":     price_per_sqft,
        "deal_score":         0.0,
        "dom":                dom,
        "effective_dom":      dom,
        "is_relist":          0,
        "prior_mls_number":   None,
        "price_reduction_pct":0.0,
        "agent_name":         agent_name,
        "brokerage":          brokerage,
        "listing_url":        listing_url,
        "photo_count":        photo_count,
        "has_virtual_tour":   has_vt,
        "is_senior_flagged":  1 if is_senior else 0,
        "remarks_snippet":    remarks,
        "first_seen_date":    first_seen,
        "last_seen_date":     today_str,
        "is_active":          1,
        # Internal — consumed by _enrich_with_details, never written to DB
        "_property_id":       _property_id,
        "_raw_id":            _raw_id,
    }


# ── Detail page fetcher ───────────────────────────────────────────────────────

def _fetch_detail(page, property_id: str, raw_id: str, mls: str,
                  _log_first: list = None) -> dict | None:
    """Call PropertyDetails to get maintenance fee, year built, and taxes.

    Confirmed working call (GET with BOTH PropertyID AND ReferenceNumber):
        GET api2.realtor.ca/Listing.svc/PropertyDetails
            ?PropertyID=<numeric_id>&ReferenceNumber=<MLS>&CultureId=1
            &ApplicationId=1&Version=7.0

    Neither ID alone works — both must be present together.
    The response carries a UTF-8 BOM so we decode with utf-8-sig.
    """
    if not property_id or not mls:
        return None

    do_log = _log_first is not None and not _log_first

    try:
        resp = page.request.get(
            DETAIL_URL,
            params={
                "PropertyID":      property_id,
                "ReferenceNumber": mls,
                "CultureId":       "1",
                "ApplicationId":   "1",
                "Version":         "7.0",
            },
            headers={
                "Referer":         "https://www.realtor.ca/map",
                "Origin":          "https://www.realtor.ca",
                "Accept":          "application/json, text/plain, */*",
                "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
            },
            timeout=15000,
        )

        text = resp.body().decode("utf-8-sig").strip()
        if not text:
            return None

        data     = json.loads(text)
        error_id = str((data.get("ErrorCode") or {}).get("Id", ""))
        if error_id not in ("200", ""):
            return None

        extracted: dict = {}
        _deep_find_fields(data, extracted)

        if do_log:
            _log_first.append(True)
            print(f"\n  ── Detail API check (first listing) ──")
            print(f"  PropertyID={property_id}  MLS={mls}")
            print(f"  Extracted: {extracted}")
            print(f"  ──────────────────────────────────────\n")

        return {"_dom": extracted} if any(extracted.values()) else None

    except Exception as exc:
        if do_log and not _log_first:
            _log_first.append(True)
            print(f"  [WARN] _fetch_detail({property_id}, {mls}): {exc}")
        return None


def _enrich_with_details(page, listings: list[dict]) -> list[dict]:
    """Fetch the detail page for every listing to fill in maintenance fee,
    year built, and annual taxes. Also re-applies the age filter now that
    we have real YearBuilt data from the detail page."""
    total = len(listings)
    print(f"  Fetching details for {total} listings "
          f"(maintenance fees, year built, taxes)...")

    enriched = []
    _log_first = []   # triggers one-time debug print on the first call

    for i, lst in enumerate(listings, 1):
        property_id = lst.pop("_property_id", "")   # consume — never reaches DB
        raw_id      = lst.pop("_raw_id", "")        # consume — never reaches DB
        mls         = lst.get("mls_number", "")

        detail = _fetch_detail(
            page, property_id, raw_id, mls,
            _log_first=_log_first
        ) if (property_id or raw_id or mls) else None

        if detail:
            dom = detail.get("_dom", {})

            # Maintenance fee — value like "$504.80 Monthly" or a raw number
            mf = _parse_fee(dom.get("maintenanceFee") or 0)
            if mf > 0:
                lst["maintenance_fee"] = mf

            # Year built — try direct yearBuilt key first (from __NEXT_DATA__),
            # then derive from "Age Of Building: 54 Years" text
            yb = 0
            if dom.get("yearBuilt"):
                yr_m = re.search(r"(\d{4})", str(dom["yearBuilt"]))
                if yr_m:
                    yb = int(yr_m.group(1))

            if not yb and dom.get("ageText"):
                age_text = dom["ageText"]
                yr_m = re.search(r"\b(19|20)\d{2}\b", age_text)   # 4-digit year
                if yr_m:
                    yb = int(yr_m.group(0))
                else:
                    age_m2 = re.search(r"(\d+)\s*year", age_text, re.IGNORECASE)
                    if age_m2:
                        yb = CURRENT_YEAR - int(age_m2.group(1))

            if yb > 1900:
                lst["year_built"] = yb
                # No hard exclusion — older buildings stay in the results
                # and get highlighted in Excel (controlled by MAX_BUILDING_AGE_YEARS).

            # Annual taxes — value like "$1,635.08" or a raw number
            tx = _parse_fee(dom.get("taxes") or 0)
            if tx > 0:
                lst["taxes_annual"] = tx

        enriched.append(lst)

        if i % 10 == 0:
            print(f"    {i}/{total} fetched")

    print(f"  Details done. {len(enriched)}/{total} listings enriched.")
    return enriched


# ── Scrape one area (called with an active Playwright page) ───────────────────

def scrape_area_with_page(page, area: dict) -> list[dict]:
    """Scrape all pages for one area using the given Playwright page."""
    results = []
    pg = 1

    while True:
        payload = {
            "CultureId":             "1",
            "ApplicationId":         "1",    # web app ID (37 = mobile, requires auth)
            "RecordsPerPage":        str(RECORDS_PER_PAGE),
            "MaximumResults":        str(RECORDS_PER_PAGE),
            "PropertySearchTypeId":  "1",    # 1 = Residential (3=Condo needs extra auth)
            "TransactionTypeId":     "2",    # 2 = For Sale
            "OwnershipTypeGroupId":  "2",    # 2 = Condo/Strata ownership filter
            "BuildingTypeId":        "17",   # 17 = Apartment (was wrongly 2=Duplex)
            "PriceMin":              str(PRICE_MIN),
            "PriceMax":              str(PRICE_MAX),
            "BedRange":              f"{BED_MIN}-{BED_MAX}",  # "1-1" = exactly 1BR
            "SortBy":                "6",    # 6 = Date Posted
            "SortOrder":             "D",    # D = Descending (newest first)
            "LatitudeMax":           str(area["lat_max"]),
            "LatitudeMin":           str(area["lat_min"]),
            "LongitudeMax":          str(area["lon_max"]),
            "LongitudeMin":          str(area["lon_min"]),
            "CurrentPage":           str(pg),
            "Version":               "7.0",
        }

        data = _browser_post(page, payload)

        if data is None:
            print(f"  [WARN] No response for {area['label']} page {pg}")
            break

        if "_error" in data:
            print(f"  [WARN] API error {area['label']} page {pg}: {data['_error']}")
            break

        error_code   = data.get("ErrorCode", {})
        paging       = data.get("Paging", {})
        total        = int(paging.get("TotalRecords", 0) or 0)
        raw_results  = data.get("Results", []) or []

        if str(error_code.get("Id", "")) not in ("200", ""):
            print(f"  [WARN] API error for {area['label']}: {error_code}")
            break

        if not raw_results:
            print(f"  [DEBUG] API returned 0 raw results. TotalRecords={total} "
                  f"ErrorCode={error_code.get('Id')}")
            break

        filtered_in = 0
        for raw in raw_results:
            listing = _parse_listing(raw, area["label"])
            if listing:
                results.append(listing)
                filtered_in += 1

        print(f"  [INFO] Page {pg}: {filtered_in}/{len(raw_results)} kept (TotalRecords={total})")

        fetched = pg * RECORDS_PER_PAGE
        if fetched >= total or len(raw_results) < RECORDS_PER_PAGE:
            break

        pg += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    # Enrich every listing with detail-page data (maintenance fee, year built, taxes)
    if results:
        results = _enrich_with_details(page, results)

    return results


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_all(areas=None) -> list[dict]:
    """
    Scrape all configured areas using a Playwright Chromium browser.
    All areas share one browser session (one Cloudflare clearance).
    Returns a deduplicated list of parsed listing dicts.
    """
    if not _HAS_PLAYWRIGHT:
        print("\n  [!] Playwright is not installed.")
        print("      Run these two commands, then try again:\n")
        print("          pip install playwright")
        print("          playwright install chromium\n")
        return []

    if areas is None:
        areas = SEARCH_AREAS

    seen_mls    = set()
    all_listings = []

    with sync_playwright() as pw:
        print("  Launching browser (Chromium)...")
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-CA",
            timezone_id="America/Vancouver",
        )
        page = context.new_page()

        # Navigate to realtor.ca — must fully load so Cloudflare issues cf_clearance cookie
        print("  Loading realtor.ca (Cloudflare clearance)...")
        try:
            page.goto("https://www.realtor.ca/", wait_until="networkidle", timeout=60000)
            time.sleep(3)
            cur = page.url
            print(f"  Page settled at: {cur}")
        except Exception as e:
            print(f"  [WARN] Homepage load: {e} — continuing anyway")

        for i, area in enumerate(areas):
            print(f"  [{i+1}/{len(areas)}] Scraping {area['label']}...")
            listings = scrape_area_with_page(page, area)

            for l in listings:
                if l["mls_number"] not in seen_mls:
                    seen_mls.add(l["mls_number"])
                    all_listings.append(l)

            print(f"    → {len(listings)} raw  |  {len(all_listings)} unique total")

            if i < len(areas) - 1:
                time.sleep(REQUEST_DELAY_SECONDS * 2)

        browser.close()

    senior_count = sum(1 for l in all_listings if l["is_senior_flagged"])
    print(f"\n  Done. Total unique: {len(all_listings)} | 55+ excluded: {senior_count}")
    return all_listings
