"""Local search using SQLite FTS5 index."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "verdicts.db"

# Icelandic month names to numbers
MONTHS = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4,
    "maí": 5, "júní": 6, "júlí": 7, "ágúst": 8,
    "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}

# Pattern to extract date from Icelandic court documents
DATE_PATTERN = re.compile(
    r"(\d{1,2})\.\s*(janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember)\s*(\d{4})",
    re.IGNORECASE
)


def extract_date(text: str) -> date | None:
    """Extract date from document text."""
    # Try multiple patterns - Landsréttur uses "Úrskurður [dag] [mánuður] [ár]"
    # Héraðsdómstólar use "Dómur [dag]. [mánuður] [ár]"
    match = DATE_PATTERN.search(text[:800])  # Date is usually near the start
    if match:
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).lower(), 1)
        year = int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def extract_keywords(text: str) -> str | None:
    """Extract keywords (Lykilorð) from document text."""
    # Landsréttur/Héraðsdómstólar: "Lykilorð" section
    match = re.search(r"Lykilorð\s*\n(.+?)(?:\n\n|Útdráttur|$)", text[:2000], re.DOTALL)
    if match:
        keywords = match.group(1).strip()
        keywords = " ".join(keywords.split())
        return keywords[:200] if keywords else None

    # Hæstiréttur: Keywords after "Kærumál." or similar on same line
    # Format: "Kærumál. Keyword1. Keyword2. Keyword3."
    match = re.search(r"(?:Kærumál|Hæstaréttarmál)\.\s*\n?\s*(.+?)(?:\n[A-ZÁÐÉÍÓÚÝÞÆÖ]|\n\n)", text[:2000], re.DOTALL)
    if match:
        keywords = match.group(1).strip()
        keywords = " ".join(keywords.split())
        # Remove trailing period patterns
        keywords = re.sub(r'\.\s*$', '', keywords)
        return keywords[:200] if keywords else None

    return None


def extract_summary(text: str) -> str | None:
    """Extract summary (Útdráttur/Reifun) from document text."""
    # Landsréttur/Héraðsdómstólar: "Útdráttur" or "Reifun" section
    match = re.search(
        r"(?:Útdráttur|Reifun)\s*\n(.+?)(?:\n\n[A-ZÁÐÉÍÓÚÝÞÆÖ]|\nÚrskurður|\nDómur|\nI\.\s*$)",
        text[:5000],
        re.DOTALL
    )
    if match:
        summary = match.group(1).strip()
        summary = " ".join(summary.split())
        return summary if summary else None

    # Hæstiréttur older format: Summary after keywords, before "Dómur Hæstaréttar"
    # Format: "Kærumál. [keywords].\n[Summary]\nDómur\n Hæstaréttar."
    match = re.search(
        r"(?:Kærumál|Hæstaréttarmál)\.(.+?)\.\n(.+?)(?:Dómur\s*\n\s*Hæstaréttar|Úrskurður\s*\n\s*Hæstaréttar)",
        text[:6000],
        re.DOTALL
    )
    if match:
        summary = match.group(2).strip()
        summary = " ".join(summary.split())
        return summary if summary else None

    # Hæstiréttur older format variant: Keywords as topic words, then summary
    # Format: "Eignarréttur. Gjöf. Þinglýsing.\n[Summary]\nDómur Hæstaréttar"
    match = re.search(
        r"gegn\n.+?\n([A-ZÁÐÉÍÓÚÝÞÆÖ][a-záðéíóúýþæö]+(?:\.\s*[A-ZÁÐÉÍÓÚÝÞÆÖ][a-záðéíóúýþæö]+)+)\.\s*\n(.+?)(?:Dómur\s+Hæstaréttar|Úrskurður\s+Hæstaréttar)",
        text[:6000],
        re.DOTALL
    )
    if match:
        summary = match.group(2).strip()
        summary = " ".join(summary.split())
        return summary if summary else None

    # Hæstiréttur newer format (2024+): "Ágreiningsefni" section with numbered paragraphs
    # Extract the first paragraph(s) after "Ágreiningsefni" header
    match = re.search(
        r"Ágreiningsefni\s*\n(.+?)(?:\n(?:Málsatvik|Málsástæður|Löggjöf|Niðurstaða)\s*\n)",
        text[:8000],
        re.DOTALL
    )
    if match:
        summary = match.group(1).strip()
        # Strip leading paragraph numbers (e.g., "6. ")
        summary = re.sub(r"^\d+\.\s*", "", summary)
        summary = " ".join(summary.split())
        return summary if summary else None

    # Hæstiréttur older format: Starts with "Dómur Hæstaréttar"
    # Find party reference which starts the case description
    match = re.search(
        r"Dómur Hæstaréttar\.?\s*\n.+?((?:Sóknaraðil|Varnaraðil|Áfrýjand|Aðaláfrýjand|Gagnáfrýjand|Ákæruvald|Ákærð).+?)(?:\nDómsorð|\n[IVX]+\s*\n)",
        text[:8000],
        re.DOTALL
    )
    if match:
        summary = match.group(1).strip()
        summary = " ".join(summary.split())
        return summary if summary else None

    return None


def extract_parties(text: str) -> str | None:
    """Extract parties (aðilar) from document text.

    Looks for pattern like:
    Party1
    (lögmaður)
    gegn
    Party2
    """
    # Find the "gegn" marker and extract text around it
    # Pattern works for both Landsréttur and Héraðsdómstólar
    match = re.search(
        r"Mál\s*(?:nr\.?)?\s*[^\n]+\n(.+?)\ngegn\n(.+?)(?:\nLykilorð|\nÚtdráttur|\nDómur|\n[A-Z]{2,}|\n\n)",
        text[:2000],
        re.DOTALL
    )
    if match:
        plaintiff = match.group(1).strip()
        defendant = match.group(2).strip()

        # Clean up - remove lawyer names and other parenthetical notes
        plaintiff = re.sub(r"\s*\([^)]*\)\s*", " ", plaintiff)
        defendant = re.sub(r"\s*\([^)]*\)\s*", " ", defendant)

        # Clean up whitespace and limit length
        plaintiff = " ".join(plaintiff.split())[:100]
        defendant = " ".join(defendant.split())[:100]

        if plaintiff and defendant:
            return f"{plaintiff} gegn {defendant}"
    return None

# Icelandic character pairs for query expansion (only the critical ones)
ICELANDIC_PAIRS = [
    ("ð", "d"),
    ("þ", "th"),
    ("æ", "ae"),
]


def generate_variants(word: str) -> set[str]:
    """Generate all Icelandic character variants of a word.

    Handles multiple substitutions, e.g., skadabaetur -> skaðabætur
    """
    variants = {word}

    # Iterate until no new variants are generated
    changed = True
    while changed:
        changed = False
        new_variants = set()
        for variant in variants:
            for char1, char2 in ICELANDIC_PAIRS:
                if char1 in variant:
                    new_variant = variant.replace(char1, char2)
                    if new_variant not in variants:
                        new_variants.add(new_variant)
                        changed = True
                if char2 in variant:
                    new_variant = variant.replace(char2, char1)
                    if new_variant not in variants:
                        new_variants.add(new_variant)
                        changed = True
        variants.update(new_variants)

    return variants


def sanitize_query(query: str) -> str:
    """Strip characters that break FTS5 syntax (quotes, special operators).

    Handles ASCII quotes and Unicode smart/curly quotes (macOS auto-converts
    straight quotes to smart quotes, and Icelandic uses „..." low-high quotes).
    """
    # Remove all ASCII and Unicode quotation marks
    return re.sub(r'["\'\u201c\u201d\u201e\u201f\u2018\u2019\u201a\u201b\u00ab\u00bb]', '', query).strip()


QUOTE_CHARS = set('"\'\u201c\u201d\u201e\u201f\u2018\u2019\u201a\u201b\u00ab\u00bb')


def is_phrase_query(query: str) -> bool:
    """Check if query is wrapped in quotation marks (phrase search intent)."""
    q = query.strip()
    if len(q) < 3:
        return False
    return q[0] in QUOTE_CHARS and q[-1] in QUOTE_CHARS


def build_phrase_query(query: str) -> str:
    """Build FTS5 phrase query with Icelandic character variants.

    Takes a quoted query like "elisabet petursdottir" and produces
    FTS5-compatible phrase expressions with all variant combinations.
    e.g., '"skadabaetur vegna"' -> '"skadabaetur vegna" OR "skaðabætur vegna" OR ...'
    """
    from itertools import product

    clean = sanitize_query(query).lower()
    words = clean.split()
    if not words:
        return f'"{clean}"'

    word_variants = [sorted(generate_variants(w)) for w in words]

    # Cartesian product of all variant combinations
    phrases = []
    for combo in product(*word_variants):
        phrases.append('"' + " ".join(combo) + '"')

    # Safety cap to avoid query explosion with many variant-rich words
    if len(phrases) > 64:
        return '"' + " ".join(words) + '"'

    return " OR ".join(phrases)


def expand_icelandic_query(query: str) -> str:
    """Expand query to handle Icelandic character variants.

    Converts query to FTS5 OR expression matching both variants.
    e.g., "skilnadur" -> "(skilnadur OR skilnaður)"
    For multiple words: "(word1 OR variant1) AND (word2 OR variant2)"
    """
    query = sanitize_query(query)
    words = query.split()
    if not words:
        return query

    expanded_words = []

    for word in words:
        word_lower = word.lower()
        variants = generate_variants(word_lower)

        if len(variants) > 1:
            # Use FTS5 OR syntax with parentheses
            expanded_words.append("(" + " OR ".join(sorted(variants)) + ")")
        else:
            expanded_words.append(word_lower)

    # Join with OR so documents matching ANY word appear (FTS5 rank
    # still pushes documents matching more words to the top)
    return " OR ".join(expanded_words)

# Fallback search URLs (used when verdict_url is not available in database)
COURT_SEARCH_URLS = {
    "haestirettur": "https://www.haestirettur.is/domar?verdict=",
    "landsrettur": "https://www.landsrettur.is/domar-og-urskurdir?verdict=",
    "heradsdomstolar": "https://www.heradsdomstolar.is/domar?verdict=",
}


@dataclass
class SearchResult:
    """A single search result."""
    id: int
    court: str
    court_display: str
    case_number: str
    snippet: str
    url: str
    date: date | None = None
    keywords: str | None = None
    summary: str | None = None
    parties: str | None = None
    snippets: list[str] | None = None  # Multiple context snippets


# Snippet extraction settings
SNIPPET_CONTEXT = 100  # Characters around each match
MAX_SNIPPETS = 3


def extract_snippets(text: str, query: str) -> list[str]:
    """Extract up to 3 snippets showing search terms in context."""
    if not text or not query:
        return []

    snippets = []
    used_positions = set()

    # Get query words and their variants
    words = query.lower().split()
    all_patterns = []

    for word in words:
        variants = generate_variants(word)
        all_patterns.extend(variants)

    if not all_patterns:
        return []

    # Sort by length (longest first) to match longer forms before shorter
    all_patterns = sorted(set(all_patterns), key=len, reverse=True)

    # Create regex pattern
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(p) for p in all_patterns) + r")\b",
        re.IGNORECASE,
    )

    for match in pattern.finditer(text):
        if len(snippets) >= MAX_SNIPPETS:
            break

        pos = match.start()

        # Skip if too close to existing snippet
        if any(abs(pos - p) < SNIPPET_CONTEXT * 2 for p in used_positions):
            continue

        # Extract snippet with context
        start = max(0, pos - SNIPPET_CONTEXT)
        end = min(len(text), match.end() + SNIPPET_CONTEXT)

        # Extend to word boundaries
        if start > 0:
            space = text.rfind(" ", 0, start)
            if space > start - 30:
                start = space + 1

        if end < len(text):
            space = text.find(" ", end)
            if space != -1 and space < end + 30:
                end = space

        snippet = text[start:end].strip()

        # Add ellipsis
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."

        # Highlight matches with <strong>
        snippet = pattern.sub(r"<strong>\1</strong>", snippet)

        snippets.append(snippet)
        used_positions.add(pos)

    return snippets


def get_court_display_name(court: str) -> str:
    """Get display name for court."""
    names = {
        "haestirettur": "Hæstiréttur",
        "landsrettur": "Landsréttur",
        "heradsdomstolar": "Héraðsdómstólar",
    }
    return names.get(court, court)


def format_case_number(case_number: str) -> str:
    """Format case number for display (e.g., 37___2023 -> 37/2023)."""
    # Replace multiple underscores with /
    import re
    return re.sub(r'_+', '/', case_number)


def build_url(court: str, case_number: str, filename: str) -> str:
    """Build URL to court search results for this case."""
    from urllib.parse import quote

    if court == "haestirettur":
        # Hæstiréttur uses a direct link with verdict ID
        verdict_id = filename.replace(".txt", "").split("_")[0]
        return f"{COURT_SEARCH_URLS[court]}{verdict_id}"
    else:
        # Landsréttur and Héraðsdómstólar use search URLs with case number
        encoded_case = quote(case_number, safe="")
        return f"{COURT_SEARCH_URLS[court]}{encoded_case}"


def search(query: str, courts: list[str] | None = None, limit: int = 100) -> list[SearchResult]:
    """
    Search for verdicts matching query.

    Args:
        query: Search query (supports FTS5 syntax)
        courts: List of courts to search, or None for all
        limit: Maximum results to return

    Returns:
        List of SearchResult objects sorted by date (newest first)
    """
    if not DB_PATH.exists():
        return []

    if not query.strip():
        return []

    # Detect phrase search (query wrapped in any type of quotation marks)
    raw_query = query.strip()
    if is_phrase_query(raw_query):
        expanded_query = build_phrase_query(raw_query)
    else:
        expanded_query = expand_icelandic_query(raw_query)

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row

    # Build court filter
    if courts:
        court_filter = f"AND v.court IN ({','.join('?' * len(courts))})"
        params = [expanded_query] + courts + [limit]
    else:
        court_filter = ""
        params = [expanded_query, limit]

    # FTS5 search with content extraction
    sql = f"""
        SELECT
            v.id,
            v.court,
            v.case_number,
            v.filename,
            v.verdict_url,
            verdicts_fts.content as full_content
        FROM verdicts_fts
        JOIN verdicts v ON verdicts_fts.rowid = v.id
        WHERE verdicts_fts MATCH ?
        {court_filter}
        ORDER BY rank
        LIMIT ?
    """

    try:
        cursor = conn.execute(sql, params)
        results = []
        for row in cursor:
            content = row["full_content"] or ""
            content_start = content[:5000]
            verdict_date = extract_date(content_start)
            keywords = extract_keywords(content_start)
            summary = extract_summary(content_start)
            parties = extract_parties(content_start)

            # Extract up to 3 snippets with search terms highlighted
            snippets = extract_snippets(content, query)

            # Use stored verdict_url if available, otherwise fall back to local view
            url = row["verdict_url"] if row["verdict_url"] else f"/domur/{row['id']}"

            results.append(SearchResult(
                id=row["id"],
                court=row["court"],
                court_display=get_court_display_name(row["court"]),
                case_number=format_case_number(row["case_number"]),
                snippet=snippets[0] if snippets else "",
                url=url,
                date=verdict_date,
                keywords=keywords,
                summary=summary,
                parties=parties,
                snippets=snippets,
            ))

        # Sort by date (newest first), None dates at the end
        results.sort(key=lambda r: r.date or date(1900, 1, 1), reverse=True)
        return results
    except sqlite3.OperationalError as e:
        # Handle FTS syntax errors gracefully
        if "fts5" in str(e).lower() or "syntax" in str(e).lower() or "unterminated" in str(e).lower():
            # Sanitize and retry as a simple quoted phrase
            clean = sanitize_query(query)
            if not clean:
                return []
            escaped = f'"{clean}"'
            params[0] = escaped
            try:
                cursor = conn.execute(sql, params)
                results = []
                for row in cursor:
                    url = row["verdict_url"] if row["verdict_url"] else f"/domur/{row['id']}"
                    results.append(SearchResult(
                        id=row["id"],
                        court=row["court"],
                        court_display=get_court_display_name(row["court"]),
                        case_number=format_case_number(row["case_number"]),
                        snippet="",
                        url=url,
                    ))
                return results
            except sqlite3.OperationalError:
                return []
        raise
    finally:
        conn.close()


def get_stats() -> dict:
    """Get index statistics."""
    if not DB_PATH.exists():
        return {"total": 0, "by_court": {}}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT court, COUNT(*) as count
        FROM verdicts
        GROUP BY court
    """)

    by_court = {}
    total = 0
    for row in cursor:
        by_court[row[0]] = row[1]
        total += row[1]

    conn.close()

    return {
        "total": total,
        "by_court": by_court,
    }


@dataclass
class Verdict:
    """A full verdict document."""
    id: int
    court: str
    court_display: str
    case_number: str
    content: str
    filename: str


def get_verdict(verdict_id: int) -> Verdict | None:
    """Get a single verdict by ID."""
    if not DB_PATH.exists():
        return None

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row

        # Get metadata from main table
        cursor = conn.execute("""
            SELECT v.id, v.court, v.case_number, v.filename, f.content
            FROM verdicts v
            JOIN verdicts_fts f ON f.rowid = v.id
            WHERE v.id = ?
        """, (verdict_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return Verdict(
            id=row["id"],
            court=row["court"],
            court_display=get_court_display_name(row["court"]),
            case_number=format_case_number(row["case_number"]),
            content=row["content"],
            filename=row["filename"],
        )
    except sqlite3.OperationalError:
        # Database locked (indexer running)
        return None
