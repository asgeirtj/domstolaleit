#!/usr/bin/env python3
"""Fetch verdict URLs from court websites and add to database.

This script crawls court search pages to collect direct verdict URLs
and stores them in the database for use in local search results.
"""

import asyncio
import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "verdicts.db"
URLS_CACHE = DATA_DIR / "verdict_urls.json"

COURTS = {
    "heradsdomstolar": {
        "url": "https://www.heradsdomstolar.is",
        "page_id": "deb3ce16-7d66-11e5-80c6-005056bc6a40",
        "link_class": "a.sentence",
    },
    "landsrettur": {
        "url": "https://www.landsrettur.is",
        "page_id": "deb3ce16-7d66-11e5-80c6-005056bc6a40",
        "link_class": "a.casenumber",
    },
    "haestirettur": {
        "url": "https://www.haestirettur.is",
        "page_id": "deb3ce16-7d66-11e5-80c6-005056bc6a40",
        "link_class": "a.casenumber",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}


def normalize_case_number(case_number: str) -> str:
    """Normalize case number for matching."""
    # Remove spaces and convert to lowercase
    normalized = case_number.strip().lower()
    # Replace common separators with underscore
    normalized = re.sub(r'[\s/]', '_', normalized)
    # Collapse multiple underscores to single (Hæstiréttur uses ___ in filenames)
    normalized = re.sub(r'_+', '_', normalized)
    return normalized


def month_ranges(start_year: int = 2010) -> list[tuple[date, date]]:
    """Generate (start, end) date tuples for each month."""
    ranges = []
    current = date(start_year, 1, 1)
    today = date.today()

    while current <= today:
        month_start = current
        if current.month == 12:
            month_end = date(current.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(current.year, current.month + 1, 1) - timedelta(days=1)

        ranges.append((month_start, min(month_end, today)))

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return ranges


async def fetch_urls_for_period(
    client: httpx.AsyncClient,
    court_name: str,
    court: dict,
    from_date: date,
    to_date: date,
) -> dict[str, str]:
    """Fetch case URLs for a specific date range."""
    params = {
        "pageid": court["page_id"],
        "searchaction": "search",
        "Verdict": "*",
        "FromDate": from_date.strftime("%d.%m.%Y"),
        "ToDate": to_date.strftime("%d.%m.%Y"),
        "PageSize": "1000",
    }

    base_url = court["url"]
    response = await client.get(f"{base_url}/", params=params)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    urls = {}

    for result_div in soup.select("div.result"):
        link = result_div.select_one(court["link_class"])
        if not link:
            continue

        href = link.get("href", "")
        case_number_elem = link.select_one("h2")
        case_number = case_number_elem.get_text(strip=True) if case_number_elem else ""

        if case_number and href:
            full_url = urljoin(base_url, href)
            # Store with normalized key for matching
            key = f"{court_name}:{normalize_case_number(case_number)}"
            urls[key] = full_url

    return urls


async def fetch_court_urls(client: httpx.AsyncClient, court_name: str) -> dict[str, str]:
    """Fetch all URLs from a court."""
    court = COURTS[court_name]
    ranges = month_ranges()
    all_urls = {}

    print(f"\n{'='*60}")
    print(f"  {court_name.upper()}")
    print(f"{'='*60}")

    for i, (from_date, to_date) in enumerate(ranges):
        month_label = from_date.strftime("%Y-%m")
        print(f"[{i+1}/{len(ranges)}] {month_label}...", end=" ", flush=True)

        try:
            urls = await fetch_urls_for_period(client, court_name, court, from_date, to_date)
            all_urls.update(urls)
            print(f"{len(urls)} cases")
        except Exception as e:
            print(f"Error: {e}")

        await asyncio.sleep(0.1)

    print(f"Total URLs for {court_name}: {len(all_urls)}")
    return all_urls


def add_url_column_if_missing(conn: sqlite3.Connection):
    """Add verdict_url column to database if it doesn't exist."""
    cursor = conn.execute("PRAGMA table_info(verdicts)")
    columns = [row[1] for row in cursor.fetchall()]

    if "verdict_url" not in columns:
        print("Adding verdict_url column to database...")
        conn.execute("ALTER TABLE verdicts ADD COLUMN verdict_url TEXT")
        conn.commit()


def update_database_urls(urls: dict[str, str]):
    """Update database with collected URLs."""
    conn = sqlite3.connect(DB_PATH)
    add_url_column_if_missing(conn)

    cursor = conn.execute("SELECT id, court, case_number FROM verdicts")
    rows = cursor.fetchall()

    updated = 0
    for row_id, court, case_number in rows:
        key = f"{court}:{normalize_case_number(case_number)}"
        if key in urls:
            conn.execute(
                "UPDATE verdicts SET verdict_url = ? WHERE id = ?",
                (urls[key], row_id)
            )
            updated += 1

    conn.commit()
    conn.close()

    print(f"\nUpdated {updated} records with URLs")


async def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    # Load cached URLs if available
    all_urls = {}
    if URLS_CACHE.exists():
        print(f"Loading cached URLs from {URLS_CACHE}")
        all_urls = json.loads(URLS_CACHE.read_text())
        print(f"Loaded {len(all_urls)} cached URLs")

    # Get courts from command line or do all
    if len(sys.argv) > 1:
        courts = sys.argv[1:]
    else:
        courts = list(COURTS.keys())

    print(f"Fetching URLs from: {', '.join(courts)}")

    async with httpx.AsyncClient(headers=HEADERS, timeout=60.0, follow_redirects=True) as client:
        for court_name in courts:
            if court_name not in COURTS:
                print(f"Unknown court: {court_name}")
                continue

            urls = await fetch_court_urls(client, court_name)
            all_urls.update(urls)

    # Save cache
    URLS_CACHE.write_text(json.dumps(all_urls, ensure_ascii=False, indent=2))
    print(f"\nSaved {len(all_urls)} URLs to {URLS_CACHE}")

    # Update database
    update_database_urls(all_urls)


if __name__ == "__main__":
    asyncio.run(main())
