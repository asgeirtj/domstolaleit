#!/usr/bin/env python3
"""Extract lawyer names and win/loss outcomes from court verdicts."""

import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "verdicts.db"
ALIAS_PATH = Path(__file__).parent.parent / "data" / "name_aliases.json"

# Patterns for extracting lawyer names from parenthetical references.
# Icelandic courts use: (Name lögmaður), (Name hrl.), (Name hdl.),
# (Name saksóknari), (Name réttargæslumaður), (Name settur saksóknari)
LAWYER_PATTERN = re.compile(
    r"\(([^()]+?)\s+"
    r"(?:lögmaður|hrl\.|hdl\.|saksóknari|réttargæslumaður|"
    r"settur\s+saksóknari|aðstoðarsaksóknari|ríkissaksóknari|"
    r"sýslumaður|löglærður\s+fulltrúi)"
    r"(?:,\s*\d+\.\s*prófmál)?"  # optional "4. prófmál" suffix
    r"\)",
    re.IGNORECASE,
)

# Detect role from the parenthetical text
ROLE_PATTERN = re.compile(
    r"(?:saksóknari|settur\s+saksóknari|aðstoðarsaksóknari|ríkissaksóknari"
    r"|sýslumaður|settur\s+sýslumaður)",
    re.IGNORECASE,
)

# Criminal case plaintiff indicators
CRIMINAL_PLAINTIFFS = {
    "ákæruvaldið",
    "ákæruvaldsins",
    "héraðssaksóknari",
    "ríkissaksóknari",
    "ríkislögreglustjóri",
    "lögreglustjórinn",
    "lögreglustjóra",
    "sýslumaðurinn",
    "sýslumaður",
}

# Dómsorð section markers
DOMSORD_PATTERN = re.compile(
    r"(?:D\s*[óÓ]\s*M\s*S\s*O\s*R\s*[ðÐ]|Dómsord|Dómsorð|Úrskurðarorð)\s*:?\s*\n",
    re.IGNORECASE,
)


def is_criminal_case(header_text: str) -> bool:
    """Check if the case is criminal based on plaintiff being prosecution."""
    lower = header_text.lower()
    return any(term in lower for term in CRIMINAL_PLAINTIFFS)


def extract_domsord(text: str) -> str:
    """Extract the Dómsorð (verdict/conclusion) section from the end of the text."""
    match = DOMSORD_PATTERN.search(text)
    if match:
        return text[match.end():]
    # Fallback: last 2000 chars likely contain the conclusion
    return text[-2000:]


def is_procedural_order(full_text: str) -> bool:
    """Check if this is a procedural order (úrskurður) rather than a substantive verdict.

    Only flag as procedural if the conclusion section uses "Úrskurðarorð"
    (not "Dómsorð"), which means it's a ruling on a procedural matter like
    custody, search warrants, etc.
    """
    # Look for "Úrskurðarorð" as the conclusion header (not "Dómsorð")
    return bool(re.search(
        r"(?:Ú|ú)rskurðarorð\s*:?\s*\n", full_text
    )) and not bool(re.search(
        r"(?:D\s*[óÓ]\s*M\s*S\s*O\s*R\s*[ðÐ]|Dómsorð|Dómsorð)\s*:?\s*\n", full_text
    ))


# Regex patterns for civil outcome detection.
# Use .{0,120}? (lazy, allows periods in "ehf." etc.) instead of [^.] which
# would stop at abbreviation dots and miss "stefndi, Búð ehf., greiði".
_S = re.IGNORECASE | re.DOTALL  # DOTALL: '.' matches newlines in Domsord text
_CIVIL_DEF_WINS_RE = [
    re.compile(r"stefnd\w*.{0,120}?(?:er |skal vera |eru )?sýkn", _S),
    re.compile(r"sýkna ber stefnd", re.IGNORECASE),
    re.compile(r"er sýkn af kröfum", re.IGNORECASE),
    re.compile(r"málinu vísað frá", re.IGNORECASE),
    re.compile(r"máli\w* er vísað frá", re.IGNORECASE),
    re.compile(r"vísað\s+(?:er\s+)?frá dómi", re.IGNORECASE),
    # Appellate: lower court ruling confirmed = appellant (plaintiff) loses
    re.compile(r"kærð\w+ úrskurð\w+.{0,30}?staðfest", _S),
    re.compile(r"staðfest er.{0,60}?niðurstaða", _S),
    re.compile(r"staðfest er ákvörðun", re.IGNORECASE),
]

_CIVIL_PL_WINS_RE = [
    re.compile(r"stefnd\w*.{0,120}?(?:ber að |skal |er gert að )?greið[iae]", _S),
    re.compile(r"er dæm\w+ til greiðslu", re.IGNORECASE),
    re.compile(r"varnaraðil\w*.{0,120}?(?:ber að |skal )?greið", _S),
    re.compile(r"áfrýjand\w*.{0,120}?greið\w+\s+stefn", _S),
    # Appellate: lower court ruling reversed = appellant (plaintiff) wins
    re.compile(r"ómerkt|ómerktur", re.IGNORECASE),
    re.compile(r"fellt úr gildi|felldur úr gildi|felld úr gildi", re.IGNORECASE),
    re.compile(r"hnekkt", re.IGNORECASE),
]


def determine_outcome_criminal(domsord: str) -> tuple[str, str]:
    """Determine outcome for criminal case. Returns (prosecution_outcome, defense_outcome)."""
    lower = domsord.lower()

    defense_wins = any(term in lower for term in [
        "sýknaður", "sýknað", "sýkna ber", "sýkn af", "ákæru vísað frá",
        "er sýkn",
    ])
    # Appellate: if prosecution appealed, "staðfestur" = their appeal rejected = defense wins
    if not defense_wins:
        defense_wins = bool(re.search(r"kærð\w+ úrskurð\w+.{0,30}?staðfest", lower, re.DOTALL))

    prosecution_wins = any(term in lower for term in [
        "fangelsi", "sekt ", "sektar", "sakfelld", "sakfellt",
        "skilorðsbundin", "samfélagsþjónust", "fésekt",
        "hegningarauka", "ökuréttarsvipting",
    ]) or bool(re.search(r"(?:frestað|fresta)\s.{0,30}?ákvörðun\s+refsing", lower, re.DOTALL))
    # Appellate: if prosecution appealed, reversal = prosecution wins
    if not prosecution_wins:
        prosecution_wins = bool(re.search(r"ómerkt|ómerktur|fellt úr gildi|felldur úr gildi|hnekkt", lower))
    # "frestað er ákvörðun refsingar" = deferred sentencing = conviction

    if defense_wins and not prosecution_wins:
        return ("loss", "win")
    if prosecution_wins and not defense_wins:
        return ("win", "loss")
    # When both indicators present, the conviction signal is usually stronger
    # (partial acquittal still means the defendant was convicted on something)
    if prosecution_wins and defense_wins:
        return ("win", "loss")
    return ("unknown", "unknown")


def determine_outcome_civil(domsord: str) -> tuple[str, str]:
    """Determine outcome for civil case. Returns (plaintiff_outcome, defendant_outcome)."""
    lower = domsord.lower()

    # Check for case dropped/dismissed (neither side wins)
    if "mál þetta er fellt niður" in lower:
        return ("unknown", "unknown")

    defendant_wins = any(pat.search(domsord) for pat in _CIVIL_DEF_WINS_RE)
    plaintiff_wins = any(pat.search(domsord) for pat in _CIVIL_PL_WINS_RE)

    # Also check for simple "frávísun" as defendant win
    if not defendant_wins and "frávísun" in lower:
        defendant_wins = True

    if defendant_wins and not plaintiff_wins:
        return ("loss", "win")
    if plaintiff_wins and not defendant_wins:
        return ("win", "loss")
    # When both: defendant acquitted on main claim but ordered to pay costs,
    # or split verdict - treat as defendant win (acquittal is the main outcome)
    if defendant_wins and plaintiff_wins:
        return ("loss", "win")
    return ("unknown", "unknown")


def find_gegn_position(header: str) -> int | None:
    """Find the position of 'gegn' (versus) in the header text."""
    match = re.search(r"\ngegn\n", header, re.IGNORECASE)
    return match.start() if match else None


def _find_formal_header_start(header: str, gegn_pos: int) -> int:
    """Find the start of the formal header block containing 'gegn'.

    Many héraðsdómstólar verdicts have a compressed summary header before the
    formal header. The summary lists ALL parties/lawyers without 'gegn', so
    parsing it assigns everyone to the plaintiff side. We skip it by finding
    the last 'D Ó M U R' marker before 'gegn'.
    """
    # Find all "D Ó M U R" or "DÓMUR" markers
    domur_pattern = re.compile(r"D\s*Ó\s*M\s*U\s*R", re.IGNORECASE)
    markers = list(domur_pattern.finditer(header[:gegn_pos]))
    if len(markers) >= 2:
        # Use the second marker (formal header start)
        return markers[1].start()
    return 0


def extract_lawyers_from_verdict(text: str) -> list[dict]:
    """Extract all lawyers and their roles from a single verdict.

    Returns list of dicts with keys: name, role, party_name, outcome
    """
    # Use first ~3000 chars for header (parties section)
    header = text[:3000]
    gegn_pos = find_gegn_position(header)

    if gegn_pos is None:
        return []

    # Skip preamble summary header — only parse the formal header block
    formal_start = _find_formal_header_start(header, gegn_pos)
    plaintiff_section = header[formal_start:gegn_pos]
    defendant_section = header[gegn_pos:]

    criminal = is_criminal_case(plaintiff_section)

    # Get domsord for outcome
    domsord = extract_domsord(text)

    if criminal:
        pl_outcome, def_outcome = determine_outcome_criminal(domsord)
    else:
        pl_outcome, def_outcome = determine_outcome_civil(domsord)

    results = []

    # Extract lawyers from plaintiff side
    for match in LAWYER_PATTERN.finditer(plaintiff_section):
        raw_name = match.group(1).strip()
        # Handle comma-separated lawyers: "Baldvin Hafsteinsson lögmaður,\nSkúli Sveinsson"
        # The regex captures the name before the role word
        names = split_lawyer_names(raw_name)
        for name in names:
            is_prosecutor = bool(ROLE_PATTERN.search(match.group(0)))
            role = "prosecutor" if (criminal and is_prosecutor) else "plaintiff_lawyer"
            results.append({
                "name": normalize_name(name),
                "role": role,
                "party_name": None,
                "outcome": pl_outcome,
            })

    # Extract lawyers from defendant side
    for match in LAWYER_PATTERN.finditer(defendant_section):
        raw_name = match.group(1).strip()
        names = split_lawyer_names(raw_name)
        for name in names:
            role = "defense_lawyer" if criminal else "defendant_lawyer"
            results.append({
                "name": normalize_name(name),
                "role": role,
                "party_name": None,
                "outcome": def_outcome,
            })

    # Deduplicate by name within same verdict
    seen = set()
    deduped = []
    for r in results:
        key = (r["name"], r["role"])
        if key not in seen and r["name"]:
            seen.add(key)
            deduped.append(r)

    return deduped


def split_lawyer_names(raw: str) -> list[str]:
    """Split multi-lawyer names that got captured in a single regex match.

    The LAWYER_PATTERN may capture multiple lawyers as one group when they share
    a parenthetical, e.g.:
      "(Jón Gunnlaugsson lögmaður, Hlynur Jónsson lögmaður)" -> group(1) =
        "Jón Gunnlaugsson lögmaður, Hlynur Jónsson"
      "(Andri Árnason hrl. Bjarki Diego lögmaður)" -> group(1) =
        "Andri Árnason hrl. Bjarki Diego"

    We split on embedded role words (lögmaður, hrl., hdl.) to recover individual names.
    """
    # Remove trailing prófmál info
    cleaned = re.sub(r",?\s*\d+\.\s*prófmál.*$", "", raw).strip()
    if not cleaned:
        return []

    # Split on role words that appear mid-string (followed by comma/space and a capital letter)
    role_split = re.compile(
        r"(?:lögmaður|hrl\.|hdl\.|saksóknari|réttargæslumaður)"
        r"[,\s]+(?=[A-ZÁÉÍÓÚÝÞÆÐÖ])",
        re.IGNORECASE,
    )

    parts = role_split.split(cleaned)
    names = []
    for part in parts:
        # Strip any trailing role words (handles single-lawyer with trailing title)
        name = re.sub(
            r"[,\s]+(?:lögmaður|hrl\.|hdl\.|saksóknari|réttargæslumaður"
            r"|settur\s+saksóknari|aðstoðarsaksóknari|ríkissaksóknari"
            r"|sýslumaður|löglærður\s+fulltrúi)[\s,]*$",
            "", part, flags=re.IGNORECASE,
        ).strip()
        name = name.strip(",").strip()
        if name and len(name) >= 3:
            names.append(name)
    return names


def normalize_name(name: str) -> str:
    """Normalize a lawyer name: trim whitespace, collapse spaces, fix initials."""
    name = " ".join(name.split())
    # Remove any trailing punctuation
    name = name.rstrip(".,;:")
    # Normalize initials: "H.B." -> "H. B.", "H. B." -> "H. B."
    # Ensures consistent spacing after periods in initials
    name = re.sub(r"\.([A-ZÁÉÍÓÚÝÞÆÖ])", r". \1", name)
    # Skip names that are too short or look like abbreviations only
    if len(name) < 3:
        return ""
    # Resolve aliases: map variant spellings to canonical (LMFI) form
    return _NAME_ALIASES.get(name, name)


def _load_name_aliases() -> dict[str, str]:
    """Load name aliases and build a case-preserving lookup.

    The alias file maps verdict_name -> lmfi_name.  We want to resolve
    any variant to a single canonical form so the DB has one entry per person.
    """
    if not ALIAS_PATH.exists():
        return {}
    raw = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    for verdict_name, lmfi_name in raw.items():
        aliases[verdict_name] = lmfi_name
    return aliases


_NAME_ALIASES = _load_name_aliases()


def init_tables(conn: sqlite3.Connection):
    """Create lawyers and case_lawyers tables (additive, doesn't touch existing tables)."""
    conn.execute("DROP TABLE IF EXISTS case_lawyers")
    conn.execute("DROP TABLE IF EXISTS lawyers")

    conn.execute("""
        CREATE TABLE lawyers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            case_count INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE case_lawyers (
            id INTEGER PRIMARY KEY,
            verdict_id INTEGER NOT NULL REFERENCES verdicts(id),
            lawyer_id INTEGER NOT NULL REFERENCES lawyers(id),
            role TEXT NOT NULL,
            party_name TEXT,
            outcome TEXT,
            UNIQUE(verdict_id, lawyer_id, role)
        )
    """)

    conn.execute("CREATE INDEX idx_case_lawyers_verdict ON case_lawyers(verdict_id)")
    conn.execute("CREATE INDEX idx_case_lawyers_lawyer ON case_lawyers(lawyer_id)")
    conn.commit()


def get_or_create_lawyer(conn: sqlite3.Connection, name: str, cache: dict) -> int:
    """Get or create a lawyer record, using an in-memory cache."""
    if name in cache:
        return cache[name]

    cursor = conn.execute(
        "INSERT OR IGNORE INTO lawyers (name) VALUES (?)", (name,)
    )
    if cursor.lastrowid and cursor.rowcount > 0:
        lawyer_id = cursor.lastrowid
    else:
        row = conn.execute(
            "SELECT id FROM lawyers WHERE name = ?", (name,)
        ).fetchone()
        lawyer_id = row[0]

    cache[name] = lawyer_id
    return lawyer_id


def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get total verdict count
    total = conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
    print(f"Processing {total} verdicts...")

    init_tables(conn)

    lawyer_cache: dict[str, int] = {}
    verdicts_with_lawyers = 0
    total_associations = 0

    # Process all verdicts
    cursor = conn.execute("""
        SELECT v.id, v.court, f.content
        FROM verdicts v
        JOIN verdicts_fts f ON f.rowid = v.id
    """)

    for i, row in enumerate(cursor):
        if (i + 1) % 1000 == 0:
            print(f"  {i + 1}/{total}...")
            conn.commit()

        verdict_id = row["id"]
        content = row["content"] or ""

        if not content:
            continue

        lawyers = extract_lawyers_from_verdict(content)
        if not lawyers:
            continue

        verdicts_with_lawyers += 1

        for lawyer_data in lawyers:
            name = lawyer_data["name"]
            if not name:
                continue

            lawyer_id = get_or_create_lawyer(conn, name, lawyer_cache)

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO case_lawyers
                    (verdict_id, lawyer_id, role, party_name, outcome)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    verdict_id,
                    lawyer_id,
                    lawyer_data["role"],
                    lawyer_data["party_name"],
                    lawyer_data["outcome"],
                ))
                total_associations += 1
            except sqlite3.IntegrityError:
                pass

    conn.commit()

    # Aggregate stats into lawyers table (only count resolved, non-superseded cases)
    print("Aggregating statistics...")
    conn.execute("""
        UPDATE lawyers SET
            case_count = (
                SELECT COUNT(DISTINCT cl.verdict_id)
                FROM case_lawyers cl
                JOIN verdicts v ON v.id = cl.verdict_id
                WHERE cl.lawyer_id = lawyers.id
                  AND cl.outcome != 'unknown'
                  AND v.superseded_by IS NULL
            ),
            wins = (
                SELECT COUNT(*)
                FROM case_lawyers cl
                JOIN verdicts v ON v.id = cl.verdict_id
                WHERE cl.lawyer_id = lawyers.id
                  AND cl.outcome = 'win'
                  AND v.superseded_by IS NULL
            ),
            losses = (
                SELECT COUNT(*)
                FROM case_lawyers cl
                JOIN verdicts v ON v.id = cl.verdict_id
                WHERE cl.lawyer_id = lawyers.id
                  AND cl.outcome = 'loss'
                  AND v.superseded_by IS NULL
            )
    """)
    conn.commit()

    # Print summary
    lawyer_count = conn.execute("SELECT COUNT(*) FROM lawyers").fetchone()[0]
    assoc_count = conn.execute("SELECT COUNT(*) FROM case_lawyers").fetchone()[0]

    print(f"\nResults:")
    print(f"  Verdicts with lawyers: {verdicts_with_lawyers}/{total}")
    print(f"  Unique lawyers: {lawyer_count}")
    print(f"  Lawyer-case associations: {assoc_count}")

    # Top 10 by case count
    print(f"\nTop 10 lawyers by case count:")
    rows = conn.execute("""
        SELECT name, case_count, wins, losses
        FROM lawyers
        ORDER BY case_count DESC
        LIMIT 10
    """).fetchall()
    for row in rows:
        win_rate = (row["wins"] / row["case_count"] * 100) if row["case_count"] > 0 else 0
        print(f"  {row['name']}: {row['case_count']} cases, "
              f"{row['wins']}W/{row['losses']}L ({win_rate:.0f}%)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
