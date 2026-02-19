"""Build a static HTML site for the lawyer leaderboard and profiles.

Reads from SQLite, outputs to docs/ for GitHub Pages.
All filtering/sorting/searching is client-side JavaScript.

Usage:
    uv run python scripts/build_static.py
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "verdicts.db"
STATIC_DIR = Path(__file__).parent.parent / "static"
OUTPUT_DIR = Path(__file__).parent.parent / "docs"

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
    if not iso_date:
        return None
    try:
        d = date.fromisoformat(iso_date)
        today = date.today()
        days = (today - d).days
        return round(days / 365.25, 1)
    except ValueError:
        return None


def _win_rate(wins: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(wins / total * 100, 1)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _h(text: str | None) -> str:
    """HTML-escape a string."""
    return escape(text or "")


# ---------------------------------------------------------------------------
# Data export
# ---------------------------------------------------------------------------


def export_leaderboard_data(conn: sqlite3.Connection) -> list[dict]:
    """Export all lawyers with adjustment stats for client-side filtering."""
    # Base stats (all cases)
    rows = conn.execute("""
        SELECT id, name, case_count, wins, losses,
               license_type, license_status,
               COALESCE(experience_from, license_date) as experience_from,
               birth_date, lmfi_url, practice_category
        FROM lawyers
        WHERE case_count >= 1
        ORDER BY case_count DESC
    """).fetchall()

    lawyer_ids = [r["id"] for r in rows]
    if not lawyer_ids:
        return []

    # Prosecutor-only stats (cases where role = prosecutor)
    pros_stats: dict[int, dict] = {}
    pros_rows = conn.execute("""
        SELECT cl.lawyer_id,
               COUNT(DISTINCT CASE WHEN cl.outcome != 'unknown' THEN cl.verdict_id END) as cnt,
               SUM(CASE WHEN cl.outcome = 'win' THEN 1 ELSE 0 END) as w,
               SUM(CASE WHEN cl.outcome = 'loss' THEN 1 ELSE 0 END) as l
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE cl.role = 'prosecutor' AND v.superseded_by IS NULL
        GROUP BY cl.lawyer_id
    """).fetchall()
    for pr in pros_rows:
        pros_stats[pr["lawyer_id"]] = {"cases": pr["cnt"], "wins": pr["w"], "losses": pr["l"]}

    # Criminal-only stats (S- cases from heradsdomstolar)
    crim_stats: dict[int, dict] = {}
    crim_rows = conn.execute("""
        SELECT cl.lawyer_id,
               COUNT(DISTINCT CASE WHEN cl.outcome != 'unknown' THEN cl.verdict_id END) as cnt,
               SUM(CASE WHEN cl.outcome = 'win' THEN 1 ELSE 0 END) as w,
               SUM(CASE WHEN cl.outcome = 'loss' THEN 1 ELSE 0 END) as l
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE v.court = 'heradsdomstolar' AND v.case_number LIKE 'S-%'
              AND v.superseded_by IS NULL
        GROUP BY cl.lawyer_id
    """).fetchall()
    for cr in crim_rows:
        crim_stats[cr["lawyer_id"]] = {"cases": cr["cnt"], "wins": cr["w"], "losses": cr["l"]}

    lawyers = []
    for r in rows:
        lid = r["id"]
        ps = pros_stats.get(lid, {"cases": 0, "wins": 0, "losses": 0})
        cs = crim_stats.get(lid, {"cases": 0, "wins": 0, "losses": 0})
        experience_from = r["experience_from"]
        years_active = _years_since(experience_from or "2001-01-31")
        years_active_approx = experience_from is None
        age = _years_since(r["birth_date"])

        lawyers.append({
            "id": lid,
            "name": r["name"],
            "case_count": r["case_count"],
            "wins": r["wins"],
            "losses": r["losses"],
            "license_type": r["license_type"],
            "license_status": r["license_status"] or "none",
            "years_active": years_active,
            "years_active_approx": years_active_approx,
            "age": int(age) if age is not None else None,
            "lmfi_url": r["lmfi_url"],
            "practice_category": r["practice_category"],
            # Adjustment stats for filtering
            "pros_cases": ps["cases"],
            "pros_wins": ps["wins"],
            "pros_losses": ps["losses"],
            "crim_cases": cs["cases"],
            "crim_wins": cs["wins"],
            "crim_losses": cs["losses"],
        })

    return lawyers


def export_lawyer_profile(conn: sqlite3.Connection, lawyer_id: int) -> dict | None:
    """Export full profile data for a single lawyer."""
    row = conn.execute(
        """SELECT id, name, case_count, wins, losses,
                  license_type, license_status,
                  COALESCE(experience_from, license_date) as experience_from,
                  lmfi_url, birth_date, practice_category, practice_subcategory
           FROM lawyers WHERE id = ?""",
        (lawyer_id,),
    ).fetchone()
    if not row:
        return None

    profile = {
        "id": row["id"],
        "name": row["name"],
        "case_count": row["case_count"],
        "wins": row["wins"],
        "losses": row["losses"],
        "win_rate": _win_rate(row["wins"], row["case_count"]),
        "license_type": row["license_type"],
        "license_status": row["license_status"],
        "license_date": row["experience_from"],
        "lmfi_url": row["lmfi_url"],
        "years_active": _years_since(row["experience_from"]),
        "age": _years_since(row["birth_date"]),
        "practice_category": row["practice_category"],
        "practice_subcategory": row["practice_subcategory"],
    }

    # Per-court breakdown
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

    by_court = {}
    for cr in court_rows:
        court = cr["court"]
        by_court[court] = {
            "display": COURT_DISPLAY.get(court, court),
            "count": cr["cnt"],
            "wins": cr["w"],
            "losses": cr["l"],
            "win_rate": _win_rate(cr["w"], cr["cnt"]),
        }
    profile["by_court"] = by_court

    # Role breakdown
    role_rows = conn.execute("""
        SELECT cl.role, COUNT(*) as cnt
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE cl.lawyer_id = ? AND v.superseded_by IS NULL
        GROUP BY cl.role
    """, (lawyer_id,)).fetchall()
    profile["roles"] = {rr["role"]: rr["cnt"] for rr in role_rows}

    # Cases (exclude superseded verdicts)
    case_rows = conn.execute("""
        SELECT cl.verdict_id, v.court, v.case_number, cl.role, cl.outcome, v.verdict_url
        FROM case_lawyers cl
        JOIN verdicts v ON v.id = cl.verdict_id
        WHERE cl.lawyer_id = ? AND v.superseded_by IS NULL
    """, (lawyer_id,)).fetchall()

    def _case_sort_key(r):
        cn = r["case_number"]
        m = re.search(r"(\d+)[_/]+(\d{4})", cn)
        if m:
            return (int(m.group(2)), int(m.group(1)))
        return (0, 0)

    sorted_cases = sorted(case_rows, key=_case_sort_key, reverse=True)
    cases = []
    for cr in sorted_cases:
        case_number = re.sub(r"_+", "/", cr["case_number"])
        cases.append({
            "verdict_id": cr["verdict_id"],
            "court": cr["court"],
            "court_display": COURT_DISPLAY.get(cr["court"], cr["court"]),
            "case_number": case_number,
            "role": cr["role"],
            "outcome": cr["outcome"],
            "verdict_url": cr["verdict_url"],
        })
    profile["cases"] = cases

    # Events
    event_rows = conn.execute(
        "SELECT event_date, event_type, license_type FROM lawyer_events WHERE lawyer_id = ? ORDER BY event_date ASC",
        (lawyer_id,),
    ).fetchall()
    profile["events"] = [
        {"date": er["event_date"], "event_type": er["event_type"], "license_type": er["license_type"]}
        for er in event_rows
    ]

    return profile


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def render_base(title: str, content: str, css_path: str = "css/style.css", favicon_path: str = "favicon.jpeg") -> str:
    """Render the base HTML layout."""
    return f"""<!DOCTYPE html>
<html lang="is">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_h(title)}</title>
    <link rel="icon" type="image/jpeg" href="{favicon_path}">
    <link rel="stylesheet" href="{css_path}">
</head>
<body>
    <header>
        <h1><a href="index.html">Lögmannaleit</a></h1>
        <p class="subtitle">Leit í dómum íslenskra dómstóla</p>
        <nav class="main-nav">
            <a href="index.html">Lögmenn</a>
        </nav>
    </header>
    <main>
        {content}
    </main>
    <footer>
        <p>Gögn úr <a href="https://www.haestirettur.is" target="_blank">Hæstarétti</a>,
           <a href="https://www.landsrettur.is" target="_blank">Landsrétti</a> og
           <a href="https://www.heradsdomstolar.is" target="_blank">Héraðsdómstólum</a></p>
    </footer>
</body>
</html>"""


def render_leaderboard(lawyers_json: str) -> str:
    """Render the leaderboard page content with embedded data + client-side JS."""
    return f"""<div class="search-container compact-filters">
    <div class="filters-top-row">
        <div class="filters-left">
            <span class="page-description"><span id="lawyer-count">0</span> lögmenn með <span id="min-cases-display">5</span>+ mál</span>
            <input type="text" id="lawyer-search" placeholder="Leita að lögmanni..." autocomplete="off">
        </div>
        <div class="filter-buttons" id="min-cases-buttons">
            <button type="button" class="filter-btn" data-value="1">1+</button>
            <button type="button" class="filter-btn active" data-value="5">5+</button>
            <button type="button" class="filter-btn" data-value="10">10+</button>
            <button type="button" class="filter-btn" data-value="20">20+</button>
            <button type="button" class="filter-btn" data-value="30">30+</button>
            <button type="button" class="filter-btn" data-value="40">40+</button>
            <button type="button" class="filter-btn" data-value="50">50+</button>
            <button type="button" class="filter-btn" data-value="100">100+</button>
        </div>
    </div>
    <div class="filters-bottom-row">
        <span class="filter-label">Innihalda:</span>
        <label class="checkbox-filter">
            <input type="checkbox" id="include-prosecutors" checked>
            <span>Saksóknara</span>
        </label>
        <label class="checkbox-filter">
            <input type="checkbox" id="include-criminal" checked>
            <span>Sakamál (S-)</span>
        </label>
        <label class="checkbox-filter">
            <input type="checkbox" id="include-retired" checked>
            <span>Óvirka lögmenn</span>
        </label>
    </div>
</div>

<div class="leaderboard-table-wrapper">
    <table class="leaderboard-table">
        <thead>
            <tr>
                <th class="col-rank">#</th>
                <th class="col-name sortable" data-sort="name">Nafn</th>
                <th class="col-cases sortable sorted" data-sort="case_count">Mal<span class="sort-arrow" id="arrow-case_count">&#9660;</span></th>
                <th class="col-wins sortable" data-sort="wins">S</th>
                <th class="col-losses sortable" data-sort="losses">T</th>
                <th class="col-winrate sortable" data-sort="win_rate" colspan="2">Sigur%</th>
                <th class="col-info">Réttindi</th>
                <th class="col-exp sortable" data-sort="years_active">Reynsla</th>
                <th class="col-age sortable" data-sort="age">Aldur</th>
            </tr>
        </thead>
        <tbody id="lawyer-table-body">
        </tbody>
    </table>
</div>

<script>
var LAWYERS = {lawyers_json};

var state = {{
    sortBy: 'case_count',
    sortDir: 'desc',
    minCases: 5,
    query: '',
    includeProsecutors: true,
    includeCriminal: true,
    includeRetired: true
}};

function getEffective(lawyer) {{
    var cc = lawyer.case_count;
    var w = lawyer.wins;
    var l = lawyer.losses;
    if (!state.includeProsecutors) {{
        cc -= lawyer.pros_cases;
        w -= lawyer.pros_wins;
        l -= lawyer.pros_losses;
    }}
    if (!state.includeCriminal) {{
        cc -= lawyer.crim_cases;
        w -= lawyer.crim_wins;
        l -= lawyer.crim_losses;
    }}
    if (cc < 0) cc = 0;
    if (w < 0) w = 0;
    if (l < 0) l = 0;
    var wr = cc > 0 ? Math.round(w / cc * 100) : 0;
    return {{ case_count: cc, wins: w, losses: l, win_rate: wr }};
}}

function renderTable() {{
    var filtered = [];
    var q = state.query.toLowerCase();

    for (var i = 0; i < LAWYERS.length; i++) {{
        var lawyer = LAWYERS[i];
        if (!state.includeRetired && lawyer.license_status !== 'active') continue;
        if (q && lawyer.name.toLowerCase().indexOf(q) === -1) continue;

        var eff = getEffective(lawyer);
        if (eff.case_count < state.minCases) continue;

        filtered.push({{ lawyer: lawyer, eff: eff }});
    }}

    // Sort
    var sortBy = state.sortBy;
    var mult = state.sortDir === 'desc' ? -1 : 1;

    filtered.sort(function(a, b) {{
        var va, vb;
        if (sortBy === 'name') {{
            va = a.lawyer.name.toLowerCase();
            vb = b.lawyer.name.toLowerCase();
            return va < vb ? -1 * mult : va > vb ? mult : 0;
        }} else if (sortBy === 'win_rate') {{
            va = a.eff.win_rate;
            vb = b.eff.win_rate;
        }} else if (sortBy === 'years_active') {{
            va = a.lawyer.years_active || 0;
            vb = b.lawyer.years_active || 0;
        }} else if (sortBy === 'age') {{
            va = a.lawyer.age || 0;
            vb = b.lawyer.age || 0;
        }} else {{
            va = a.eff[sortBy] || 0;
            vb = b.eff[sortBy] || 0;
        }}
        return (va - vb) * mult;
    }});

    document.getElementById('lawyer-count').textContent = filtered.length;
    document.getElementById('min-cases-display').textContent = state.minCases;

    var html = '';
    for (var i = 0; i < filtered.length; i++) {{
        var item = filtered[i];
        var lawyer = item.lawyer;
        var eff = item.eff;
        var inactive = lawyer.license_status !== 'active' && lawyer.license_status !== 'none';
        var rowClass = inactive ? ' class="row-inactive"' : '';
        var licenseBadge = '';
        if (lawyer.license_type && lawyer.license_status === 'active') {{
            licenseBadge = '<span class="license-badge license-active">' + lawyer.license_type.toUpperCase() + '</span>';
        }}
        var lmfiLink = '';
        if (lawyer.lmfi_url) {{
            lmfiLink = ' <a href="' + lawyer.lmfi_url + '" target="_blank" rel="noopener" class="lmfi-link" title="LMFI">L</a>';
        }}
        var expText = '';
        if (lawyer.license_status === 'active' && lawyer.years_active != null) {{
            expText = lawyer.years_active.toFixed(1);
            if (lawyer.years_active_approx) expText += ' +';
        }}
        var ageText = '';
        if (lawyer.license_status === 'active' && lawyer.age != null) {{
            ageText = lawyer.age;
        }}

        html += '<tr' + rowClass + '>' +
            '<td class="col-rank">' + (i + 1) + '</td>' +
            '<td class="col-name"><a href="logmenn/' + lawyer.id + '/index.html">' + lawyer.name + '</a>' + lmfiLink + '</td>' +
            '<td class="col-cases">' + eff.case_count + '</td>' +
            '<td class="col-wins">' + eff.wins + '</td>' +
            '<td class="col-losses">' + eff.losses + '</td>' +
            '<td class="col-winrate-num">' + eff.win_rate + '%</td>' +
            '<td class="col-winrate-bar"><div class="win-bar"><div class="win-bar-fill" style="width:' + eff.win_rate + '%"></div></div></td>' +
            '<td class="col-info">' + licenseBadge + '</td>' +
            '<td class="col-exp">' + expText + '</td>' +
            '<td class="col-age">' + ageText + '</td>' +
            '</tr>';
    }}

    document.getElementById('lawyer-table-body').innerHTML = html;

    // Update sort arrows
    document.querySelectorAll('.leaderboard-table th.sortable').forEach(function(th) {{
        var field = th.dataset.sort;
        th.classList.toggle('sorted', field === state.sortBy);
        var arrow = document.getElementById('arrow-' + field);
        if (arrow) arrow.remove();
    }});
    var activeTh = document.querySelector('th[data-sort="' + state.sortBy + '"]');
    if (activeTh) {{
        var arrowSpan = document.createElement('span');
        arrowSpan.className = 'sort-arrow';
        arrowSpan.id = 'arrow-' + state.sortBy;
        arrowSpan.innerHTML = state.sortDir === 'asc' ? '&#9650;' : '&#9660;';
        activeTh.appendChild(arrowSpan);
    }}
}}

// Filter buttons
document.querySelectorAll('#min-cases-buttons .filter-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
        document.querySelector('#min-cases-buttons .filter-btn.active').classList.remove('active');
        this.classList.add('active');
        state.minCases = parseInt(this.dataset.value);
        renderTable();
    }});
}});

// Checkboxes
document.getElementById('include-prosecutors').addEventListener('change', function() {{
    state.includeProsecutors = this.checked;
    renderTable();
}});
document.getElementById('include-criminal').addEventListener('change', function() {{
    state.includeCriminal = this.checked;
    renderTable();
}});
document.getElementById('include-retired').addEventListener('change', function() {{
    state.includeRetired = this.checked;
    renderTable();
}});

// Search
var searchTimer;
document.getElementById('lawyer-search').addEventListener('input', function() {{
    clearTimeout(searchTimer);
    var self = this;
    searchTimer = setTimeout(function() {{
        state.query = self.value;
        renderTable();
    }}, 200);
}});

// Sort headers
document.querySelectorAll('th.sortable').forEach(function(th) {{
    th.addEventListener('click', function() {{
        var field = this.dataset.sort;
        if (state.sortBy === field) {{
            state.sortDir = state.sortDir === 'desc' ? 'asc' : 'desc';
        }} else {{
            state.sortBy = field;
            state.sortDir = field === 'name' ? 'asc' : 'desc';
        }}
        renderTable();
    }});
}});

// Initial render
renderTable();
</script>"""


def render_profile(profile: dict) -> str:
    """Render a single lawyer profile page."""
    lawyer = profile
    parts = []

    parts.append('<article class="lawyer-profile">')
    parts.append('    <a href="../../index.html" class="back-link">Til baka</a>')

    # Header
    parts.append('    <div class="lawyer-header">')
    parts.append('        <h2>')
    parts.append(f'            {_h(lawyer["name"])}')
    if lawyer.get("license_status"):
        status_display = LICENSE_STATUS_DISPLAY.get(lawyer["license_status"], "")
        parts.append(f'            <span class="license-badge license-{_h(lawyer["license_status"])}">')
        parts.append(f'                {_h(status_display)}')
        parts.append('            </span>')
    parts.append('        </h2>')

    # Meta line
    has_meta = lawyer.get("license_type") or lawyer.get("years_active") is not None or lawyer.get("age") is not None
    if has_meta:
        parts.append('        <div class="lawyer-meta">')
        if lawyer.get("license_type"):
            lt_display = LICENSE_TYPE_DISPLAY.get(lawyer["license_type"], lawyer["license_type"])
            parts.append(f'            <span class="meta-item">{_h(lt_display)}</span>')
        if lawyer.get("years_active") is not None:
            parts.append(f'            <span class="meta-item">{lawyer["years_active"]} ára reynsla</span>')
        if lawyer.get("age") is not None:
            parts.append(f'            <span class="meta-item">{int(lawyer["age"])} ára</span>')
        if lawyer.get("practice_category"):
            pc = _h(lawyer["practice_category"])
            psc = lawyer.get("practice_subcategory")
            if psc and psc != lawyer["practice_category"]:
                pc += f' — {_h(psc)}'
            parts.append(f'            <span class="meta-item meta-practice">{pc}</span>')
        if lawyer.get("lmfi_url"):
            parts.append(f'            <a href="{_h(lawyer["lmfi_url"])}" target="_blank" rel="noopener" class="meta-item meta-link">LMFI</a>')
        parts.append('        </div>')

    # Stats row
    wr = lawyer["win_rate"]
    parts.append('        <div class="lawyer-stats-row">')
    parts.append(f'            <div class="stat-box"><span class="stat-value">{lawyer["case_count"]}</span><span class="stat-label">Mal</span></div>')
    parts.append(f'            <div class="stat-box stat-win"><span class="stat-value">{lawyer["wins"]}</span><span class="stat-label">Sigrar</span></div>')
    parts.append(f'            <div class="stat-box stat-loss"><span class="stat-value">{lawyer["losses"]}</span><span class="stat-label">Tap</span></div>')
    parts.append(f'            <div class="stat-box"><span class="stat-value">{wr:.0f}%</span><span class="stat-label">Sigurhlutfall</span></div>')
    parts.append('        </div>')
    parts.append(f'        <div class="win-bar win-bar-large"><div class="win-bar-fill" style="width: {wr}%"></div></div>')
    parts.append('    </div>')

    # Events / career timeline
    events = lawyer.get("events", [])
    has_timeline = events or lawyer.get("license_date") or lawyer.get("license_type")
    if has_timeline:
        parts.append('    <div class="lawyer-section">')
        parts.append('        <h3>Ferill</h3>')
        parts.append('        <div class="event-timeline">')
        if events:
            for ev in events:
                ev_date = ev["date"][:10] if ev.get("date") else "?"
                ev_type = EVENT_TYPE_DISPLAY.get(ev["event_type"], ev["event_type"])
                parts.append('            <div class="event-item">')
                parts.append(f'                <span class="event-date">{_h(ev_date)}</span>')
                parts.append(f'                <span class="event-type">{_h(ev_type)}</span>')
                if ev.get("license_type"):
                    lt = LICENSE_TYPE_DISPLAY.get(ev["license_type"], ev["license_type"])
                    parts.append(f'                <span class="event-license">{_h(lt)}</span>')
                parts.append('            </div>')
        elif lawyer.get("license_date"):
            parts.append('            <div class="event-item">')
            parts.append(f'                <span class="event-date">{_h(lawyer["license_date"][:10])}</span>')
            parts.append('                <span class="event-type">Réttindi veitt</span>')
            if lawyer.get("license_type"):
                lt = LICENSE_TYPE_DISPLAY.get(lawyer["license_type"], lawyer["license_type"])
                parts.append(f'                <span class="event-license">{_h(lt)}</span>')
            parts.append('            </div>')
        if lawyer.get("license_status") == "active":
            parts.append('            <div class="event-item event-current">')
            parts.append('                <span class="event-date">Nú</span>')
            parts.append(f'                <span class="event-type">{_h(LICENSE_STATUS_DISPLAY.get("active", ""))}</span>')
            if lawyer.get("license_type"):
                lt = LICENSE_TYPE_DISPLAY.get(lawyer["license_type"], lawyer["license_type"])
                parts.append(f'                <span class="event-license">{_h(lt)}</span>')
            parts.append('            </div>')
        parts.append('        </div>')
        parts.append('    </div>')

    # Role breakdown
    roles = lawyer.get("roles", {})
    if roles:
        parts.append('    <div class="lawyer-section">')
        parts.append('        <h3>Hlutverk</h3>')
        parts.append('        <div class="role-tags">')
        for role, count in roles.items():
            rd = ROLE_DISPLAY.get(role, role)
            parts.append(f'            <span class="role-tag role-{_h(role)}">')
            parts.append(f'                {_h(rd)}')
            parts.append(f'                <span class="role-count">{count}</span>')
            parts.append('            </span>')
        parts.append('        </div>')
        parts.append('    </div>')

    # Court breakdown
    by_court = lawyer.get("by_court", {})
    if by_court:
        parts.append('    <div class="lawyer-section">')
        parts.append('        <h3>Eftir dómstóli</h3>')
        parts.append('        <div class="court-breakdown">')
        for court, stats in by_court.items():
            cwr = stats["win_rate"]
            parts.append(f'            <div class="court-stat-card {_h(court)}">')
            parts.append(f'                <div class="court-stat-name">{_h(stats["display"])}</div>')
            parts.append(f'                <div class="court-stat-numbers">')
            parts.append(f'                    <span>{stats["count"]} mal</span>')
            parts.append(f'                    <span class="court-stat-wl">{stats["wins"]}S / {stats["losses"]}T</span>')
            parts.append(f'                    <span>({cwr:.0f}%)</span>')
            parts.append(f'                </div>')
            parts.append(f'                <div class="win-bar win-bar-small"><div class="win-bar-fill" style="width: {cwr}%"></div></div>')
            parts.append('            </div>')
        parts.append('        </div>')
        parts.append('    </div>')

    # Cases
    cases = lawyer.get("cases", [])
    parts.append('    <div class="lawyer-section">')
    parts.append('        <div class="cases-header">')
    parts.append(f'            <h3>Mal (<span id="visible-count">{len(cases)}</span>)</h3>')
    parts.append('            <label class="checkbox-filter">')
    parts.append('                <input type="checkbox" id="hide-criminal-cases">')
    parts.append('                <span>Fela sakamál (S-)</span>')
    parts.append('            </label>')
    parts.append('        </div>')
    parts.append('        <div class="cases-list" id="cases-list">')
    for case in cases:
        rd = ROLE_DISPLAY.get(case["role"], case["role"])
        outcome_class = f'outcome-{case["outcome"]}' if case.get("outcome") else "outcome-unknown"
        outcome_text = {"win": "Sigur", "loss": "Tap"}.get(case.get("outcome", ""), "Óvíst")

        parts.append(f'            <div class="case-card {_h(case["court"])}" data-court="{_h(case["court"])}" data-case-number="{_h(case["case_number"])}">')
        parts.append('                <div class="case-header">')
        parts.append(f'                    <span class="court-tag {_h(case["court"])}">{_h(case["court_display"])}</span>')
        parts.append('                    <div class="case-meta">')
        parts.append(f'                        <span class="role-tag role-{_h(case["role"])} role-tag-small">{_h(rd)}</span>')
        parts.append(f'                        <span class="outcome-badge {outcome_class}">{_h(outcome_text)}</span>')
        parts.append('                    </div>')
        parts.append('                </div>')
        if case.get("verdict_url"):
            parts.append(f'                <h3><a href="{_h(case["verdict_url"])}" target="_blank" rel="noopener">{_h(case["case_number"])}</a></h3>')
        else:
            parts.append(f'                <h3>{_h(case["case_number"])}</h3>')
        parts.append('            </div>')
    parts.append('        </div>')
    parts.append('    </div>')

    parts.append('</article>')

    # Hide criminal cases JS
    parts.append("""<script>
document.getElementById('hide-criminal-cases').addEventListener('change', function() {
    var hide = this.checked;
    var cards = document.querySelectorAll('#cases-list .case-card');
    var visible = 0;
    cards.forEach(function(card) {
        var caseNum = card.dataset.caseNumber;
        var court = card.dataset.court;
        var isCriminal = court === 'heradsdomstolar' && caseNum.startsWith('S-');
        if (hide && isCriminal) {
            card.style.display = 'none';
        } else {
            card.style.display = '';
            visible++;
        }
    });
    document.getElementById('visible-count').textContent = visible;
});
</script>""")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------


def build():
    print("Building static site...")

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    conn = _connect()

    # Clean output directory
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)

    # Create directory structure
    logmenn_dir = OUTPUT_DIR / "logmenn"
    logmenn_dir.mkdir(parents=True, exist_ok=True)
    css_dir = OUTPUT_DIR / "css"
    css_dir.mkdir(parents=True, exist_ok=True)

    # Copy static assets
    shutil.copy2(STATIC_DIR / "css" / "style.css", css_dir / "style.css")
    favicon_src = STATIC_DIR / "favicon.jpeg"
    if favicon_src.exists():
        shutil.copy2(favicon_src, OUTPUT_DIR / "favicon.jpeg")
    print("  Copied static assets")

    # Export leaderboard data
    lawyers = export_leaderboard_data(conn)
    print(f"  Exported {len(lawyers)} lawyers")

    # Render leaderboard page
    lawyers_json = json.dumps(lawyers, ensure_ascii=False, separators=(",", ":"))
    leaderboard_content = render_leaderboard(lawyers_json)
    leaderboard_html = render_base("Lögmannaleit", leaderboard_content)
    (OUTPUT_DIR / "index.html").write_text(leaderboard_html, encoding="utf-8")
    print("  Generated leaderboard index.html")

    # Render profile pages
    profile_count = 0
    for lawyer in lawyers:
        lid = lawyer["id"]
        profile = export_lawyer_profile(conn, lid)
        if not profile:
            continue

        profile_dir = logmenn_dir / str(lid)
        profile_dir.mkdir(parents=True, exist_ok=True)

        profile_content = render_profile(profile)
        profile_html = render_base(
            f'{profile["name"]} - Logmannaleit',
            profile_content,
            css_path="../../css/style.css",
            favicon_path="../../favicon.jpeg",
        )
        (profile_dir / "index.html").write_text(profile_html, encoding="utf-8")
        profile_count += 1

    print(f"  Generated {profile_count} profile pages")

    conn.close()

    # Print summary
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.rglob("*") if f.is_file())
    print(f"\nDone! Output: {OUTPUT_DIR}")
    print(f"Total size: {total_size / 1024 / 1024:.1f} MB")
    print(f"\nTo preview: python3 -m http.server -d {OUTPUT_DIR} 9000")


if __name__ == "__main__":
    build()
