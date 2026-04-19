"""
Condo Tracker - SQLite Database Layer

Tables:
  listings         — one row per MLS number ever seen
  daily_snapshots  — one row per (mls_number, date) with price/status
  address_history  — tracks every MLS# seen at a normalized address (relist detection)
"""

import sqlite3
import re
from datetime import date
from config import DB_PATH


# ── Connection helper ─────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    mls_number          TEXT PRIMARY KEY,
    address_normalized  TEXT NOT NULL,
    address_raw         TEXT,
    city                TEXT,
    province            TEXT,
    postal_code         TEXT,
    latitude            REAL,
    longitude           REAL,
    building_type       TEXT,
    ownership_type      TEXT,

    -- unit characteristics
    sqft                REAL,
    bedrooms            INTEGER,
    bathrooms           REAL,
    parking_spaces      INTEGER,
    year_built          INTEGER,
    stories             INTEGER,

    -- financials (latest values; history in daily_snapshots)
    price               REAL,
    price_original      REAL,   -- price on first_seen_date
    maintenance_fee     REAL,   -- monthly strata/condo fee
    taxes_annual        REAL,   -- annual property tax

    -- computed
    price_per_sqft      REAL,
    deal_score          REAL,   -- 0–100
    dom                 INTEGER,   -- days on market (this listing)
    effective_dom       INTEGER,   -- dom across all relists at this address
    is_relist           INTEGER DEFAULT 0,   -- 1 if address had a prior MLS#
    prior_mls_number    TEXT,
    price_reduction_pct REAL,   -- % drop from original price

    -- metadata
    agent_name          TEXT,
    brokerage           TEXT,
    listing_url         TEXT,
    photo_count         INTEGER,
    has_virtual_tour    INTEGER DEFAULT 0,
    is_senior_flagged   INTEGER DEFAULT 0,   -- 1 = 55+ detected → excluded
    remarks_snippet     TEXT,   -- first 300 chars of public remarks

    first_seen_date     TEXT,   -- ISO YYYY-MM-DD
    last_seen_date      TEXT,
    is_active           INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mls_number      TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,   -- ISO YYYY-MM-DD
    price           REAL,
    maintenance_fee REAL,
    dom             INTEGER,
    is_active       INTEGER DEFAULT 1,
    UNIQUE(mls_number, snapshot_date),
    FOREIGN KEY (mls_number) REFERENCES listings(mls_number)
);

CREATE TABLE IF NOT EXISTS address_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    address_normalized  TEXT NOT NULL,
    mls_number          TEXT NOT NULL,
    first_seen_date     TEXT,
    last_seen_date      TEXT,
    initial_price       REAL,
    final_price         REAL,
    status              TEXT DEFAULT 'active',   -- active | expired | sold
    UNIQUE(address_normalized, mls_number)
);

CREATE INDEX IF NOT EXISTS idx_listings_address  ON listings(address_normalized);
CREATE INDEX IF NOT EXISTS idx_listings_active   ON listings(is_active);
CREATE INDEX IF NOT EXISTS idx_snapshots_date    ON daily_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_addr_history_addr ON address_history(address_normalized);
"""


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ── Address normalizer ────────────────────────────────────────────────────────

def normalize_address(raw: str) -> str:
    """
    Lower-case, strip unit/suite prefix variations, remove postal code,
    collapse whitespace so '302-2580 Langdon St' == '2580 langdon st abbotsford'.
    """
    if not raw:
        return ""
    # realtor.ca format: "302-2580 Langdon Street|Abbotsford, BC V2T 3L3"
    addr = raw.split("|")[0]   # drop city/province part after pipe
    addr = addr.lower()
    # strip unit prefix like "302-" or "Ph3-" or "#12 "
    addr = re.sub(r"^[\w#]+-", "", addr)
    addr = re.sub(r"^#\d+\s+", "", addr)
    # remove postal code
    addr = re.sub(r"[a-z]\d[a-z]\s*\d[a-z]\d", "", addr)
    # collapse whitespace
    addr = re.sub(r"\s+", " ", addr).strip()
    # city from after the pipe
    if "|" in raw:
        city_part = raw.split("|")[1]
        city = city_part.split(",")[0].strip().lower()
        addr = f"{addr} {city}"
    return addr


# ── Write helpers ─────────────────────────────────────────────────────────────

def upsert_listing(listing: dict):
    """Insert or update a listing record."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT price_original, first_seen_date FROM listings WHERE mls_number=?",
            (listing["mls_number"],)
        ).fetchone()

        if existing:
            # Preserve original price and first seen date
            listing.setdefault("price_original", existing["price_original"])
            listing.setdefault("first_seen_date", existing["first_seen_date"])
            conn.execute("""
                UPDATE listings SET
                    address_raw=:address_raw,
                    city=:city, province=:province, postal_code=:postal_code,
                    latitude=:latitude, longitude=:longitude,
                    sqft=:sqft, bedrooms=:bedrooms, bathrooms=:bathrooms,
                    parking_spaces=:parking_spaces, year_built=:year_built,
                    price=:price, maintenance_fee=:maintenance_fee,
                    taxes_annual=:taxes_annual, price_per_sqft=:price_per_sqft,
                    deal_score=:deal_score, dom=:dom, effective_dom=:effective_dom,
                    is_relist=:is_relist, prior_mls_number=:prior_mls_number,
                    price_reduction_pct=:price_reduction_pct,
                    agent_name=:agent_name, brokerage=:brokerage,
                    listing_url=:listing_url, photo_count=:photo_count,
                    has_virtual_tour=:has_virtual_tour,
                    is_senior_flagged=:is_senior_flagged,
                    remarks_snippet=:remarks_snippet,
                    last_seen_date=:last_seen_date, is_active=1
                WHERE mls_number=:mls_number
            """, listing)
        else:
            listing.setdefault("price_original", listing.get("price"))
            conn.execute("""
                INSERT OR IGNORE INTO listings (
                    mls_number, address_normalized, address_raw, city, province,
                    postal_code, latitude, longitude, building_type, ownership_type,
                    sqft, bedrooms, bathrooms, parking_spaces, year_built, stories,
                    price, price_original, maintenance_fee, taxes_annual,
                    price_per_sqft, deal_score, dom, effective_dom,
                    is_relist, prior_mls_number, price_reduction_pct,
                    agent_name, brokerage, listing_url, photo_count,
                    has_virtual_tour, is_senior_flagged, remarks_snippet,
                    first_seen_date, last_seen_date, is_active
                ) VALUES (
                    :mls_number, :address_normalized, :address_raw, :city, :province,
                    :postal_code, :latitude, :longitude, :building_type, :ownership_type,
                    :sqft, :bedrooms, :bathrooms, :parking_spaces, :year_built, :stories,
                    :price, :price_original, :maintenance_fee, :taxes_annual,
                    :price_per_sqft, :deal_score, :dom, :effective_dom,
                    :is_relist, :prior_mls_number, :price_reduction_pct,
                    :agent_name, :brokerage, :listing_url, :photo_count,
                    :has_virtual_tour, :is_senior_flagged, :remarks_snippet,
                    :first_seen_date, :last_seen_date, 1
                )
            """, listing)


def record_snapshot(mls_number: str, snapshot_date: str,
                    price: float, maintenance_fee: float, dom: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO daily_snapshots
                (mls_number, snapshot_date, price, maintenance_fee, dom)
            VALUES (?, ?, ?, ?, ?)
        """, (mls_number, snapshot_date, price, maintenance_fee, dom))


def update_address_history(address_norm: str, mls_number: str,
                            first_seen: str, price: float):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO address_history
                (address_normalized, mls_number, first_seen_date, last_seen_date, initial_price, final_price)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(address_normalized, mls_number) DO UPDATE SET
                last_seen_date=excluded.last_seen_date,
                final_price=excluded.final_price
        """, (address_norm, mls_number, first_seen, first_seen, price, price))


def mark_inactive_listings(active_mls_numbers: set, today: str):
    """Mark any previously active listing not seen today as inactive."""
    if not active_mls_numbers:
        return
    with get_conn() as conn:
        placeholders = ",".join("?" * len(active_mls_numbers))
        conn.execute(f"""
            UPDATE listings SET is_active=0, last_seen_date=?
            WHERE is_active=1 AND mls_number NOT IN ({placeholders})
        """, [today] + list(active_mls_numbers))
        # Update address_history for expired listings
        conn.execute(f"""
            UPDATE address_history SET status='expired', last_seen_date=?
            WHERE status='active' AND mls_number NOT IN ({placeholders})
        """, [today] + list(active_mls_numbers))


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_active_listings():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM listings
            WHERE is_active=1 AND is_senior_flagged=0
            ORDER BY deal_score DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_price_history(mls_number: str):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT snapshot_date, price, maintenance_fee, dom
            FROM daily_snapshots
            WHERE mls_number=?
            ORDER BY snapshot_date
        """, (mls_number,)).fetchall()
    return [dict(r) for r in rows]


def get_all_price_history():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ds.snapshot_date, ds.price, ds.dom,
                   l.address_raw, l.mls_number, l.city
            FROM daily_snapshots ds
            JOIN listings l ON l.mls_number = ds.mls_number
            WHERE l.is_senior_flagged=0
            ORDER BY ds.mls_number, ds.snapshot_date
        """).fetchall()
    return [dict(r) for r in rows]


def get_relist_alerts():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT l.*,
                   ah_old.mls_number   AS original_mls,
                   ah_old.initial_price AS original_price,
                   ah_old.first_seen_date AS original_list_date
            FROM listings l
            JOIN address_history ah_old
                ON ah_old.address_normalized = l.address_normalized
                AND ah_old.mls_number != l.mls_number
                AND ah_old.status != 'active'
            WHERE l.is_active=1 AND l.is_senior_flagged=0
            ORDER BY l.effective_dom DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_market_trends():
    """Daily summary stats for the trends sheet."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                ds.snapshot_date,
                COUNT(DISTINCT ds.mls_number)          AS active_count,
                ROUND(AVG(ds.price), 0)                AS avg_price,
                ROUND(MIN(ds.price), 0)                AS min_price,
                ROUND(MAX(ds.price), 0)                AS max_price,
                ROUND(AVG(l.price_per_sqft), 2)        AS avg_price_per_sqft,
                ROUND(AVG(ds.dom), 1)                  AS avg_dom,
                SUM(CASE WHEN l.is_relist=1 THEN 1 ELSE 0 END) AS relist_count
            FROM daily_snapshots ds
            JOIN listings l ON l.mls_number = ds.mls_number
            WHERE l.is_senior_flagged=0
            GROUP BY ds.snapshot_date
            ORDER BY ds.snapshot_date
        """).fetchall()
    return [dict(r) for r in rows]


def get_prior_address_mls(address_norm: str, current_mls: str):
    """Return the most recent inactive MLS# seen at this address, if any."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT mls_number, initial_price, first_seen_date
            FROM address_history
            WHERE address_normalized=? AND mls_number!=? AND status!='active'
            ORDER BY last_seen_date DESC
            LIMIT 1
        """, (address_norm, current_mls)).fetchone()
    return dict(row) if row else None


def get_today_price_drops(today: str):
    """Return listings where today's price < yesterday's price."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                l.address_raw, l.mls_number, l.city,
                today_s.price   AS price_today,
                yest_s.price    AS price_yesterday,
                (today_s.price - yest_s.price)          AS drop_amount,
                ROUND(100.0*(today_s.price - yest_s.price)/yest_s.price, 2) AS drop_pct,
                l.dom, l.deal_score, l.listing_url
            FROM daily_snapshots today_s
            JOIN daily_snapshots yest_s
                ON yest_s.mls_number = today_s.mls_number
               AND yest_s.snapshot_date = DATE(?, '-1 day')
            JOIN listings l ON l.mls_number = today_s.mls_number
            WHERE today_s.snapshot_date = ?
              AND today_s.price < yest_s.price
              AND l.is_senior_flagged = 0
            ORDER BY drop_pct ASC
        """, (today, today)).fetchall()
    return [dict(r) for r in rows]
