"""
Scrape active lawyer listings from island.is and lmfi.is.

Sources:
  - island.is GraphQL API: Active lawyers with license types
  - lmfi.is: Icelandic Bar Association lawyer directory (by letter)

Outputs:
  - data/island_is_lawyers.json: { "Name": "hrl|lrl|hdl", ... }
  - data/lmfi_lawyers.json: { "Name": "hrl|lrl|hdl", ... }

Also compares against the local verdicts.db to find misclassified lawyers
(e.g., marked "retired" in DB but active on island.is).
"""

import html
import json
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "verdicts.db"

ISLAND_IS_URL = (
    "https://island.is/api/graphql"
    "?operationName=GetLawyers"
    "&variables=%7B%7D"
    "&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%22"
    "07e79d87cba414c9c07c1bdc19297db8315a25497aaa7bd9b012f01c8fc622db%22%7D%7D"
)

# License type hierarchy: hrl > lrl > hdl
LICENSE_RANK = {"hdl": 0, "lrl": 1, "hrl": 2}

# island.is license type strings -> short codes
LICENSE_MAP = {
    "Málflutningsréttindi fyrir héraðsdómstólunum": "hdl",
    "Málflutningsréttindi fyrir Landsrétti": "lrl",
    "Málflutningsréttindi fyrir Hæstarétti": "hrl",
}

# lmfi.is title strings -> short codes (HTML-decoded text)
# Order matters: check most specific first
LMFI_LICENSE_MAP = {
    "héraðsdómstólum, Landsrétti og Hæstarétti": "hrl",
    "héraðsdómstólum og Landsrétti": "lrl",
    "héraðsdómstólum": "hdl",
}

# Icelandic alphabet letters used for lmfi.is pagination
ICELANDIC_LETTERS = [
    "A", "Á", "B", "C", "D", "E", "É", "F", "G", "H", "I", "Í",
    "J", "K", "L", "M", "N", "O", "Ó", "P", "R", "S", "T", "U",
    "Ú", "V", "X", "Y", "Ý", "Þ", "Æ", "Ö",
]


def fetch_island_is_lawyers() -> dict[str, str]:
    """Fetch lawyers from island.is GraphQL API and deduplicate by name."""
    print("Fetching lawyers from island.is...")
    resp = httpx.get(ISLAND_IS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    raw_lawyers = data["data"]["getLawyers"]
    print(f"  Raw entries from API: {len(raw_lawyers)}")

    # Deduplicate by name, keeping highest license type
    lawyers: dict[str, str] = {}
    unknown_types: set[str] = set()

    for entry in raw_lawyers:
        name = entry["name"].strip()
        licence_type_str = entry.get("licenceType", "")
        code = LICENSE_MAP.get(licence_type_str)

        if code is None:
            unknown_types.add(licence_type_str)
            continue

        existing = lawyers.get(name)
        if existing is None or LICENSE_RANK[code] > LICENSE_RANK[existing]:
            lawyers[name] = code

    if unknown_types:
        print(f"  WARNING: Unknown license types: {unknown_types}")

    print(f"  Unique lawyers after dedup: {len(lawyers)}")

    # Stats by license type
    counts = {}
    for code in lawyers.values():
        counts[code] = counts.get(code, 0) + 1
    for code in sorted(counts, key=lambda c: LICENSE_RANK[c], reverse=True):
        print(f"    {code}: {counts[code]}")

    return lawyers


def _scrape_lmfi_list(client: httpx.Client, is_corporate: int | None, label: str) -> dict[str, dict]:
    """Scrape a single lmfi.is lawyer list.

    Args:
        is_corporate: 0 for law-firm lawyers, 1 for in-house, None for unfiltered "all".

    Returns {name: {"license_type": str, "url": str}}.
    """
    if is_corporate is not None:
        print(f"\n  Fetching {label} (IsCorporate={is_corporate})...")
    else:
        print(f"\n  Fetching {label} (unfiltered)...")
    lawyers: dict[str, dict] = {}

    # Parse entries: <a href="/logmannalisti/ID/slug#lawyer">Name<div class="title">License</div></a>
    pattern = re.compile(
        r'<a\s+href="(/logmannalisti/\d+/[^"]+)#lawyer">\s*'
        r'([^<]+?)\s*'
        r'<div\s+class="title">\s*([^<]+?)\s*</div>',
        re.DOTALL,
    )

    for letter in ICELANDIC_LETTERS:
        encoded_letter = urllib.parse.quote(letter)
        if is_corporate is not None:
            url = f"https://www.lmfi.is/logmannalisti?letter={encoded_letter}&IsCorporate={is_corporate}"
        else:
            url = f"https://www.lmfi.is/logmannalisti?letter={encoded_letter}"
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"    WARNING: Failed to fetch letter {letter}: {e}")
            continue

        matches = pattern.findall(resp.text)

        for raw_path, raw_name, raw_title in matches:
            name = html.unescape(raw_name).strip()
            title = html.unescape(raw_title).strip()
            profile_url = f"https://www.lmfi.is{raw_path}"

            code = None
            for key, val in LMFI_LICENSE_MAP.items():
                if key in title:
                    code = val
                    break
            if code is None:
                code = "hdl"

            existing = lawyers.get(name)
            if existing is None or LICENSE_RANK[code] > LICENSE_RANK.get(existing["license_type"], -1):
                lawyers[name] = {"license_type": code, "url": profile_url}

        count_for_letter = len(matches)
        if count_for_letter > 0:
            print(f"    {letter}: {count_for_letter} lawyers")

        time.sleep(0.3)

    print(f"  Total {label}: {len(lawyers)}")
    return lawyers


def fetch_lmfi_lawyers() -> dict[str, dict]:
    """Scrape lawyers from lmfi.is bar association directory (all three lists).

    Returns {name: {"license_type": str, "url": str, "is_corporate": bool}}.
    """
    print("\nFetching lawyers from lmfi.is...")
    client = httpx.Client(timeout=30, follow_redirects=True)

    regular = _scrape_lmfi_list(client, is_corporate=0, label="regular lawyers")
    corporate = _scrape_lmfi_list(client, is_corporate=1, label="innanhúslögmenn (corporate)")
    all_lawyers = _scrape_lmfi_list(client, is_corporate=None, label="allir lögmenn (all)")

    client.close()

    # Merge: regular lawyers first, then corporate, then fill gaps from unfiltered list
    lawyers: dict[str, dict] = {}
    for name, info in regular.items():
        lawyers[name] = {**info, "is_corporate": False}

    for name, info in corporate.items():
        if name in lawyers:
            # On both lists — mark as corporate, keep higher license
            lawyers[name]["is_corporate"] = True
            if LICENSE_RANK[info["license_type"]] > LICENSE_RANK[lawyers[name]["license_type"]]:
                lawyers[name]["license_type"] = info["license_type"]
                lawyers[name]["url"] = info["url"]
        else:
            # Only on corporate list
            lawyers[name] = {**info, "is_corporate": True}

    # Add lawyers only found on the unfiltered "all" list.
    # If they're not on the law-firm list (IsCorporate=0), they're innanhúslögmenn.
    all_only_count = 0
    for name, info in all_lawyers.items():
        if name not in lawyers:
            is_at_firm = name in regular
            lawyers[name] = {**info, "is_corporate": not is_at_firm}
            all_only_count += 1

    print(f"\n  Combined unique lawyers: {len(lawyers)}")
    corporate_count = sum(1 for v in lawyers.values() if v["is_corporate"])
    print(f"  Corporate/in-house: {corporate_count}")
    print(f"  Only on unfiltered list: {all_only_count}")

    counts: dict[str, int] = {}
    for info in lawyers.values():
        counts[info["license_type"]] = counts.get(info["license_type"], 0) + 1
    for code in sorted(counts, key=lambda c: LICENSE_RANK[c], reverse=True):
        print(f"    {code}: {counts[code]}")

    return lawyers


def compare_with_database(
    island_lawyers: dict[str, str],
    lmfi_lawyers: dict[str, dict],
) -> None:
    """Compare external lawyer lists with the local database."""
    print("\n" + "=" * 60)
    print("COMPARISON WITH DATABASE")
    print("=" * 60)

    if not DB_PATH.exists():
        print("  Database not found, skipping comparison.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all lawyers from DB
    cursor.execute("SELECT name, license_status, license_type FROM lawyers")
    db_lawyers = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    conn.close()

    db_active = {n for n, (s, _) in db_lawyers.items() if s == "active"}
    db_retired = {n for n, (s, _) in db_lawyers.items() if s == "retired"}

    print(f"\nDatabase totals:")
    print(f"  Active: {len(db_active)}")
    print(f"  Retired: {len(db_retired)}")
    print(f"  Total: {len(db_lawyers)}")

    island_names = set(island_lawyers.keys())
    lmfi_names = set(lmfi_lawyers.keys())

    # -- island.is vs DB --
    print(f"\nisland.is ({len(island_names)} lawyers):")
    retired_but_active_island = island_names & db_retired
    print(f"  On island.is but marked RETIRED in DB: {len(retired_but_active_island)}")
    if retired_but_active_island:
        for name in sorted(retired_but_active_island):
            db_type = db_lawyers[name][1] or "none"
            island_type = island_lawyers[name]
            print(f"    - {name} (DB: retired/{db_type}, island.is: {island_type})")

    active_and_on_island = island_names & db_active
    print(f"  On island.is AND active in DB: {len(active_and_on_island)}")

    on_island_not_in_db = island_names - set(db_lawyers.keys())
    print(f"  On island.is but NOT in DB at all: {len(on_island_not_in_db)}")

    in_db_active_not_on_island = db_active - island_names
    print(f"  Active in DB but NOT on island.is: {len(in_db_active_not_on_island)}")
    if in_db_active_not_on_island:
        for name in sorted(in_db_active_not_on_island):
            db_type = db_lawyers[name][1] or "none"
            print(f"    - {name} (DB: active/{db_type})")

    # -- lmfi.is vs DB --
    if lmfi_lawyers:
        print(f"\nlmfi.is ({len(lmfi_names)} lawyers):")
        retired_but_active_lmfi = lmfi_names & db_retired
        print(f"  On lmfi.is but marked RETIRED in DB: {len(retired_but_active_lmfi)}")

        active_and_on_lmfi = lmfi_names & db_active
        print(f"  On lmfi.is AND active in DB: {len(active_and_on_lmfi)}")

        on_lmfi_not_in_db = lmfi_names - set(db_lawyers.keys())
        print(f"  On lmfi.is but NOT in DB at all: {len(on_lmfi_not_in_db)}")

    # -- island.is vs lmfi.is --
    if lmfi_lawyers:
        print(f"\nisland.is vs lmfi.is:")
        both = island_names & lmfi_names
        print(f"  On both: {len(both)}")
        island_only = island_names - lmfi_names
        print(f"  island.is only: {len(island_only)}")
        lmfi_only = lmfi_names - island_names
        print(f"  lmfi.is only: {len(lmfi_only)}")

        # License type mismatches
        mismatches = []
        for name in both:
            lmfi_type = lmfi_lawyers[name]["license_type"]
            if island_lawyers[name] != lmfi_type:
                mismatches.append((name, island_lawyers[name], lmfi_type))
        if mismatches:
            print(f"  License type mismatches: {len(mismatches)}")
            for name, i_type, l_type in sorted(mismatches)[:20]:
                print(f"    - {name}: island.is={i_type}, lmfi.is={l_type}")
            if len(mismatches) > 20:
                print(f"    ... and {len(mismatches) - 20} more")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch from island.is
    island_lawyers = fetch_island_is_lawyers()
    island_path = DATA_DIR / "island_is_lawyers.json"
    island_path.write_text(
        json.dumps(island_lawyers, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"  Saved to {island_path}")

    # Fetch from lmfi.is
    lmfi_lawyers = fetch_lmfi_lawyers()
    if lmfi_lawyers:
        lmfi_path = DATA_DIR / "lmfi_lawyers.json"
        lmfi_path.write_text(
            json.dumps(lmfi_lawyers, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"  Saved to {lmfi_path}")

    # Compare with DB
    compare_with_database(island_lawyers, lmfi_lawyers)


if __name__ == "__main__":
    main()
