#!/usr/bin/env python3
"""Import Icelandic Bar Association data from lawyers.csv and Reynsla.csv.

lawyers.csv: License events (granted, deposited, revoked, reinstated), birth dates, LMFI URLs.
Reynsla.csv: Current status and experience start date (Excel serial date).

The experience_from date is stored raw so years can be computed dynamically at request time.
"""

import csv
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "verdicts.db"
LAWYERS_CSV = Path(__file__).parent.parent / "lawyers.csv"
LAWYERS_V2_CSV = Path(__file__).parent.parent / "lawyers v2.csv"
REYNSLA_CSV = Path(__file__).parent.parent / "Reynsla.csv"
LOGMENN_CSV = Path(__file__).parent.parent / "logmenn.csv"

LICENSE_TYPE_MAP = {
    "Héraðsdómslögmaður": "hdl",
    "Hæstaréttarlögmaður": "hrl",
    "Landsréttarlögmaður": "lrl",
}

# Reynsla.csv "Staða" -> DB status
REYNSLA_STATUS_MAP = {
    "Héraðsdómslögmaður": "active",
    "Hæstaréttarlögmaður": "active",
    "Landsréttarlögmaður": "active",
    "Hættur": "retired",
}

LICENSE_DISPLAY = {
    "hdl": "Héraðsdómslögmaður",
    "hrl": "Hæstaréttarlögmaður",
    "lrl": "Landsréttarlögmaður",
}

STATUS_DISPLAY = {
    "active": "Virk réttindi",
    "inactive": "Innlagt leyfi",
    "revoked": "Niðurfelling",
    "retired": "Hættur",
}

EVENT_TYPE_DISPLAY = {
    "Lögmannsréttindi": "Réttindi veitt",
    "Innlagt leyfi": "Leyfi innlagt",
    "Niðurfelling": "Réttindi felld niður",
    "Endurveiting": "Réttindi endurveitt",
}


def parse_date(date_str: str) -> str | None:
    """Parse date from 'd.m.yyyy' format to ISO 'yyyy-mm-dd'."""
    if not date_str or not date_str.strip():
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%d.%m.%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def excel_serial_to_iso(serial_str: str) -> str | None:
    """Convert Excel serial date number to ISO date string.

    Excel uses 1899-12-30 as epoch (due to the Lotus 1-2-3 leap year bug).
    European CSVs use dot as thousands separator: '41.837' = 41837.
    """
    if not serial_str or not serial_str.strip():
        return None
    try:
        # Remove dots (thousands separator) and spaces
        cleaned = serial_str.strip().replace(".", "").replace(" ", "")
        serial = int(cleaned)
        if serial < 1 or serial > 100000:
            return None
        # Excel epoch: 1899-12-30
        dt = datetime(1899, 12, 30) + timedelta(days=serial)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def normalize_for_matching(name: str) -> str:
    """Normalize name for matching: lowercase, collapse spaces, fix initials."""
    name = " ".join(name.lower().split())
    name = name.rstrip(".,;:")
    name = re.sub(r"\.([a-záéíóúýþæðö])", r". \1", name)
    return name


def init_tables(conn: sqlite3.Connection):
    """Create bar data tables and ensure columns exist."""
    conn.execute("DROP TABLE IF EXISTS lawyer_events")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lawyer_events (
            id INTEGER PRIMARY KEY,
            lawyer_id INTEGER REFERENCES lawyers(id),
            bar_name TEXT NOT NULL,
            event_date TEXT,
            event_type TEXT NOT NULL,
            license_type TEXT,
            lmfi_url TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lawyer_events_lid ON lawyer_events(lawyer_id)")
    conn.commit()

    # Add columns to lawyers table if not present
    cursor = conn.execute("PRAGMA table_info(lawyers)")
    existing = {row[1] for row in cursor.fetchall()}
    for col, typ in [
        ("license_type", "TEXT"),
        ("license_status", "TEXT"),
        ("lmfi_url", "TEXT"),
        ("license_date", "TEXT"),
        ("birth_date", "TEXT"),
        ("experience_from", "TEXT"),
        ("practice_category", "TEXT"),
        ("practice_subcategory", "TEXT"),
        ("lmfi_id", "INTEGER"),
        ("is_corporate", "INTEGER"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE lawyers ADD COLUMN {col} {typ}")
    conn.commit()


def load_name_aliases() -> dict[str, str]:
    """Load name aliases from data/name_aliases.json.

    Returns a mapping of normalized alias -> normalized canonical name.
    The alias file maps island.is spelling -> LMFI spelling (canonical).
    """
    import json

    alias_path = Path(__file__).parent.parent / "data" / "name_aliases.json"
    if not alias_path.exists():
        return {}
    raw = json.loads(alias_path.read_text(encoding="utf-8"))
    # Map both directions: island.is -> LMFI and LMFI -> LMFI (for completeness)
    aliases = {}
    for island_name, lmfi_name in raw.items():
        aliases[normalize_for_matching(island_name)] = normalize_for_matching(lmfi_name)
    return aliases


def build_name_index(conn: sqlite3.Connection) -> dict[str, int]:
    """Build a normalized name -> lawyer_id lookup from the lawyers table.

    Also loads name aliases so that variant spellings (e.g. island.is vs LMFI)
    resolve to the same lawyer_id.
    """
    rows = conn.execute("SELECT id, name FROM lawyers").fetchall()
    index = {}
    for row in rows:
        normalized = normalize_for_matching(row[1])
        index[normalized] = row[0]

    # Add aliases in BOTH directions so all import functions can match
    # aliases maps: normalized(island.is name) -> normalized(LMFI name)
    aliases = load_name_aliases()
    added = 0
    for alias_norm, canonical_norm in aliases.items():
        # Direction 1: island.is name -> LMFI name's id (DB has LMFI spelling)
        if alias_norm not in index and canonical_norm in index:
            index[alias_norm] = index[canonical_norm]
            added += 1
        # Direction 2: LMFI name -> island.is name's id (DB has island.is spelling)
        if canonical_norm not in index and alias_norm in index:
            index[canonical_norm] = index[alias_norm]
            added += 1
    if added:
        print(f"  Added {added} name aliases to index (both directions)")

    return index


def import_lawyers_csv(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Import license events from lawyers.csv."""
    if not LAWYERS_CSV.exists():
        print(f"lawyers.csv not found at {LAWYERS_CSV}, skipping")
        return

    events = []
    birth_dates: dict[int, str] = {}

    with open(LAWYERS_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            name = row.get("name", "").strip()
            if not name:
                continue

            event_date = parse_date(row.get("date", ""))
            event_type = row.get("type", "").strip()
            license_type = LICENSE_TYPE_MAP.get(row.get("rettindi", "").strip(), "")
            lmfi_url = row.get("url", "").strip()
            birth_date = parse_date(row.get("fæðingardagur", ""))

            normalized = normalize_for_matching(name)
            lawyer_id = name_index.get(normalized)

            if lawyer_id and birth_date:
                birth_dates[lawyer_id] = birth_date

            events.append((
                lawyer_id, name, event_date, event_type, license_type, lmfi_url,
            ))

    print(f"lawyers.csv: {len(events)} events parsed")

    conn.executemany("""
        INSERT INTO lawyer_events (lawyer_id, bar_name, event_date, event_type, license_type, lmfi_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, events)
    conn.commit()

    matched = len({e[0] for e in events if e[0] is not None})
    print(f"  {matched} unique lawyers matched")

    # Update from events: license_type, status, lmfi_url, license_date
    conn.execute("""
        UPDATE lawyers SET
            license_type = (
                SELECT le.license_type FROM lawyer_events le
                WHERE le.lawyer_id = lawyers.id AND le.license_type != ''
                  AND le.event_type IN ('Lögmannsréttindi', 'Endurveiting')
                ORDER BY
                  CASE le.license_type WHEN 'hrl' THEN 3 WHEN 'lrl' THEN 2 WHEN 'hdl' THEN 1 ELSE 0 END DESC
                LIMIT 1
            ),
            license_status = (
                SELECT CASE le.event_type
                    WHEN 'Lögmannsréttindi' THEN 'active'
                    WHEN 'Endurveiting' THEN 'active'
                    WHEN 'Innlagt leyfi' THEN 'inactive'
                    WHEN 'Niðurfelling' THEN 'revoked'
                    ELSE NULL
                END
                FROM lawyer_events le
                WHERE le.lawyer_id = lawyers.id
                ORDER BY le.event_date DESC LIMIT 1
            ),
            lmfi_url = (
                SELECT le.lmfi_url FROM lawyer_events le
                WHERE le.lawyer_id = lawyers.id AND le.lmfi_url != ''
                ORDER BY le.event_date DESC LIMIT 1
            ),
            license_date = (
                SELECT le.event_date FROM lawyer_events le
                WHERE le.lawyer_id = lawyers.id AND le.event_type = 'Lögmannsréttindi'
                ORDER BY le.event_date ASC LIMIT 1
            )
        WHERE id IN (SELECT DISTINCT lawyer_id FROM lawyer_events WHERE lawyer_id IS NOT NULL)
    """)

    for lawyer_id, bd in birth_dates.items():
        conn.execute("UPDATE lawyers SET birth_date = ? WHERE id = ?", (bd, lawyer_id))

    conn.commit()
    print(f"  Updated birth dates for {len(birth_dates)} lawyers")


def import_reynsla_csv(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Import experience data from Reynsla.csv.

    This overrides license_status and license_type with more authoritative data,
    and adds experience_from dates for dynamic years-of-experience calculation.
    """
    if not REYNSLA_CSV.exists():
        print(f"Reynsla.csv not found at {REYNSLA_CSV}, skipping")
        return

    matched = 0
    unmatched_names = []

    with open(REYNSLA_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            name = row.get("Nafn", "").strip()
            if not name:
                continue

            stada = row.get("Staða", "").strip()
            reynsla_fra = row.get("Reynsla frá", "")

            normalized = normalize_for_matching(name)
            lawyer_id = name_index.get(normalized)

            if not lawyer_id:
                unmatched_names.append(name)
                continue

            status = REYNSLA_STATUS_MAP.get(stada)
            license_type = LICENSE_TYPE_MAP.get(stada, "")
            experience_from = excel_serial_to_iso(reynsla_fra)

            updates = []
            params = []

            if status:
                updates.append("license_status = ?")
                params.append(status)
            if license_type:
                updates.append("license_type = ?")
                params.append(license_type)
            if experience_from:
                updates.append("experience_from = ?")
                params.append(experience_from)

            if updates:
                params.append(lawyer_id)
                conn.execute(
                    f"UPDATE lawyers SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                matched += 1

    conn.commit()
    print(f"Reynsla.csv: {matched} lawyers updated")
    print(f"  {len(unmatched_names)} names not found in verdicts DB")

    if unmatched_names[:10]:
        print(f"  Sample unmatched: {', '.join(unmatched_names[:10])}")


# logmenn.csv réttindi -> license_type, ranked by precedence (highest wins)
RETTINDI_MAP = {
    "Málflutningsréttindi fyrir héraðsdómstólunum": ("hdl", 1),
    "Málflutningsréttindi fyrir Landsrétti": ("lrl", 2),
    "Málflutningsréttindi fyrir Hæstarétti": ("hrl", 3),
}


def import_logmenn_csv(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Import license type data from logmenn.csv (Latin-1 encoded).

    A lawyer may appear multiple times (one row per license level).
    We pick the highest level: Hæstaréttur > Landsréttur > Héraðsdómstólar.
    Only fills in license_type for lawyers that don't already have one.
    """
    if not LOGMENN_CSV.exists():
        print(f"logmenn.csv not found at {LOGMENN_CSV}, skipping")
        return

    # Collect highest license type per lawyer
    best_type: dict[int, tuple[str, int]] = {}  # lawyer_id -> (type, precedence)

    with open(LOGMENN_CSV, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            name = row.get("Nafn", "").strip()
            rettindi = row.get("Tegund réttinda", "").strip()
            if not name or not rettindi:
                continue

            mapped = RETTINDI_MAP.get(rettindi)
            if not mapped:
                continue

            license_type, precedence = mapped
            normalized = normalize_for_matching(name)
            lawyer_id = name_index.get(normalized)

            if not lawyer_id:
                continue

            current = best_type.get(lawyer_id)
            if not current or precedence > current[1]:
                best_type[lawyer_id] = (license_type, precedence)

    # Only update lawyers that currently have no license_type
    rows = conn.execute(
        "SELECT id FROM lawyers WHERE license_type IS NULL OR license_type = ''"
    ).fetchall()
    missing_ids = {row[0] for row in rows}

    updated = 0
    for lawyer_id, (license_type, _) in best_type.items():
        if lawyer_id in missing_ids:
            conn.execute(
                "UPDATE lawyers SET license_type = ? WHERE id = ?",
                (license_type, lawyer_id),
            )
            updated += 1

    conn.commit()
    print(f"logmenn.csv: {len(best_type)} lawyers matched, {updated} filled in missing license_type")


# lawyers v2.csv qualification -> license_type
QUALIFICATION_MAP = {
    "Lögmaður með réttindi til málflutnings fyrir héraðsdómstólum": "hdl",
    "Lögmaður með réttindi til málflutnings fyrir héraðsdómstólum og Landsrétti": "lrl",
    "Lögmaður með réttindi til málflutnings fyrir héraðsdómstólum, Landsrétti og Hæstarétti": "hrl",
}


def import_lawyers_v2_csv(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Import authoritative lawyer data from 'lawyers v2.csv'.

    This is the most reliable source — overrides license_type, lmfi_url,
    license_date, and adds practice categories and LMFI IDs.
    """
    if not LAWYERS_V2_CSV.exists():
        print(f"lawyers v2.csv not found at {LAWYERS_V2_CSV}, skipping")
        return

    matched = 0
    unmatched_names = []

    with open(LAWYERS_V2_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            name = row.get("Name", "").strip()
            if not name:
                continue

            normalized = normalize_for_matching(name)
            lawyer_id = name_index.get(normalized)

            if not lawyer_id:
                unmatched_names.append(name)
                continue

            qualification = row.get("Qualification", "").strip()
            url = row.get("URL", "").strip()
            practice_cat = row.get("Practice_Category", "").strip()
            practice_sub = row.get("Practice_Subcategory", "").strip()
            rettindi_date_raw = row.get("Rettindi_Date", "").strip()
            lmfi_id_raw = row.get("Lawyer_ID", "").strip()

            license_type = QUALIFICATION_MAP.get(qualification, "")
            lmfi_id = int(lmfi_id_raw) if lmfi_id_raw.isdigit() else None

            # Parse date: "2010-04-30 00:00:00" -> "2010-04-30"
            rettindi_date = rettindi_date_raw[:10] if len(rettindi_date_raw) >= 10 else None

            updates = []
            params = []

            if license_type:
                updates.append("license_type = ?")
                params.append(license_type)
            if url:
                updates.append("lmfi_url = ?")
                params.append(url)
            if rettindi_date:
                updates.append("license_date = ?")
                params.append(rettindi_date)
                # Also set experience_from if not already set
                updates.append("experience_from = COALESCE(experience_from, ?)")
                params.append(rettindi_date)
            if practice_cat:
                updates.append("practice_category = ?")
                params.append(practice_cat)
            if practice_sub:
                updates.append("practice_subcategory = ?")
                params.append(practice_sub)
            if lmfi_id is not None:
                updates.append("lmfi_id = ?")
                params.append(lmfi_id)

            if updates:
                params.append(lawyer_id)
                conn.execute(
                    f"UPDATE lawyers SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                matched += 1

    conn.commit()
    print(f"lawyers v2.csv: {matched} lawyers updated")
    print(f"  {len(unmatched_names)} names not found in verdicts DB")
    if unmatched_names[:10]:
        print(f"  Sample unmatched: {', '.join(unmatched_names[:10])}")


def import_lmfi_json(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Import LMFI profile URLs and corporate flag from scraped data/lmfi_lawyers.json.

    Fills in lmfi_url for lawyers matched by name who don't already have one.
    Sets is_corporate=1 for lawyers on the innanhúslögmenn list.
    """
    import json

    lmfi_path = Path(__file__).parent.parent / "data" / "lmfi_lawyers.json"
    if not lmfi_path.exists():
        print(f"lmfi_lawyers.json not found at {lmfi_path}, skipping")
        return

    lmfi = json.loads(lmfi_path.read_text(encoding="utf-8"))
    matched = 0
    corporate_count = 0

    for name, info in lmfi.items():
        if isinstance(info, dict):
            url = info.get("url", "")
            is_corporate = 1 if info.get("is_corporate") else 0
        else:
            # Legacy format: {"name": "license_type"}
            url = ""
            is_corporate = 0

        normalized = normalize_for_matching(name)
        lawyer_id = name_index.get(normalized)
        if not lawyer_id:
            continue

        if url:
            conn.execute(
                "UPDATE lawyers SET lmfi_url = COALESCE(NULLIF(lmfi_url, ''), ?) WHERE id = ?",
                (url, lawyer_id),
            )
        conn.execute(
            "UPDATE lawyers SET is_corporate = ? WHERE id = ?",
            (is_corporate, lawyer_id),
        )
        matched += 1
        if is_corporate:
            corporate_count += 1

    conn.commit()
    print(f"lmfi_lawyers.json: {matched} LMFI URLs matched, {corporate_count} marked as corporate")


def import_island_is_json(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Mark lawyers as active based on island.is government registry.

    island.is is the definitive source for active license status.
    Also updates license_type if island.is reports a higher level.
    """
    import json

    island_path = Path(__file__).parent.parent / "data" / "island_is_lawyers.json"
    if not island_path.exists():
        print(f"island_is_lawyers.json not found at {island_path}, skipping")
        return

    island = json.loads(island_path.read_text(encoding="utf-8"))
    license_rank = {"hdl": 1, "lrl": 2, "hrl": 3}
    matched = 0

    for name, license_type in island.items():
        normalized = normalize_for_matching(name)
        lawyer_id = name_index.get(normalized)
        if not lawyer_id:
            continue

        # Always set active; upgrade license_type if island.is reports higher
        current = conn.execute(
            "SELECT license_type FROM lawyers WHERE id = ?", (lawyer_id,)
        ).fetchone()
        current_type = current[0] if current and current[0] else ""
        current_rank = license_rank.get(current_type, 0)
        new_rank = license_rank.get(license_type, 0)

        if new_rank > current_rank:
            conn.execute(
                "UPDATE lawyers SET license_status = 'active', license_type = ? WHERE id = ?",
                (license_type, lawyer_id),
            )
        else:
            conn.execute(
                "UPDATE lawyers SET license_status = 'active' WHERE id = ?",
                (lawyer_id,),
            )
        matched += 1

    conn.commit()
    print(f"island_is_lawyers.json: {matched} lawyers marked active (definitive)")


def apply_manual_overrides(conn: sqlite3.Connection, name_index: dict[str, int]):
    """Apply manual overrides from data/lawyer_overrides.json.

    This runs last and overrides any previous data for lawyers not properly
    represented on lmfi.is or island.is. Supports: license_status, license_type,
    is_corporate, lmfi_url.
    """
    import json

    overrides_path = Path(__file__).parent.parent / "data" / "lawyer_overrides.json"
    if not overrides_path.exists():
        print(f"lawyer_overrides.json not found at {overrides_path}, skipping")
        return

    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    applied = 0

    for name, fields in overrides.items():
        if name.startswith("_"):
            continue
        if not isinstance(fields, dict):
            continue

        normalized = normalize_for_matching(name)
        lawyer_id = name_index.get(normalized)
        if not lawyer_id:
            print(f"  WARNING: Override for '{name}' — not found in DB")
            continue

        updates = []
        values = []
        for key, val in fields.items():
            if key == "is_corporate":
                updates.append("is_corporate = ?")
                values.append(1 if val else 0)
            elif key == "license_status":
                updates.append("license_status = ?")
                values.append(val)
            elif key == "license_type":
                updates.append("license_type = ?")
                values.append(val)
            elif key == "lmfi_url":
                updates.append("lmfi_url = ?")
                values.append(val)

        if updates:
            values.append(lawyer_id)
            conn.execute(
                f"UPDATE lawyers SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            applied += 1
            print(f"  {name}: {', '.join(updates)}")

    conn.commit()
    print(f"lawyer_overrides.json: {applied} lawyers updated")


def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    init_tables(conn)

    name_index = build_name_index(conn)
    print(f"Built name index with {len(name_index)} lawyers\n")

    # Import lawyers.csv first (events, birth dates, LMFI URLs)
    import_lawyers_csv(conn, name_index)
    print()

    # Import Reynsla.csv second (overrides status/type with authoritative data, adds experience_from)
    import_reynsla_csv(conn, name_index)
    print()

    # Import logmenn.csv (fills in missing license_type only)
    import_logmenn_csv(conn, name_index)
    print()

    # Import lawyers v2.csv — overrides license_type/URL/dates
    import_lawyers_v2_csv(conn, name_index)
    print()

    # Import scraped LMFI URLs (fills gaps from CSVs)
    import_lmfi_json(conn, name_index)
    print()

    # island.is is the definitive source for active status
    import_island_is_json(conn, name_index)
    print()

    # Manual overrides for lawyers not on external lists (runs last, wins all)
    apply_manual_overrides(conn, name_index)
    print()

    # Final status rule for remaining lawyers:
    #   has LMFI URL → active (LMFI members are practicing)
    #   no LMFI URL and not already set active by island.is → inactive
    set_active = conn.execute(
        "UPDATE lawyers SET license_status = 'active' WHERE lmfi_url IS NOT NULL AND lmfi_url != '' AND license_status != 'active'"
    ).rowcount
    set_inactive = conn.execute(
        "UPDATE lawyers SET license_status = 'inactive' WHERE license_status != 'active' AND (lmfi_url IS NULL OR lmfi_url = '')"
    ).rowcount
    conn.commit()
    print(f"Final status: {set_active} more set active (LMFI URL), {set_inactive} set inactive (no LMFI, not on island.is)")

    # Print summary
    print("\n--- Summary ---")
    stats = conn.execute("""
        SELECT license_status, COUNT(*) as cnt
        FROM lawyers WHERE license_status IS NOT NULL
        GROUP BY license_status
    """).fetchall()
    print("Status breakdown:")
    for row in stats:
        print(f"  {STATUS_DISPLAY.get(row[0], row[0])}: {row[1]}")

    type_stats = conn.execute("""
        SELECT license_type, COUNT(*) as cnt
        FROM lawyers WHERE license_type IS NOT NULL AND license_type != ''
        GROUP BY license_type
    """).fetchall()
    print("License type breakdown:")
    for row in type_stats:
        print(f"  {LICENSE_DISPLAY.get(row[0], row[0])}: {row[1]}")

    exp_count = conn.execute(
        "SELECT COUNT(*) FROM lawyers WHERE experience_from IS NOT NULL"
    ).fetchone()[0]
    print(f"Lawyers with experience_from date: {exp_count}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
