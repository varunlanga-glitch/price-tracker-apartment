╔══════════════════════════════════════════════════════════════════╗
║         ABBOTSFORD / MISSION / LANGLEY  CONDO TRACKER           ║
║         1-Bedroom  |  Under $450K  |  Built 2011+               ║
╚══════════════════════════════════════════════════════════════════╝

QUICK START
───────────
1. Make sure Python is installed (python.org — version 3.10 or newer)

2. Open a terminal (Command Prompt on Windows, Terminal on Mac)
   and navigate to this folder:
      cd "C:\Users\YourName\...\Condo Research"   (Windows)
      cd "/Users/YourName/.../Condo Research"     (Mac)

3. Install required packages (one time only):
      pip install requests openpyxl

4. Run the tracker:
      python run.py

5. Your report is saved to:
      reports\condo_tracker_YYYY-MM-DD.xlsx

Run it once per day for best results. The more history you build,
the more powerful the relist detection and trend charts become.


WHAT EACH SHEET DOES
─────────────────────
  1. Active Listings   — Full sortable/filterable master list of all current
                         1BR condos. Sorted by Deal Score (best first).
                         Amber rows = relisted properties (disguised drops).

  2. Top Deals         — Only listings with Deal Score >= 65. Best value
                         units at a glance.

  3. Relist Alerts     — THE KEY SHEET. Listings where realtor.ca shows
                         a fresh MLS# but our tracker has seen a prior
                         MLS# at the same address that expired. This reveals
                         the REAL days on market and REAL price reduction.

  4. Price Drops Today — Any listing that dropped in price since yesterday's
                         run. Good for daily monitoring.

  5. Price History Log — Every daily price recorded per listing. Shows the
                         full journey of each unit over time.

  6. Market Trends     — Day-by-day market stats: total inventory, avg price,
                         avg DOM, relist count.

  7. Buyer's Guide     — Column explanations, realtor tricks to watch for,
                         due diligence checklist.


DEAL SCORE EXPLAINED (0–100)
─────────────────────────────
  40% — Price per sqft vs market (cheaper = better)
  25% — Total price reduction from original/first listed price
  20% — Days on market / motivated seller signal
  10% — Monthly maintenance fee (lower = better)
   5% — Year built (newer = better)

  Score 70+ : Strong value signal — worth viewing
  Score 85+ : Exceptional — act quickly


RELIST DETECTION (the most important feature)
──────────────────────────────────────────────
Agents sometimes let a listing expire and relist it at a new MLS#.
On realtor.ca, the new listing shows "1 day on market" but the seller
has actually been trying to sell for 60+ days.

This tracker catches it by:
  1. Normalizing every address (strips unit#, postal code)
  2. Recording each MLS# seen at each address over time
  3. When a new MLS# appears at an old address → relist flagged
  4. "Effective DOM" shows the true total days
  5. "Original Price" shows the first listed price (even from prior MLS#)

This works better with more data. After 2-3 weeks the database
builds enough history to catch most relists in the area.


AUTOMATING DAILY RUNS
──────────────────────
WINDOWS (Task Scheduler):
  1. Open Task Scheduler → Create Basic Task
  2. Trigger: Daily at 8:00 AM
  3. Action: Start a Program
     Program: python
     Arguments: run.py
     Start in: C:\path\to\Condo Research

MAC / LINUX (cron):
  Open terminal, type:  crontab -e
  Add this line (runs at 8am daily):
    0 8 * * * cd "/path/to/Condo Research" && python3 run.py


FILES IN THIS FOLDER
─────────────────────
  run.py           — Run this daily (main entry point)
  config.py        — Change areas, price range, filters here
  scraper.py       — realtor.ca API scraper
  database.py      — SQLite database operations
  analyzer.py      — Relist detection + deal scoring
  report.py        — Excel report generator
  condo_tracker.db — Your persistent database (grows daily)
  reports/         — Generated Excel files (one per day)
  README.txt       — This file


CONFIGURATION
─────────────
Edit config.py to change:
  SEARCH_AREAS     — Add/remove cities
  PRICE_MAX        — Change price ceiling (currently $450,000)
  BED_MIN/BED_MAX  — Change bedroom count (currently 1-1)
  MAX_BUILDING_AGE_YEARS — Currently 15 years (built 2011+)
  SCORE_WEIGHTS    — Adjust what matters most in deal scoring


TROUBLESHOOTING
───────────────
"No listings returned":
  realtor.ca may occasionally block scrape requests if you run
  too frequently. Wait 30 minutes and try again. Running once
  per day is the recommended cadence.

"Module not found":
  Run:  pip install requests openpyxl

"Permission error on database":
  Make sure you're running from the correct folder.
  The database (condo_tracker.db) must be in the same folder as run.py.


DATA NOTES
──────────
  - Only listings on realtor.ca MLS are captured (not private sales,
    Zillow exclusive, etc.)
  - Maintenance fees and taxes may be missing from some listings
    (shown as $0) — always verify with the listing agent
  - Square footage from MLS sometimes includes balconies — verify
    with the strata plan before making an offer
  - 55+ age-restricted listings are automatically excluded
  - Buildings older than 15 years are automatically excluded


VERSION
───────
Built: April 2026
Source: realtor.ca unofficial API (browser-based, no auth required)
