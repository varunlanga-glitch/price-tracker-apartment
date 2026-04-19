---
name: realtor-ca-scraper
description: >
  Knowledge base and gotcha guide for scraping realtor.ca listings using
  Playwright + the unofficial realtor.ca API. Covers working API endpoints,
  bot-protection layers, date formats, deal scoring, and all hard-won lessons
  from building the Abbotsford/Mission/Langley condo tracker.
project: Condo Research tracker (run.py)
---

# realtor.ca Scraper — Complete Knowledge Base

## 1. Architecture Overview

```
run.py  →  scraper.py  →  analyzer.py  →  report.py
              ↕                ↕
          database.py      config.py
```

- **scraper.py** — Playwright Chromium browser fetches the realtor.ca search
  API and the PropertyDetails API.  Both go through `page.request.*` (HTTP
  layer), NOT `page.goto()` navigation.
- **analyzer.py** — relist detection, effective DOM, price-reduction %, deal
  score 0–100.
- **report.py** — multi-sheet Excel workbook via openpyxl.
- **database.py** — SQLite; one row per MLS# in `listings`, daily snapshot
  history in `daily_snapshots`, address-level relist tracking in
  `address_history`.

---

## 2. API Endpoints

### 2a. Property Search (working ✅)

```
POST https://api2.realtor.ca/Listing.svc/PropertySearch_Post
Content-Type: application/x-www-form-urlencoded
```

Key form parameters:

| Parameter | Value | Notes |
|---|---|---|
| `CultureId` | `1` | English |
| `ApplicationId` | `1` | Web app. `37` = mobile (requires auth) |
| `RecordsPerPage` | `200` | Max per page |
| `PropertySearchTypeId` | `1` | Residential |
| `TransactionTypeId` | `2` | For Sale |
| `OwnershipTypeGroupId` | `2` | Condo/Strata |
| `BuildingTypeId` | `17` | Apartment. `2` = Duplex (wrong!) |
| `PriceMin` / `PriceMax` | numbers | |
| `BedRange` | `"1-1"` | Do NOT use in direct HTTP call — triggers Cloudflare 403. Filter client-side instead. |
| `LatitudeMin/Max` | floats | Bounding box |
| `LongitudeMin/Max` | floats | Bounding box (negative for BC) |
| `SortBy` | `6` | Date posted |
| `SortOrder` | `D` | Descending |
| `CurrentPage` | `1`, `2`, … | |
| `Version` | `7.0` | |

Response shape:
```json
{
  "ErrorCode": {"Id": 200},
  "Paging": {"TotalRecords": 78},
  "Results": [ { ...listing... } ]
}
```

### 2b. Property Details (working ✅)

```
GET https://api2.realtor.ca/Listing.svc/PropertyDetails
    ?PropertyID=<numeric_id>
    &ReferenceNumber=<MLS_number>
    &CultureId=1&ApplicationId=1&Version=7.0
```

**CRITICAL**: Both `PropertyID` AND `ReferenceNumber` must be present.
Neither works alone:
- `PropertyID` alone → `400 "Invalid Mls Reference Number"`
- `ReferenceNumber` alone → `400 "Invalid PropertyID"`
- Both together → `200` with full listing detail

`PropertyID` = the numeric ID embedded in the listing URL:
`https://www.realtor.ca/real-estate/**29622267**/title`

`ReferenceNumber` = the MLS# string (e.g., `R3112047`).

**UTF-8 BOM**: The response body has a UTF-8 BOM (`\xef\xbb\xbf`).
`resp.json()` will throw `"Unexpected UTF-8 BOM"`.
Fix: `json.loads(resp.body().decode("utf-8-sig"))`.

Response contains `Building`, `Property`, `Land`, `Individual` sub-objects
with maintenance fees, year built, taxes, etc.

---

## 3. Bot Protection Layers

realtor.ca uses **two separate** bot-protection systems:

| Layer | Protects | Can bypass? |
|---|---|---|
| **Cloudflare** | `api2.realtor.ca` API endpoints | Yes — load `https://www.realtor.ca/` first in Chromium to get `cf_clearance` cookie; then use `page.request.get/post()` which carries that cookie |
| **Incapsula / Imperva** | `www.realtor.ca` listing pages | No — triggers Geetest CAPTCHA (`new_captcha: true`) in headless Chromium; page.goto() yields empty body |

### What works

```python
# Load homepage ONCE — gets cf_clearance cookie
page.goto("https://www.realtor.ca/", wait_until="networkidle", timeout=60000)
time.sleep(3)

# All subsequent API calls use page.request.* (same cookie jar)
resp = page.request.post(API_URL, form=payload, headers={...})
resp = page.request.get(DETAIL_URL, params={...}, headers={...})
```

### What does NOT work

```python
# page.goto() to individual listing pages → Incapsula Geetest CAPTCHA
# body is empty (0 chars), title is ''
page.goto("https://www.realtor.ca/real-estate/12345/...", ...)
page.inner_text("body")  # returns ""

# Raw HTTP GET to listing page → Incapsula block page (920 bytes)
# "Request unsuccessful. Incapsula incident ID: ..."
page.request.get("https://www.realtor.ca/real-estate/...")

# PropertyDetails with only one ID parameter → 400 error
page.request.get(DETAIL_URL, params={"PropertyID": "29622267"})  # fails
```

---

## 4. Key Data Field Gotchas

### 4a. InsertedDateUTC — .NET Ticks, not Unix ms

The `InsertedDateUTC` field returns a value like `639120519381470000` — this
is **.NET ticks** (100-nanosecond intervals since `0001-01-01`), not Unix ms.

```python
_DOTNET_TICKS_OFFSET = 621_355_968_000_000_000   # ticks from 0001-01-01 to 1970-01-01
unix_seconds = (val - _DOTNET_TICKS_OFFSET) / 10_000_000
datetime.utcfromtimestamp(unix_seconds)
```

If you do `val / 1000` (treating it as Unix ms), you get an invalid timestamp
that crashes on Windows with `OSError: [Errno 22] Invalid argument`.

### 4b. TimeOnRealtor — use this for DOM, not computed dates

`raw["TimeOnRealtor"]` is an integer that realtor.ca displays directly as
"X days on market". Use it instead of computing from `InsertedDateUTC`:

```python
time_on_realtor = raw.get("TimeOnRealtor")
if time_on_realtor is not None:
    dom = max(0, int(time_on_realtor))
```

### 4c. Maintenance fee / year built / taxes NOT in search results

The search API returns `Building.YearBuilt = None`, `Property.MaintenanceFee = None`,
`Property.Taxes = None` for almost every listing. These fields only come from
the PropertyDetails endpoint (see §2b above).

### 4d. BedRange parameter triggers Cloudflare 403

Do NOT include `BedRange` in the search payload for direct HTTP calls.
Filter bedrooms client-side after the API returns results.

### 4e. Field names in the search result

Top-level fields include: `Id`, `MlsNumber`, `TimeOnRealtor`, `InsertedDateUTC`,
`RelativeDetailsURL`, `PublicRemarks`, `PhotoCount`, `HasVirtualTour`,
`Building`, `Property`, `Individual`, `Media`, `Land`, `Business`.

`Building` sub-keys: `Type`, `Bedrooms`, `BathroomTotal`, `SizeInterior`,
`StoriesTotal`, `YearBuilt` (usually None).

`Property` sub-keys: `Price`, `PriceUnformattedValue`, `OwnershipType`,
`Address`, `MaintenanceFee` (usually None), `Taxes` (usually None),
`ParkingSpaceTotal`.

---

## 5. Deal Score Weights

Defined in `config.py` (`SCORE_WEIGHTS`); must sum to 1.0.

| Factor | Weight | Logic |
|---|---|---|
| `price_per_sqft` | 0.35 | Percentile rank, inverted — cheapest = 1.0 |
| `price_reduction` | 0.20 | `min(drop_pct / 15, 1.0)` — 15% drop = full score |
| `dom` | 0.20 | DOM 21–90 days = sweet spot; ramps from 0.3 → 1.0 |
| `monthly_cost` | 0.15 | `maintenance_fee + taxes_annual/12`, percentile rank, inverted |
| `year_built` | 0.10 | Percentile rank — newest = 1.0 |

`monthly_cost` combines maintenance fee + monthly property tax so that a
high-fee/low-tax condo is scored the same as a low-fee/high-tax condo.

---

## 6. 55+ Senior Detection

Defined in `config.py` (`SENIOR_KEYWORDS`). Only use **tight, unambiguous**
phrases. The following are intentionally excluded because they appear in
normal condo listings:

❌ `"retirement"`, `"adult living"`, `"adult community"`, `"no children"`,
   `"adult-only"`, `"adults only"`, `"adult oriented"`, `"no minors"`

✅ Keep: `"55+"`, `"55 and over"`, `"age restricted"`, `"age restriction"`,
   `"seniors only"`, `"senior community"`, `"senior living"`, `"seniors living"`

---

## 7. Age Filter — Soft, Not Hard

`MAX_BUILDING_AGE_YEARS` in `config.py` is a **highlight threshold**, not a
hard exclusion. Buildings older than the threshold are included in results
but get their "Year Built" cell highlighted amber in the Excel report.

The only hard exclusion is the 55+ senior flag.

To hide old buildings: use Excel's column filter on "Year Built".
To change threshold: edit `MAX_BUILDING_AGE_YEARS` (set `0` to disable).

---

## 8. Playwright Setup Pattern

```python
with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
        locale="en-CA",
        timezone_id="America/Vancouver",
    )
    page = context.new_page()

    # 1. Load homepage for Cloudflare clearance
    page.goto("https://www.realtor.ca/", wait_until="networkidle", timeout=60000)
    time.sleep(3)

    # 2. All API calls via page.request (carries cf_clearance)
    resp = page.request.post(API_URL, form=payload, headers=HEADERS)
    data = json.loads(resp.body().decode("utf-8-sig"))   # strip BOM!
```

---

## 9. Excel Report Notes (openpyxl)

- Hyperlinks: `cell.hyperlink = url` + `Font(color="0563C1", underline="single")`
- PermissionError on save if Excel has the file open: catch `PermissionError`
  and retry with a suffix (`_1.xlsx`, `_2.xlsx`, …)
- FormulaRule for two-condition conditional formatting:
  ```python
  FormulaRule(formula=["AND(I4>0,I4<2011)"], fill=..., font=...)
  ```
- Color scale for deal score: `ColorScaleRule(start_type="num", start_value=0,
  start_color="F8696B", mid_value=50, mid_color="FFEB84", end_value=100,
  end_color="63BE7B")`

---

## 10. Running the Tracker

```bash
# Test — Abbotsford only (~3–5 min)
python run.py --test

# Full run — all 3 cities (~10–15 min)
python run.py
```

First run: no historical data, so relist detection and trend charts are sparse.
After 2–3 weeks: price history, relists, and deal scores become meaningful.

One-time setup:
```bash
pip install playwright openpyxl
playwright install chromium
```

---

## 11. Files at a Glance

| File | Purpose |
|---|---|
| `config.py` | All tunable parameters — edit here first |
| `scraper.py` | Playwright API calls, listing parser, detail enrichment |
| `analyzer.py` | Relist detection, deal score computation |
| `database.py` | SQLite schema + all DB read/write functions |
| `report.py` | Excel workbook generation (openpyxl) |
| `run.py` | Orchestrates the full pipeline |
| `condo_tracker.db` | SQLite database (persists between runs) |
| `reports/` | Generated Excel files (one per run date) |
