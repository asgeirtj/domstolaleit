"""Database queries for lawyer data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "verdicts.db"


@dataclass
class LawyerSummary:
    """Lawyer row for the leaderboard."""
    id: int
    name: str
    case_count: int
    wins: int
    losses: int
    win_rate: float  # 0.0 - 100.0
    license_type: str | None = None  # hdl or hrl
    license_status: str | None = None  # active, inactive, revoked
    license_date: str | None = None  # first license date (ISO)
    years_active: float | None = None  # years since first license
    years_active_approx: bool = False  # True if estimated from fallback date
    age: float | None = None  # current age from birth_date
    lmfi_url: str | None = None
    practice_category: str | None = None


@dataclass
class CaseRecord:
    """A single case associated with a lawyer."""
    verdict_id: int
    court: str
    court_display: str
    case_number: str
    role: str
    outcome: str | None
    verdict_url: str | None = None


@dataclass
class LawyerEvent:
    """A single bar association event."""
    date: str | None
    event_type: str
    license_type: str | None


@dataclass
class LawyerProfile:
    """Full lawyer profile with per-court breakdown and case list."""
    id: int
    name: str
    case_count: int
    wins: int
    losses: int
    win_rate: float
    by_court: dict[str, dict] = field(default_factory=dict)
    cases: list[CaseRecord] = field(default_factory=list)
    roles: dict[str, int] = field(default_factory=dict)
    license_type: str | None = None
    license_status: str | None = None
    license_date: str | None = None
    lmfi_url: str | None = None
    events: list[LawyerEvent] = field(default_factory=list)
    years_active: int | None = None
    age: int | None = None
    practice_category: str | None = None
    practice_subcategory: str | None = None


COURT_DISPLAY = {
    "haestirettur": "Hæstiréttur",
    "landsrettur": "Landsréttur",
    "heradsdomstolar": "Héraðsdómstólar",
}

ROLE_DISPLAY = {
    "plaintiff_lawyer": "Lögmaður stefnanda",
    "defendant_lawyer": "Lögmaður stefnda",
    "prosecutor": "Saksóknari",
    "defense_lawyer": "Verjandi",
}

LICENSE_TYPE_DISPLAY = {
    "hdl": "Hdl.",
    "hrl": "Hrl.",
    "lrl": "Lrl.",
}

LICENSE_STATUS_DISPLAY = {
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


def _years_since(iso_date: str | None) -> float | None:
    """Calculate years since an ISO date string, with one decimal."""
    if not iso_date:
        return None
    from datetime import date
    try:
        d = date.fromisoformat(iso_date)
        today = date.today()
        days = (today - d).days
        return round(days / 365.25, 1)
    except ValueError:
        return None


def _connect() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _calc_win_rate(wins: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(wins / total * 100, 1)


def get_leaderboard(
    sort_by: str = "case_count",
    sort_dir: str = "desc",
    min_cases: int = 5,
    name_query: str | None = None,
    exclude_prosecutors: bool = False,
    exclude_criminal: bool = False,
    exclude_retired: bool = False,
    exclude_corporate: bool = False,
    limit: int = 5000,
) -> list[LawyerSummary]:
    """Get lawyer leaderboard with optional filtering."""
    conn = _connect()
    if not conn:
        return []

    direction = "ASC" if sort_dir == "asc" else "DESC"

    # Any filter that requires recalculating from case_lawyers
    needs_recalc = exclude_prosecutors or exclude_criminal

    if needs_recalc:
        # Build WHERE clauses for case_lawyers subquery
        cl_filters = []
        if exclude_prosecutors:
            cl_filters.append("cl.role != 'prosecutor'")
        if exclude_criminal:
            # Exclude S- cases from héraðsdómstólar (criminal/sakamál)
            cl_filters.append("NOT (v.court = 'heradsdomstolar' AND v.case_number LIKE 'S-%')")

        cl_where = " AND ".join(cl_filters)
        # Need a JOIN to verdicts if filtering by case_number
        verdict_join = "JOIN verdicts v ON v.id = cl.verdict_id" if exclude_criminal else ""
        if not exclude_criminal:
            cl_where = cl_where  # no verdict join needed

        stats_sorts = {
            "case_count": f"stats.case_count {direction}",
            "wins": f"stats.wins {direction}",
            "losses": f"stats.losses {direction}",
            "win_rate": f"CAST(stats.wins AS REAL) / NULLIF(stats.case_count, 0) {direction}",
            "name": f"l.name {direction}",
            "years_active": f"COALESCE(l.experience_from, l.license_date, '2001-01-31') {direction}",
            "age": f"l.birth_date {direction}",
        }
        order = stats_sorts.get(sort_by, f"stats.case_count {direction}")
        conditions = ["stats.case_count >= ?"]
        params: list = [min_cases]

        if name_query and name_query.strip():
            conditions.append("l.name LIKE ?")
            params.append(f"%{name_query.strip()}%")
        if exclude_retired:
            conditions.append("l.license_status = 'active'")
        if exclude_corporate:
            conditions.append("COALESCE(l.is_corporate, 0) = 0")

        where = " AND ".join(conditions)
        params.append(limit)

        # Always join verdicts in the subquery when we have filters
        rows = conn.execute(f"""
            SELECT l.id, l.name,
                   stats.case_count, stats.wins, stats.losses,
                   l.license_type, l.license_status,
                   COALESCE(l.experience_from, l.license_date) as experience_from,
                   l.birth_date, l.lmfi_url, l.practice_category
            FROM lawyers l
            JOIN (
                SELECT cl.lawyer_id,
                       COUNT(DISTINCT CASE WHEN cl.outcome != 'unknown' THEN cl.verdict_id END) as case_count,
                       SUM(CASE WHEN cl.outcome = 'win' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN cl.outcome = 'loss' THEN 1 ELSE 0 END) as losses
                FROM case_lawyers cl
                JOIN verdicts v ON v.id = cl.verdict_id
                WHERE {cl_where} AND v.superseded_by IS NULL
                GROUP BY cl.lawyer_id
            ) stats ON stats.lawyer_id = l.id
            WHERE {where}
            ORDER BY {order}
            LIMIT ?
        """, params).fetchall()
    else:
        allowed_sorts = {
            "case_count": f"case_count {direction}",
            "wins": f"wins {direction}",
            "losses": f"losses {direction}",
            "win_rate": f"CAST(wins AS REAL) / NULLIF(case_count, 0) {direction}",
            "name": f"name {direction}",
            "years_active": f"COALESCE(experience_from, license_date, '2001-01-31') {direction}",
            "age": f"birth_date {direction}",
        }
        order = allowed_sorts.get(sort_by, f"case_count {direction}")

        conditions = ["case_count >= ?"]
        params = [min_cases]

        if name_query and name_query.strip():
            conditions.append("name LIKE ?")
            params.append(f"%{name_query.strip()}%")
        if exclude_retired:
            conditions.append("license_status = 'active'")
        if exclude_corporate:
            conditions.append("COALESCE(is_corporate, 0) = 0")

        where = " AND ".join(conditions)
        params.append(limit)

        rows = conn.execute(f"""
            SELECT id, name, case_count, wins, losses,
                   license_type, license_status,
                   COALESCE(experience_from, license_date) as experience_from,
                   birth_date, lmfi_url, practice_category
            FROM lawyers
            WHERE {where}
            ORDER BY {order}
            LIMIT ?
        """, params).fetchall()

    conn.close()

    return [
        LawyerSummary(
            id=r["id"],
            name=r["name"],
            case_count=r["case_count"],
            wins=r["wins"],
            losses=r["losses"],
            win_rate=_calc_win_rate(r["wins"], r["case_count"]),
            license_type=r["license_type"],
            license_status=r["license_status"],
            license_date=r["experience_from"],
            years_active=_years_since(r["experience_from"] or "2001-01-31"),
            years_active_approx=r["experience_from"] is None,
            age=_years_since(r["birth_date"]),
            lmfi_url=r["lmfi_url"],
            practice_category=r["practice_category"],
        )
        for r in rows
    ]


def get_lawyer(lawyer_id: int) -> LawyerProfile | None:
    """Get full lawyer profile with cases and per-court breakdown."""
    conn = _connect()
    if not conn:
        return None

    row = conn.execute(
        "SELECT id, name, case_count, wins, losses, license_type, license_status, COALESCE(experience_from, license_date) as experience_from, lmfi_url, birth_date, practice_category, practice_subcategory FROM lawyers WHERE id = ?",
        (lawyer_id,),
    ).fetchone()

    if not row:
        conn.close()
        return None

    profile = LawyerProfile(
        id=row["id"],
        name=row["name"],
        case_count=row["case_count"],
        wins=row["wins"],
        losses=row["losses"],
        win_rate=_calc_win_rate(row["wins"], row["case_count"]),
        license_type=row["license_type"],
        license_status=row["license_status"],
        license_date=row["experience_from"],
        lmfi_url=row["lmfi_url"],
        years_active=_years_since(row["experience_from"]),
        age=_years_since(row["birth_date"]),
        practice_category=row["practice_category"],
        practice_subcategory=row["practice_subcategory"],
    )

    # Per-court breakdown (exclude superseded verdicts)
    court_rows = conn.execute("""
        SELECT v.court,
               COUNT(DISTINCT CASE WHEN cl.outcome != 'unknown' THEN cl.verdict_id END) as cnt,
               SUM(CASE WHEN cl.outcome = 'win' THEN 1 ELSE 0 END) as w,
               SUM(CASE WHEN cl.outcome = 'loss' THEN 1 ELSE 0 END) as l
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE cl.lawyer_id = ? AND v.superseded_by IS NULL
        GROUP BY v.court
    """, (lawyer_id,)).fetchall()

    for cr in court_rows:
        court = cr["court"]
        profile.by_court[court] = {
            "display": COURT_DISPLAY.get(court, court),
            "count": cr["cnt"],
            "wins": cr["w"],
            "losses": cr["l"],
            "win_rate": _calc_win_rate(cr["w"], cr["cnt"]),
        }

    # Role breakdown (exclude superseded verdicts)
    role_rows = conn.execute("""
        SELECT cl.role, COUNT(*) as cnt
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE cl.lawyer_id = ? AND v.superseded_by IS NULL
        GROUP BY cl.role
    """, (lawyer_id,)).fetchall()

    for rr in role_rows:
        profile.roles[rr["role"]] = rr["cnt"]

    # All cases, sorted chronologically in Python (newest first), mixed across courts
    # Exclude superseded verdicts — only show the highest court's verdict
    case_rows = conn.execute("""
        SELECT cl.verdict_id, v.court, v.case_number, cl.role, cl.outcome, v.verdict_url
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE cl.lawyer_id = ? AND v.superseded_by IS NULL
    """, (lawyer_id,)).fetchall()

    import re

    def _case_sort_key(row):
        """Extract (year, number) from case_number for chronological sorting."""
        cn = row["case_number"]
        m = re.search(r"(\d+)[_/]+(\d{4})", cn)
        if m:
            return (int(m.group(2)), int(m.group(1)))
        return (0, 0)

    sorted_cases = sorted(case_rows, key=_case_sort_key, reverse=True)

    for cr in sorted_cases:
        case_number = re.sub(r"_+", "/", cr["case_number"])
        profile.cases.append(CaseRecord(
            verdict_id=cr["verdict_id"],
            court=cr["court"],
            court_display=COURT_DISPLAY.get(cr["court"], cr["court"]),
            case_number=case_number,
            role=cr["role"],
            outcome=cr["outcome"],
            verdict_url=cr["verdict_url"],
        ))

    # Bar association events
    event_rows = conn.execute("""
        SELECT event_date, event_type, license_type
        FROM lawyer_events
        WHERE lawyer_id = ?
        ORDER BY event_date ASC
    """, (lawyer_id,)).fetchall()

    for er in event_rows:
        profile.events.append(LawyerEvent(
            date=er["event_date"],
            event_type=er["event_type"],
            license_type=er["license_type"],
        ))

    conn.close()
    return profile


def get_lawyer_count(
    min_cases: int = 5,
    exclude_prosecutors: bool = False,
    exclude_criminal: bool = False,
    exclude_retired: bool = False,
    exclude_corporate: bool = False,
) -> int:
    """Get total number of lawyers meeting the minimum case threshold."""
    conn = _connect()
    if not conn:
        return 0

    if exclude_prosecutors or exclude_criminal:
        cl_filters = []
        if exclude_prosecutors:
            cl_filters.append("cl.role != 'prosecutor'")
        if exclude_criminal:
            cl_filters.append("NOT (v.court = 'heradsdomstolar' AND v.case_number LIKE 'S-%')")

        cl_where = " AND ".join(cl_filters)
        # Need JOIN to lawyers for retired/corporate filters
        needs_lawyer_join = exclude_retired or exclude_corporate
        retired_join = "JOIN lawyers l ON l.id = sub.lawyer_id" if needs_lawyer_join else ""
        outer_filters = []
        if exclude_retired:
            outer_filters.append("l.license_status = 'active'")
        if exclude_corporate:
            outer_filters.append("COALESCE(l.is_corporate, 0) = 0")
        retired_filter = "WHERE " + " AND ".join(outer_filters) if outer_filters else ""

        row = conn.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT cl.lawyer_id, COUNT(DISTINCT CASE WHEN cl.outcome != 'unknown' THEN cl.verdict_id END) as cnt
                FROM case_lawyers cl
                JOIN verdicts v ON v.id = cl.verdict_id
                WHERE {cl_where} AND v.superseded_by IS NULL
                GROUP BY cl.lawyer_id
                HAVING cnt >= ?
            ) sub
            {retired_join}
            {retired_filter}
        """, (min_cases,)).fetchone()
    else:
        conditions = ["case_count >= ?"]
        params = [min_cases]
        if exclude_retired:
            conditions.append("license_status = 'active'")
        if exclude_corporate:
            conditions.append("COALESCE(is_corporate, 0) = 0")
        where = " AND ".join(conditions)
        row = conn.execute(
            f"SELECT COUNT(*) FROM lawyers WHERE {where}", params
        ).fetchone()

    conn.close()
    return row[0] if row else 0
