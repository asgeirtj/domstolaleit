#!/usr/bin/env python3
"""Fetch verdict URLs from court websites and add to database.

Uses the same offset pagination as download_all.py to collect direct
verdict URLs for all courts, then matches them to database records
by normalized case number.
"""

import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "verdicts.db"
URLS_CACHE = DATA_DIR / "verdict_urls.json"

COURTS = {
    "landsrettur": {
        "base_url": "https://landsrettur.is",
        "list_url": "/domar-og-urskurdir/$Verdicts/Index/",
        "pageitemid": "5cf6e850-20b6-11e9-85de-94b86df896cb",
        "page_size": 12,
        "link_selector": "a.casenumber",
        "use_offset": True,
    },
    "heradsdomstolar": {
        "base_url": "https://www.heradsdomstolar.is",
        "list_url": "/default.aspx",
        "pageitemid": "e7fc58af-8d46-11e5-80c6-005056bc6a40",
        "page_size": 20,
        "link_selector": "a.sentence",
        "use_offset": True,
    },
    "haestirettur": {
        "base_url": "https://www.haestirettur.is",
        "list_url": "/default.aspx",
        "pageitemid": "4468cca6-a82f-11e5-9402-005056bc2afe",
        "page_size": 10,
        "link_selector": "a.casenumber",
        "use_offset": True,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}


def normalize_case_number(case_number: str) -> str:
    """Normalize case number for matching."""
    normalized = case_number.strip().lower()
    normalized = re.sub(r'[\s/]', '_', normalized)
    normalized = re.sub(r'_+', '_', normalized)
    return normalized


async def fetch_page(client: httpx.AsyncClient, court_name: str, court: dict, offset: int) -> dict[str, str]:
    """Fetch a page of verdict URLs."""
    base_url = court["base_url"]
    url = f"{base_url}{court['list_url']}"

    # All courts use offset/count pagination
    params = {
        "pageitemid": court["pageitemid"],
        "offset": offset,
        "count": court["page_size"],
    }

    response = await client.get(url, params=params)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    urls = {}

    for link in soup.select(court["link_selector"]):
        href = link.get("href", "")
        if not href:
            continue

        case_number_elem = link.select_one("h2")
        case_number = case_number_elem.get_text(strip=True) if case_number_elem else ""

        if case_number and href:
            full_url = urljoin(base_url, href)
            key = f"{court_name}:{normalize_case_number(case_number)}"
            urls[key] = full_url

    return urls


async def fetch_court_urls(client: httpx.AsyncClient, court_name: str) -> dict[str, str]:
    """Fetch all URLs from a court using concurrent batch pagination."""
    court = COURTS[court_name]
    page_size = court["page_size"]
    all_urls = {}

    # Known approximate totals to avoid over-fetching
    max_offsets = {
        "haestirettur": 12200,
        "landsrettur": 6000,
        "heradsdomstolar": 15000,
    }
    max_offset = max_offsets.get(court_name, 15000)

    print(f"\n{'='*60}")
    print(f"  {court_name.upper()}")
    print(f"{'='*60}")

    # Generate all offsets upfront
    offsets = list(range(0, max_offset, page_size))
    batch_size = 20  # Concurrent requests per batch

    for batch_start in range(0, len(offsets), batch_size):
        batch = offsets[batch_start:batch_start + batch_size]
        tasks = [fetch_page(client, court_name, court, o) for o in batch]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for offset, result in zip(batch, results):
            if isinstance(result, Exception):
                continue
            all_urls.update(result)

        current = batch_start + len(batch)
        if current % (batch_size * 5) == 0 or current >= len(offsets):
            print(f"  {current}/{len(offsets)} pages, {len(all_urls)} URLs...", flush=True)

        await asyncio.sleep(0.1)  # Brief pause between batches

    print(f"  Total: {len(all_urls)} URLs")
    return all_urls


def update_database_urls(urls: dict[str, str]):
    """Update database with collected URLs."""
    conn = sqlite3.connect(DB_PATH)

    # Ensure column exists
    cursor = conn.execute("PRAGMA table_info(verdicts)")
    columns = [row[1] for row in cursor.fetchall()]
    if "verdict_url" not in columns:
        conn.execute("ALTER TABLE verdicts ADD COLUMN verdict_url TEXT")
        conn.commit()

    rows = conn.execute("SELECT id, court, case_number FROM verdicts").fetchall()

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
    print(f"\nUpdated {updated}/{len(rows)} records with URLs")


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

    courts = sys.argv[1:] if len(sys.argv) > 1 else list(COURTS.keys())
    print(f"Fetching URLs from: {', '.join(courts)}")

    async with httpx.AsyncClient(headers=HEADERS, timeout=60.0, follow_redirects=True, verify=False) as client:
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
