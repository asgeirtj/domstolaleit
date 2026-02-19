#!/usr/bin/env python3
"""Build appeal chains between court levels and mark superseded verdicts.

When a verdict is appealed to a higher court, only the highest court's
outcome should count for lawyer statistics. This script links lower-court
verdicts to their higher-court appeals via the `superseded_by` column.

Court hierarchy: Heradsdomstolar -> Landsrettur (est. 2018) -> Haestirettur

Matching strategies (all run in one pass):
  1. Case number: Upper court text contains "i malinu nr. S-800/2016" -> look up HD
  2. HR web scrape: HR verdict page has hidden <span id="verdict-url"> with LR URL
  3. Fingerprint: For anonymized cases ([...]/2016), match on date + court + lawyers + judge
     extracted from the embedded HD verdict in the LR text

Run after build_index.py and fetch_verdict_urls.py.
"""

import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "verdicts.db"
APPEAL_LINKS_CACHE = DATA_DIR / "appeal_links.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Case number reference in upper court text: "i malinu nr. E-3906/2018"
CASE_REF_PATTERN = re.compile(
    r"(?:í\s+)?máli?\w*\s+nr\.\s+([A-Z]-?\d+/\d{4})",
    re.IGNORECASE,
)

# Anonymized case number: S-[...]/2016 or S-[…]/2016
ANON_REF_PATTERN = re.compile(
    r"(?:í\s+)?máli?\w*\s+nr\.\s+([A-Z])-?\[(?:\.\.\.|…)\]/(\d{4})",
    re.IGNORECASE,
)

# Court + date from the LR reference paragraph:
# "Heradsdóms Reykjaness 7. november 2016 i malinu nr."
# Also matches the embedded HD header: "Domur Heradsdoms Reykjaness manudaginn 7. november 2016"
# Location can be multi-word: "Norðurlands eystra"
LR_REF_PATTERN = re.compile(
    r"Héraðsdóms\s+(.+?)\s+"
    r"(?:(?:mánudaginn|þriðjudaginn|miðvikudaginn|fimmtudaginn|föstudaginn|laugardaginn|sunnudaginn)\s*,?\s+)?"
    r"(\d{1,2})\.\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)

# Full HD verdict header (for standalone HD verdicts and embedded sections)
# Matches both "Dómur Héraðsdóms..." and "D Ó M U R\nHéraðsdóms..."
# Location can be multi-word: "Norðurlands eystra", "Norðurlands vestra"
HD_HEADER_PATTERN = re.compile(
    r"(?:D\s*[óÓ]\s*M\s*U\s*R|Dómur|Úrskurður)\s+Héraðsdóms\s+(.+?)\s+"
    r"(?:(?:mánudaginn|þriðjudaginn|miðvikudaginn|fimmtudaginn|föstudaginn|laugardaginn|sunnudaginn)\s*,?\s+)?"
    r"(\d{1,2})\.\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)

# Judge from "Dom thennan kvedur upp X heradsdomari"
JUDGE_PATTERN = re.compile(
    r"[Dd]óm\s+þennan\s+kveður\s+upp\s+(.+?)\s+héraðsdómari",
)

# Lawyer from parenthetical references (for HD verdict headers)
LAWYER_PARENS_PATTERN = re.compile(
    r"\(([^()]+?)\s+(?:lögmaður|hrl\.|hdl\.|héraðsdómslögmaður|héraðsdómslögmanns"
    r"|saksóknari|saksóknarfulltrúi|settur\s+saksóknari)",
    re.IGNORECASE,
)

# Lawyer from Domsord fee section (genitive case names in embedded HD text):
# "malsvarnarlaun ... verjanda sins, Ruts Arnar Birgissonar heradsdómslögmanns"
LAWYER_FEE_PATTERN = re.compile(
    r"(?:málsvarnarlaun|málflutningslaun).{0,80}?,"
    r"\s+([A-ZÁÉÍÓÚÝÞÆÐÖ][a-záéíóúýþæðö]+(?:\s+[A-ZÁÉÍÓÚÝÞÆÐÖ][a-záéíóúýþæðö]+){1,3})\s+"
    r"(?:héraðsdómslögmanns|lögmanns|hrl\.|hdl\.)",
    re.DOTALL,
)

# Icelandic month names to numbers
MONTHS = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4,
    "maí": 5, "júní": 6, "júlí": 7, "ágúst": 8,
    "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}


def _extract_verdictid(url: str) -> str | None:
    """Extract the verdictid UUID from a Landsrettur URL."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        for key, values in params.items():
            if key.lower() == "verdictid" and values:
                return values[0].lower()
    except Exception:
        pass
    return None


def _normalize_name(name: str) -> str:
    """Normalize a name for comparison: lowercase, collapse whitespace."""
    return " ".join(name.lower().split())


def _last_name(full_name: str) -> str:
    """Extract last name (patronymic) from a full name.

    Last names (patronymics like Sigurðsson/Sigurðssonar) are more stable
    across Icelandic declension than first names.
    """
    parts = full_name.strip().split()
    return parts[-1].lower() if parts else ""


def _extract_lawyer_lastnames(text: str) -> set[str]:
    """Extract lawyer last names from both parenthetical and fee section patterns."""
    lastnames = set()

    # Parenthetical: (Sigurður Freyr Sigurðsson hdl.)
    for m in LAWYER_PARENS_PATTERN.finditer(text):
        name = m.group(1).strip().rstrip(",")
        if len(name) >= 3:
            lastnames.add(_last_name(name))

    # Fee section: "verjanda sins, Sigurðar Freys Sigurðssonar héraðsdómslögmanns"
    for m in LAWYER_FEE_PATTERN.finditer(text):
        name = m.group(1).strip()
        if len(name) >= 3:
            lastnames.add(_last_name(name))

    return lastnames


def _extract_lr_fingerprint(text: str) -> dict | None:
    """Extract matching fingerprint from an LR verdict's reference to the HD case.

    Parses the LR opening paragraph like:
      "Áfrýjað er dómi Héraðsdóms Reykjavíkur 2. desember 2015 í málinu nr. S-[…]/2015"

    Extracts judge and lawyer last names from the embedded HD section.
    """
    header = LR_REF_PATTERN.search(text[:5000])
    if not header:
        return None

    location = header.group(1).rstrip(",.")
    day = int(header.group(2))
    month_name = header.group(3).lower()
    year = int(header.group(4))

    month = MONTHS.get(month_name)
    if not month:
        return None

    # Find the embedded HD section (starts with "Dómur Héraðsdóms" or "D Ó M U R")
    hd_section = ""
    hd_match = HD_HEADER_PATTERN.search(text)
    if hd_match:
        hd_section = text[hd_match.start():]

    # Extract judge from embedded HD section
    judge = None
    search_text = hd_section or text
    judge_match = JUDGE_PATTERN.search(search_text)
    if judge_match:
        judge = _normalize_name(judge_match.group(1))

    # Extract lawyer last names from embedded HD section
    lawyer_lastnames = _extract_lawyer_lastnames(hd_section) if hd_section else set()

    return {
        "location": location.lower(),
        "day": day,
        "month": month,
        "year": year,
        "judge": judge,
        "lawyer_lastnames": lawyer_lastnames,
    }


def _extract_hd_fingerprint(text: str) -> dict | None:
    """Extract fingerprint from a standalone HD verdict for matching."""
    header = HD_HEADER_PATTERN.search(text[:500])
    if not header:
        return None

    location = header.group(1).rstrip(",.")
    day = int(header.group(2))
    month_name = header.group(3).lower()
    year = int(header.group(4))

    month = MONTHS.get(month_name)
    if not month:
        return None

    judge = None
    judge_match = JUDGE_PATTERN.search(text)
    if judge_match:
        judge = _normalize_name(judge_match.group(1))

    lawyer_lastnames = _extract_lawyer_lastnames(text)

    return {
        "location": location.lower(),
        "day": day,
        "month": month,
        "year": year,
        "judge": judge,
        "lawyer_lastnames": lawyer_lastnames,
    }


def match_by_case_number(conn: sqlite3.Connection) -> dict[int, int]:
    """Match upper court verdicts to lower court by explicit case number reference.

    Returns dict mapping lower_verdict_id -> upper_verdict_id.
    """
    print("\n--- Case number matching ---")

    # Build lookups
    hd_lookup: dict[str, int] = {}
    lr_lookup: dict[str, int] = {}

    for vid, court, cn in conn.execute(
        "SELECT id, court, case_number FROM verdicts WHERE court IN ('heradsdomstolar', 'landsrettur')"
    ).fetchall():
        key = cn.strip().upper()
        if court == "heradsdomstolar":
            hd_lookup[key] = vid
        else:
            lr_lookup[key] = vid

    chains: dict[int, int] = {}
    lr_to_hd = 0
    hr_to_hd = 0
    hr_to_lr = 0

    upper_courts = conn.execute("""
        SELECT v.id, v.court, SUBSTR(f.content, 1, 5000)
        FROM verdicts v
        JOIN verdicts_fts f ON f.rowid = v.id
        WHERE v.court IN ('landsrettur', 'haestirettur')
    """).fetchall()

    for vid, court, text_start in upper_courts:
        if not text_start:
            continue

        for ref in CASE_REF_PATTERN.findall(text_start):
            ref_upper = ref.strip().upper()
            if court == "landsrettur" and ref_upper in hd_lookup:
                chains[hd_lookup[ref_upper]] = vid
                lr_to_hd += 1
            elif court == "haestirettur":
                if ref_upper in lr_lookup:
                    chains[lr_lookup[ref_upper]] = vid
                    hr_to_lr += 1
                elif ref_upper in hd_lookup:
                    chains[hd_lookup[ref_upper]] = vid
                    hr_to_hd += 1

    print(f"  LR -> HD: {lr_to_hd}")
    print(f"  HR -> LR: {hr_to_lr}")
    print(f"  HR -> HD: {hr_to_hd}")
    print(f"  Total: {len(chains)}")
    return chains


def match_by_fingerprint(conn: sqlite3.Connection, already_matched: set[int]) -> dict[int, int]:
    """Match anonymized LR verdicts to HD by date + court + lawyers + judge.

    For LR verdicts with anonymized HD case numbers ([...]/year), extracts a
    fingerprint from the embedded HD verdict text and matches against HD verdicts.

    Returns dict mapping hd_verdict_id -> lr_verdict_id.
    """
    print("\n--- Fingerprint matching (anonymized cases) ---")

    # Find LR verdicts with anonymized HD references that aren't already matched
    lr_rows = conn.execute("""
        SELECT v.id, v.case_number, f.content
        FROM verdicts v
        JOIN verdicts_fts f ON f.rowid = v.id
        WHERE v.court = 'landsrettur'
    """).fetchall()

    candidates = []
    for vid, cn, text in lr_rows:
        if not text:
            continue
        # Skip if this LR verdict already has an HD linked to it
        if vid in already_matched:
            continue
        # Check for anonymized reference (both [...] and [...] variants)
        anon = ANON_REF_PATTERN.search(text[:5000])
        if not anon:
            continue
        prefix = anon.group(1)
        year = int(anon.group(2))
        fp = _extract_lr_fingerprint(text)
        if fp:
            candidates.append((vid, cn, prefix, year, fp))

    print(f"  LR verdicts with anonymized refs to fingerprint: {len(candidates)}")

    if not candidates:
        return {}

    # Build HD fingerprint index grouped by (year, location)
    # Only compute for years that appear in candidates
    candidate_years = {c[3] for c in candidates}
    hd_rows = conn.execute("""
        SELECT v.id, v.case_number, f.content
        FROM verdicts v
        JOIN verdicts_fts f ON f.rowid = v.id
        WHERE v.court = 'heradsdomstolar'
    """).fetchall()

    # Index: (year, location) -> list of (hd_id, case_number, fingerprint)
    hd_index: dict[tuple[int, str], list[tuple[int, str, dict]]] = {}
    indexed = 0
    for hd_id, hd_cn, hd_text in hd_rows:
        if not hd_text:
            continue
        # Quick year check from case number
        m = re.search(r"/(\d{4})", hd_cn)
        if not m or int(m.group(1)) not in candidate_years:
            continue
        fp = _extract_hd_fingerprint(hd_text)
        if not fp:
            continue
        key = (fp["year"], fp["location"])
        if key not in hd_index:
            hd_index[key] = []
        hd_index[key].append((hd_id, hd_cn, fp))
        indexed += 1

    print(f"  HD verdicts fingerprinted: {indexed}")

    # Match candidates against HD index
    chains: dict[int, int] = {}
    matched = 0
    ambiguous = 0
    unique_date = 0

    for lr_id, lr_cn, prefix, year, lr_fp in candidates:
        key = (lr_fp["year"], lr_fp["location"])
        hd_candidates = hd_index.get(key, [])

        # Filter by exact date match
        date_matches = [
            (hd_id, hd_cn, hd_fp) for hd_id, hd_cn, hd_fp in hd_candidates
            if hd_fp["day"] == lr_fp["day"] and hd_fp["month"] == lr_fp["month"]
        ]

        if not date_matches:
            continue

        # Filter by case prefix (S- or E-)
        prefix_matches = [
            (hd_id, hd_cn, hd_fp) for hd_id, hd_cn, hd_fp in date_matches
            if hd_cn.upper().startswith(f"{prefix}-")
        ]
        if prefix_matches:
            date_matches = prefix_matches

        # If only one candidate after date + prefix filter, accept it directly
        if len(date_matches) == 1:
            chains[date_matches[0][0]] = lr_id
            matched += 1
            unique_date += 1
            continue

        # Multiple candidates: score by judge + lawyer last name overlap
        best_score = -1
        best_match = None
        best_tied = False

        for hd_id, hd_cn, hd_fp in date_matches:
            score = 0
            # Judge match is the strongest signal
            if lr_fp["judge"] and hd_fp["judge"] and lr_fp["judge"] == hd_fp["judge"]:
                score += 5
            # Lawyer last name overlap (patronymics survive declension)
            if lr_fp["lawyer_lastnames"] and hd_fp["lawyer_lastnames"]:
                overlap = lr_fp["lawyer_lastnames"] & hd_fp["lawyer_lastnames"]
                score += len(overlap) * 2

            if score > best_score:
                best_score = score
                best_match = (hd_id, hd_cn)
                best_tied = False
            elif score == best_score and score > 0:
                best_tied = True

        # Require at least judge OR one lawyer last name match, and no tie
        if best_match and best_score >= 2 and not best_tied:
            chains[best_match[0]] = lr_id
            matched += 1
        elif best_tied:
            ambiguous += 1

    print(f"  Matched: {matched} (unique date: {unique_date}, scored: {matched - unique_date})")
    print(f"  Ambiguous (tied score, skipped): {ambiguous}")
    return chains


async def fetch_hr_appeal_link(
    client: httpx.AsyncClient, verdict_url: str
) -> str | None:
    """Fetch a Haestirettur verdict page and extract the Landsrettur link."""
    try:
        resp = await client.get(verdict_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        span = soup.select_one("#verdict-url")
        if span:
            lr_url = span.get_text(strip=True)
            if lr_url and "landsrettur" in lr_url.lower():
                return lr_url

        for link in soup.select("a[data-solution]"):
            href = link.get("data-solution", "") or link.get("href", "")
            if href and "landsrettur" in href.lower():
                return href
    except (httpx.HTTPError, Exception):
        pass
    return None


async def match_hr_to_lr_by_scraping(
    conn: sqlite3.Connection,
    cached_links: dict[str, str],
) -> tuple[dict[int, int], dict[str, str]]:
    """Match HR -> LR by scraping HR verdict pages for LR links.

    Returns (chains dict, updated cache dict).
    """
    print("\n--- HR -> LR web scraping ---")

    hr_rows = conn.execute("""
        SELECT id, case_number, verdict_url
        FROM verdicts
        WHERE court = 'haestirettur' AND verdict_url IS NOT NULL
    """).fetchall()

    post_2018 = []
    for vid, cn, url in hr_rows:
        m = re.search(r"/(\d{4})", cn)
        if m and int(m.group(1)) >= 2018:
            post_2018.append((vid, cn, url))

    print(f"  Post-2018 HR verdicts: {len(post_2018)}")

    # Build LR verdictid lookup
    lr_verdictid_lookup: dict[str, int] = {}
    for lid, lurl in conn.execute(
        "SELECT id, verdict_url FROM verdicts WHERE court = 'landsrettur' AND verdict_url IS NOT NULL"
    ).fetchall():
        vid_uuid = _extract_verdictid(lurl)
        if vid_uuid:
            lr_verdictid_lookup[vid_uuid] = lid

    # Fetch uncached pages
    new_links = dict(cached_links)
    to_fetch = [(v, c, u) for v, c, u in post_2018 if u not in cached_links]
    print(f"  Cached: {len(post_2018) - len(to_fetch)}, To fetch: {len(to_fetch)}")

    if to_fetch:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=30.0, follow_redirects=True, verify=False
        ) as client:
            batch_size = 10
            for i in range(0, len(to_fetch), batch_size):
                batch = to_fetch[i:i + batch_size]
                results = await asyncio.gather(*[
                    fetch_hr_appeal_link(client, url) for _, _, url in batch
                ])
                for (_, _, url), lr_url in zip(batch, results):
                    new_links[url] = lr_url or ""

                fetched = min(i + batch_size, len(to_fetch))
                if fetched % 50 == 0 or fetched >= len(to_fetch):
                    print(f"  Fetched {fetched}/{len(to_fetch)}...", flush=True)
                await asyncio.sleep(0.2)

    # Build chains
    chains: dict[int, int] = {}
    for vid, cn, url in post_2018:
        lr_url = new_links.get(url, "")
        if not lr_url:
            continue
        lr_vid_uuid = _extract_verdictid(lr_url)
        if lr_vid_uuid and lr_vid_uuid in lr_verdictid_lookup:
            chains[lr_verdictid_lookup[lr_vid_uuid]] = vid

    print(f"  Matched: {len(chains)}")
    return chains, new_links


def apply_chains(conn: sqlite3.Connection, chains: dict[int, int]):
    """Apply appeal chains to the database by setting superseded_by."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(verdicts)").fetchall()]
    if "superseded_by" not in cols:
        conn.execute("ALTER TABLE verdicts ADD COLUMN superseded_by INTEGER REFERENCES verdicts(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_verdicts_superseded ON verdicts(superseded_by)")
        conn.commit()

    conn.execute("UPDATE verdicts SET superseded_by = NULL WHERE superseded_by IS NOT NULL")
    for lower_id, upper_id in chains.items():
        conn.execute("UPDATE verdicts SET superseded_by = ? WHERE id = ?", (upper_id, lower_id))
    conn.commit()


def print_summary(conn: sqlite3.Connection):
    """Print summary of appeal chains in the database."""
    total = conn.execute("SELECT COUNT(*) FROM verdicts WHERE superseded_by IS NOT NULL").fetchone()[0]
    by_court = conn.execute("""
        SELECT court, COUNT(*) FROM verdicts
        WHERE superseded_by IS NOT NULL GROUP BY court
    """).fetchall()
    triples = conn.execute("""
        SELECT COUNT(*) FROM verdicts hd
        JOIN verdicts lr ON hd.superseded_by = lr.id
        WHERE lr.superseded_by IS NOT NULL
    """).fetchone()[0]

    print(f"\n{'='*50}")
    print(f"  SUMMARY")
    print(f"{'='*50}")
    print(f"  Total superseded verdicts: {total}")
    for court, cnt in by_court:
        print(f"    {court}: {cnt}")
    print(f"  Triple chains (HD->LR->HR): {triples}")
    print(f"{'='*50}")


async def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # 1. Case number matching (LR->HD, HR->HD, HR->LR by text)
    chains = match_by_case_number(conn)

    # 2. HR -> LR via web scraping
    cached_links: dict[str, str] = {}
    if APPEAL_LINKS_CACHE.exists():
        cached_links = json.loads(APPEAL_LINKS_CACHE.read_text())
        print(f"\nLoaded {len(cached_links)} cached HR appeal links")

    hr_chains, updated_cache = await match_hr_to_lr_by_scraping(conn, cached_links)
    chains.update(hr_chains)

    # 3. Fingerprint matching for anonymized cases
    # Build set of LR verdict IDs that already have an HD linked to them
    already_matched_lr = set(chains.values())  # LR ids that are targets of HD->LR chains
    fp_chains = match_by_fingerprint(conn, already_matched_lr)
    chains.update(fp_chains)

    print(f"\nTotal unique chains: {len(chains)}")

    # Apply to database
    apply_chains(conn, chains)

    # Save HR link cache
    APPEAL_LINKS_CACHE.write_text(json.dumps(updated_cache, ensure_ascii=False, indent=2))
    print(f"Saved {len(updated_cache)} HR appeal links to {APPEAL_LINKS_CACHE}")

    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
