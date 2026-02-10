#!/usr/bin/env python3
"""Download PDFs from Icelandic courts."""

import asyncio
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

COURTS = {
    "heradsdomstolar": {
        "url": "https://www.heradsdomstolar.is",
        "page_id": "deb3ce16-7d66-11e5-80c6-005056bc6a40",
        "link_class": "a.sentence",
        "has_pdf": True,
    },
    "landsrettur": {
        "url": "https://www.landsrettur.is",
        "page_id": "deb3ce16-7d66-11e5-80c6-005056bc6a40",
        "link_class": "a.casenumber",
        "has_pdf": True,
    },
    "haestirettur": {
        "url": "https://www.haestirettur.is",
        "page_id": "deb3ce16-7d66-11e5-80c6-005056bc6a40",
        "link_class": "a.casenumber",
        "has_pdf": False,  # No PDFs available
    },
}

DATA_DIR = Path(__file__).parent.parent / "data" / "pdfs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

# Start year for downloads (older cases often lack PDFs)
START_YEAR = 2020


def month_ranges(start_year: int = START_YEAR) -> list[tuple[date, date]]:
    """Generate (start, end) date tuples for each month from start_year to now."""
    ranges = []
    current = date(start_year, 1, 1)
    today = date.today()

    while current <= today:
        # First day of month
        month_start = current
        # Last day of month
        if current.month == 12:
            month_end = date(current.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(current.year, current.month + 1, 1) - timedelta(days=1)

        ranges.append((month_start, min(month_end, today)))

        # Move to next month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return ranges


async def fetch_cases_for_period(
    client: httpx.AsyncClient,
    court: dict,
    base_url: str,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Fetch cases for a specific date range."""
    params = {
        "pageid": court["page_id"],
        "searchaction": "search",
        "Verdict": "*",
        "FromDate": from_date.strftime("%d.%m.%Y"),
        "ToDate": to_date.strftime("%d.%m.%Y"),
        "PageSize": "1000",
    }

    response = await client.get(f"{base_url}/", params=params)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    cases = []

    for result_div in soup.select("div.result"):
        link = result_div.select_one(court["link_class"])
        if not link:
            continue

        href = link.get("href", "")
        case_number_elem = link.select_one("h2")
        case_number = case_number_elem.get_text(strip=True) if case_number_elem else "unknown"

        # Clean case number for filename
        safe_name = re.sub(r'[^\w\-]', '_', case_number)

        cases.append({
            "url": urljoin(base_url, href),
            "case_number": case_number,
            "filename": f"{safe_name}.pdf",
        })

    return cases


async def find_pdf_link(client: httpx.AsyncClient, case_url: str) -> str | None:
    """Find PDF download link on case detail page."""
    response = await client.get(case_url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # Look for PDF download link
    pdf_link = soup.select_one('a.pdflink[href*="Download"]')
    if not pdf_link:
        pdf_link = soup.select_one('a[href*=".pdf"]')
    if not pdf_link:
        pdf_link = soup.select_one('a[href*="Download"][href*="docId"]')

    if pdf_link:
        href = pdf_link.get("href", "")
        return urljoin(case_url, href)

    return None


async def download_pdf(client: httpx.AsyncClient, pdf_url: str, output_path: Path) -> bool:
    """Download PDF to file."""
    try:
        response = await client.get(pdf_url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not pdf_url.endswith(".pdf"):
            return False

        output_path.write_bytes(response.content)
        return True
    except Exception as e:
        print(f"    Download error: {e}")
        return False


async def download_court(client: httpx.AsyncClient, court_name: str):
    """Download all PDFs from a single court."""
    court = COURTS[court_name]

    if not court["has_pdf"]:
        print(f"\n{court_name}: Skipping (no PDFs available)")
        return 0, 0, 0

    base_url = court["url"]
    output_dir = DATA_DIR / court_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  {court_name.upper()}")
    print(f"{'='*60}")

    ranges = month_ranges()
    total_downloaded = 0
    total_skipped = 0
    total_failed = 0

    for i, (from_date, to_date) in enumerate(ranges):
        month_label = from_date.strftime("%Y-%m")
        print(f"\n[{i+1}/{len(ranges)}] {month_label}...", end=" ", flush=True)

        try:
            cases = await fetch_cases_for_period(client, court, base_url, from_date, to_date)
        except Exception as e:
            print(f"Error fetching: {e}")
            continue

        if not cases:
            print("0 cases")
            continue

        print(f"{len(cases)} cases")

        downloaded = 0
        skipped = 0
        failed = 0

        for case in cases:
            output_path = output_dir / case["filename"]

            if output_path.exists():
                skipped += 1
                continue

            try:
                pdf_url = await find_pdf_link(client, case["url"])
                if not pdf_url:
                    failed += 1
                    continue

                if await download_pdf(client, pdf_url, output_path):
                    downloaded += 1
                    print(f"  + {case['case_number']}")
                else:
                    failed += 1
            except Exception as e:
                print(f"  ! {case['case_number']}: {e}")
                failed += 1

            # Be polite - small delay between requests
            await asyncio.sleep(0.2)

        total_downloaded += downloaded
        total_skipped += skipped
        total_failed += failed

        if downloaded or failed:
            print(f"  -> {downloaded} new, {skipped} exist, {failed} failed")

    print(f"\n{court_name} TOTAL: {total_downloaded} downloaded, {total_skipped} skipped, {total_failed} failed")
    return total_downloaded, total_skipped, total_failed


async def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Get court from command line or do all with PDFs
    if len(sys.argv) > 1:
        courts_to_download = sys.argv[1:]
    else:
        courts_to_download = [name for name, cfg in COURTS.items() if cfg["has_pdf"]]

    print(f"Downloading from: {', '.join(courts_to_download)}")
    print(f"Date range: {START_YEAR} to {date.today().year}")

    # verify=False for Proxyman debugging
    async with httpx.AsyncClient(headers=HEADERS, timeout=60.0, follow_redirects=True, verify=False) as client:
        grand_downloaded = 0
        grand_skipped = 0
        grand_failed = 0

        for court_name in courts_to_download:
            if court_name not in COURTS:
                print(f"Unknown court: {court_name}")
                continue

            d, s, f = await download_court(client, court_name)
            grand_downloaded += d
            grand_skipped += s
            grand_failed += f

        print(f"\n{'='*60}")
        print(f"GRAND TOTAL: {grand_downloaded} downloaded, {grand_skipped} skipped, {grand_failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
