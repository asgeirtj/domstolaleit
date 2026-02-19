#!/usr/bin/env python3
"""Download all verdicts from Icelandic courts using pagination endpoints."""

import asyncio
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent / "data"
PDF_DIR = DATA_DIR / "pdfs"
TXT_DIR = DATA_DIR / "txt"


def make_chronological_filename(case_number: str) -> str:
    """Convert scraped case number to chronological filename (no extension).

    Examples:
        'E-102/2020' -> '2020-E-0102'
        '1/2018'     -> '2018-0001'
        '1. /1999'   -> '1999-0001'
    """
    cn = case_number.strip()
    # PREFIX-NUM/YEAR (heradsdomstolar)
    m = re.search(r"([A-Z])-(\d+)/(\d{4})", cn)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2).zfill(4)}"
    # NUM/YEAR (landsrettur, haestirettur)
    m = re.search(r"(\d+)/(\d{4})", cn)
    if m:
        return f"{m.group(2)}-{m.group(1).zfill(4)}"
    # Fallback: any number followed by 4-digit year
    m = re.search(r"(\d+)\D+(\d{4})", cn)
    if m:
        return f"{m.group(2)}-{m.group(1).zfill(4)}"
    # Last resort
    return re.sub(r"[^\w\-]", "_", cn)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

COURTS = {
    "landsrettur": {
        "base_url": "https://landsrettur.is",
        "list_url": "/domar-og-urskurdir/$Verdicts/Index/",
        "pageitemid": "5cf6e850-20b6-11e9-85de-94b86df896cb",
        "page_size": 12,
        "link_selector": "a.casenumber",
        "has_pdf": True,
        "use_offset": True,
    },
    "heradsdomstolar": {
        "base_url": "https://www.heradsdomstolar.is",
        "list_url": "/default.aspx",
        "pageitemid": "e7fc58af-8d46-11e5-80c6-005056bc6a40",
        "page_size": 20,
        "link_selector": "a.sentence",
        "has_pdf": True,
        "html_fallback": "#verdict-text",  # Older cases have no PDF, extract HTML instead
        "use_offset": True,
    },
    "haestirettur": {
        "base_url": "https://www.haestirettur.is",
        "list_url": "/default.aspx",
        "pageitemid": "4468cca6-a82f-11e5-9402-005056bc2afe",
        "page_size": 10,
        "link_selector": "a.casenumber",
        "has_pdf": False,  # Save as HTML/text instead
        "content_selector": "div.verdict__body",
        "use_offset": True,  # Uses offset pagination
    },
}


async def fetch_page(client: httpx.AsyncClient, court: dict, offset: int) -> list[dict]:
    """Fetch a page of verdicts."""
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
    cases = []

    for link in soup.select(court["link_selector"]):
        href = link.get("href", "")
        if not href:
            continue

        case_number_elem = link.select_one("h2")
        case_number = case_number_elem.get_text(strip=True) if case_number_elem else "unknown"

        cases.append({
            "url": urljoin(base_url, href),
            "case_number": case_number,
            "filename": f"{make_chronological_filename(case_number)}.pdf",
        })

    return cases


async def find_pdf_link(client: httpx.AsyncClient, case_url: str) -> str | None:
    """Find PDF download link on case detail page."""
    try:
        response = await client.get(case_url)
        response.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(response.text, "lxml")

    # Look for PDF download link (various patterns)
    for selector in [
        'a.pdflink[href*="Download"]',
        'a[href*=".pdf"]',
        'a[href*="Download"][href*="docId"]',
        'a.pdflink',
    ]:
        pdf_link = soup.select_one(selector)
        if pdf_link:
            href = pdf_link.get("href", "")
            if href:
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
    except Exception:
        return False


async def extract_html_content(client: httpx.AsyncClient, case_url: str, selector: str, output_path: Path) -> bool:
    """Extract verdict text from HTML and save as text file."""
    try:
        response = await client.get(case_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        content_elem = soup.select_one(selector)

        if not content_elem:
            return False

        # Get text content, preserving some structure
        text = content_elem.get_text(separator="\n", strip=True)
        if not text or len(text) < 100:
            return False

        output_path.write_text(text, encoding="utf-8")
        return True
    except Exception:
        return False


async def download_court(client: httpx.AsyncClient, court_name: str):
    """Download all verdicts from a court."""
    court = COURTS[court_name]
    pdf_dir = PDF_DIR / court_name
    txt_dir = TXT_DIR / court_name
    txt_dir.mkdir(parents=True, exist_ok=True)
    if court.get("has_pdf", True):
        pdf_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  {court_name.upper()}")
    print(f"{'='*60}")

    has_pdf = court.get("has_pdf", True)
    content_selector = court.get("content_selector")
    html_fallback = court.get("html_fallback")

    if not has_pdf:
        print("(Saving as text - no PDFs available)")

    offset = 0
    page_size = court["page_size"]
    total_downloaded = 0
    total_skipped = 0
    total_failed = 0
    consecutive_empty = 0

    while True:
        print(f"\nOffset {offset}...", end=" ", flush=True)

        try:
            cases = await fetch_page(client, court, offset)
        except Exception as e:
            print(f"Error: {e}")
            break

        if not cases:
            consecutive_empty += 1
            print("0 cases")
            if consecutive_empty >= 10:
                print("No more cases found")
                break
            offset += page_size
            continue

        consecutive_empty = 0
        print(f"{len(cases)} cases")

        downloaded = 0
        skipped = 0
        failed = 0

        for case in cases:
            pdf_path = pdf_dir / case["filename"]
            txt_path = txt_dir / case["filename"].replace(".pdf", ".txt")

            if has_pdf:
                # Skip if we have either the PDF or a .txt extraction
                if pdf_path.exists() or txt_path.exists():
                    skipped += 1
                    continue
            else:
                if txt_path.exists():
                    skipped += 1
                    continue

            if has_pdf:
                pdf_url = await find_pdf_link(client, case["url"])
                if pdf_url and await download_pdf(client, pdf_url, pdf_path):
                    downloaded += 1
                    print(f"  + {case['case_number']}")
                elif html_fallback:
                    # No PDF link — try extracting HTML content instead
                    if await extract_html_content(client, case["url"], html_fallback, txt_path):
                        downloaded += 1
                        print(f"  + {case['case_number']} (html)")
                    else:
                        failed += 1
                else:
                    failed += 1
            else:
                # Extract HTML content for courts without PDFs
                if await extract_html_content(client, case["url"], content_selector, txt_path):
                    downloaded += 1
                    print(f"  + {case['case_number']}")
                else:
                    failed += 1

            await asyncio.sleep(0.15)

        total_downloaded += downloaded
        total_skipped += skipped
        total_failed += failed

        if downloaded or failed:
            print(f"  -> {downloaded} new, {skipped} exist, {failed} failed")

        offset += page_size

    print(f"\n{court_name}: {total_downloaded} downloaded, {total_skipped} skipped, {total_failed} failed")
    return total_downloaded, total_skipped, total_failed


async def main():
    import sys

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    TXT_DIR.mkdir(parents=True, exist_ok=True)

    courts = sys.argv[1:] if len(sys.argv) > 1 else ["landsrettur", "heradsdomstolar", "haestirettur"]

    print(f"Downloading from: {', '.join(courts)}")

    async with httpx.AsyncClient(headers=HEADERS, timeout=60.0, follow_redirects=True, verify=False) as client:
        grand_d, grand_s, grand_f = 0, 0, 0

        for court_name in courts:
            if court_name not in COURTS:
                print(f"Unknown court: {court_name}")
                continue

            d, s, f = await download_court(client, court_name)
            grand_d += d
            grand_s += s
            grand_f += f

        print(f"\n{'='*60}")
        print(f"TOTAL: {grand_d} downloaded, {grand_s} skipped, {grand_f} failed")


if __name__ == "__main__":
    asyncio.run(main())
