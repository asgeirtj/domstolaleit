# Dómstólaleit

Unified search across Iceland's 3 court websites.

## Quick Start

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

## Architecture

- **Backend**: FastAPI + httpx + BeautifulSoup4
- **Frontend**: Vanilla HTML + htmx (no build step)
- **Search modes**:
  - **Live**: Scrapes court websites in real-time
  - **Local**: SQLite FTS5 index of 38,800+ downloaded verdicts

## Court Websites

| Court | URL | Years | Verdict count |
|-------|-----|-------|---------------|
| Hæstiréttur | haestirettur.is | 1998-present | ~12,175 |
| Landsréttur | landsrettur.is | 2018-present | ~11,658 |
| Héraðsdómstólar | heradsdomstolar.is | 2005-present | ~15,826 |

Landsréttur was established January 1, 2018 (no earlier data exists).
Hæstiréttur cases drop sharply after 2017 (~700/yr -> ~50/yr) because Landsréttur took over as the primary appeals court.

## Key Files

```
app/
  main.py              - FastAPI entry point
  search.py            - Local SQLite FTS5 search
  lawyers.py           - Lawyer leaderboard/profile queries
  api/routes.py        - Search endpoints (/leit, /local)
  api/lawyer_routes.py - Lawyer endpoints (/logmenn)
  scrapers/
    base.py            - Base scraper with PDF extraction
    aggregator.py      - Parallel search coordinator
    haestirettur.py    - Supreme Court scraper
    landsrettur.py     - Court of Appeals scraper
    heradsdomstolar.py - District Courts scraper
  utils/
    icelandic.py       - BIN inflection lookup

scripts/
  download_all.py      - Download all verdicts (offset pagination, covers full history)
  download_pdfs.py     - Download PDFs from courts (date-range, 2020+, LEGACY)
  build_index.py       - Build SQLite FTS5 index from downloaded files
  fetch_verdict_urls.py - Populate verdict_url column with court website links
  build_appeal_chains.py - Link appealed verdicts via superseded_by column
  extract_lawyers.py   - Extract lawyers + outcomes from verdicts

data/
  verdicts.db          - SQLite database with FTS5 + lawyers
  verdict_urls.json    - Cached court website URLs (used by fetch_verdict_urls.py)
  appeal_links.json    - Cached HR->LR appeal links (used by build_appeal_chains.py)
  lawyer_overrides.json - Manual overrides for lawyers not on lmfi.is/island.is
  txt/                 - Extracted text (used by build_index.py and the running app)
    haestirettur/      - .txt files (HTML-extracted, no PDFs on their site)
    landsrettur/       - .txt files (extracted from PDFs by convert_pdfs.py)
    heradsdomstolar/   - .txt files (PDFs for ~2019+, HTML-extracted for older cases)
  pdfs/                - Original PDFs (backup only, not used at runtime)
    landsrettur/       - .pdf files
    heradsdomstolar/   - .pdf files (only ~2019+, older cases have no PDF)

templates/
  base.html            - Layout template
  index.html           - Search form with mode toggle
  verdict.html         - Single verdict view
  logmenn.html         - Lawyer leaderboard
  logmadur.html        - Individual lawyer profile
  partials/            - htmx response partials
```

## Database Schema

```sql
CREATE TABLE verdicts (
    id INTEGER PRIMARY KEY,
    court TEXT NOT NULL,
    case_number TEXT NOT NULL,
    filename TEXT NOT NULL,
    text_length INTEGER,
    verdict_url TEXT,  -- Direct link to court website (populated by fetch_verdict_urls.py)
    superseded_by INTEGER REFERENCES verdicts(id)  -- Points to higher-court appeal verdict
);

CREATE VIRTUAL TABLE verdicts_fts USING fts5(
    case_number,
    content
);

CREATE TABLE lawyers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    case_count INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0
);

CREATE TABLE case_lawyers (
    id INTEGER PRIMARY KEY,
    verdict_id INTEGER NOT NULL REFERENCES verdicts(id),
    lawyer_id INTEGER NOT NULL REFERENCES lawyers(id),
    role TEXT NOT NULL,        -- plaintiff_lawyer, defendant_lawyer, prosecutor, defense_lawyer
    party_name TEXT,
    outcome TEXT,              -- win, loss, unknown
    UNIQUE(verdict_id, lawyer_id, role)
);
```

## Scripts - Execution Order

IMPORTANT: Scripts must be run in this order. Each depends on the previous.

```bash
# 1. Download verdicts from courts (use download_all.py, NOT download_pdfs.py)
#    - Saves as .txt for Hæstiréttur, .pdf for Landsréttur/Héraðsdómstólar (recent)
#    - Falls back to HTML extraction (.txt) for older Héraðsdómstólar cases without PDFs
#    - Skips already-downloaded files
#    - Uses offset pagination to cover full history (1998+)
uv run python scripts/download_all.py
uv run python scripts/download_all.py haestirettur  # single court

# 2. Build/rebuild search index
#    - DROPS AND RECREATES verdicts + verdicts_fts tables
#    - Caches extracted PDF text as .txt files for faster future rebuilds
#    - IMPORTANT: verdict_url column is included in schema but values are lost on rebuild
#    - After rebuild, you MUST re-run step 3 to restore verdict URLs
uv run python scripts/build_index.py

# 3. Fetch verdict URLs for direct linking to court websites
#    - Uses offset pagination (same as download_all.py) to get URLs
#    - Caches URLs in data/verdict_urls.json for fast re-runs
#    - Matches URLs to DB records by normalized case number
#    - MUST be run after build_index.py to populate verdict_url column
uv run python scripts/fetch_verdict_urls.py

# 3.5. Build appeal chains (links lower-court verdicts to their appeals)
#    - Stage 1: Parses upper court texts for lower court case number references
#    - Stage 2: Scrapes HR verdict pages for LR links (async, cached)
#    - Sets superseded_by on lower-court verdicts that were appealed
#    - Caches HR->LR links in data/appeal_links.json
#    - MUST be run after fetch_verdict_urls.py (Stage 2 needs verdict_url)
uv run python scripts/build_appeal_chains.py

# 4. Extract lawyers and outcomes (run after build_appeal_chains.py)
#    - Drops and recreates lawyers + case_lawyers tables
#    - Reads verdict text from verdicts_fts to find lawyer names
#    - Determines win/loss from Dómsorð section (~66% accuracy)
#    - Aggregation excludes superseded verdicts (only highest court counts)
uv run python scripts/extract_lawyers.py
```

## Full Rebuild Sequence

When rebuilding from scratch:
```bash
uv run python scripts/download_all.py       # Step 1: Download
uv run python scripts/build_index.py        # Step 2: Index (slow - pdfplumber)
uv run python scripts/fetch_verdict_urls.py # Step 3: URLs
uv run python scripts/build_appeal_chains.py # Step 3.5: Appeal chains
uv run python scripts/extract_lawyers.py    # Step 4: Lawyers
```

## Verdict URL Format

Each court has a different URL format with UUIDs:
- Hæstiréttur: `https://www.haestirettur.is/domar/_domur/?id={uuid}`
- Landsréttur: `https://www.landsrettur.is/domar-og-urskurdir/domur-urskurdur/?Id={uuid}&verdictid={uuid}`
- Héraðsdómstólar: `https://www.heradsdomstolar.is/domar/domur/?id={uuid}`

These UUIDs are NOT derivable from case numbers or filenames. They must be fetched
from the court website listing pages (fetch_verdict_urls.py). When verdict_url is NULL,
search results fall back to the local verdict view at `/domur/{id}`.

## Court Pagination APIs

All three courts use AJAX endpoints with `offset/count` pagination:

- **Hæstiréttur**: `GET /default.aspx?pageitemid={id}&offset={n}&count=10`
  - Server caps at 10 results regardless of count parameter
  - Total: ~12,178 records (offset 0 to ~12,170)

- **Landsréttur**: `GET /domar-og-urskurdir/$Verdicts/Index/?pageitemid={id}&offset={n}&count=12`

- **Héraðsdómstólar**: `GET /default.aspx?pageitemid={id}&offset={n}&count=20`
  - Server caps at 20 results per page
  - Total: ~23,800 records (offset 0 to ~23,780)
  - IMPORTANT: The website's "load more" button (class `moreVer`) has misleading
    `data-count` and `data-more` attributes. The actual AJAX call uses `offset` + `count`.
    The old `pageid`/`count`/`more` pattern was WRONG and only returned the first 20 items
    repeatedly regardless of parameters.

Empty pages can occur mid-stream (gaps). Use consecutive_empty >= 10 before stopping.

## Lawyer Extraction

- Names extracted from parenthetical references: `(Name lögmaður)`, `(Name hrl.)`, `(Name hdl.)`, `(Name saksóknari)`, etc.
- `gegn` (versus) marker splits plaintiff vs defendant side
- Outcome detection from Dómsorð section:
  - Criminal: `sýknaður/sýknað` = defense wins; `fangelsi/sekt/sakfelld` = prosecution wins
  - Civil: `stefndi er sýkn` = defendant wins; `stefnda ber að greiða` = plaintiff wins
  - Procedural orders (Úrskurðarorð without Dómsorð) = unknown outcome
- ~33% of outcomes are "unknown" (procedural orders, appellate remands, ambiguous cases)
- Name normalization: initials like "H.B." are expanded to "H. B." for deduplication

## Testing

```bash
uv run pytest
```

## Data Coverage (Feb 2026)

| Court | Indexed | On disk | Coverage |
|-------|---------|---------|----------|
| Hæstiréttur | 12,175 | 12,175 | 1998-present (complete) |
| Landsréttur | 11,632 | 11,658 | 2018-present (complete) |
| Héraðsdómstólar | 15,066 | 28,325 | 2005-present (complete) |
| **Total** | **38,873** | **52,158** | |

Note: "On disk" includes both .pdf and .txt files. "Indexed" = documents in verdicts_fts.
The gap for héraðsdómstólar (28k on disk vs 15k indexed) is because many files are
.pdf/.txt pairs for the same case (build_index.py reads .txt only, skipping duplicate PDFs).

## Data Storage

- `data/txt/` — extracted text files used by `build_index.py` to create the search index
- `data/pdfs/` — original PDFs (backup only, ~2.5GB). Not used at runtime or by the index builder
- `data/verdicts.db` — the only file the running webapp needs

PDFs and TXT files are stored in separate directory trees under `data/`. The `convert_pdfs.py`
script reads from `data/pdfs/` and writes `.txt` to `data/txt/`.

## Known Pitfalls / Lessons Learned

- **SQLite LIKE is case-sensitive for Unicode**: `LIKE '%elísabet%'` matches `Elísabet`
  for the accented í, but LIKE is NOT reliably case-insensitive for all Unicode chars.
  Test with actual Icelandic names.

- **Smart/curly quotes break FTS5**: macOS auto-converts `"` to `"` `"` (smart quotes).
  `sanitize_query()` must strip ALL Unicode quotation mark variants, not just ASCII `"` `'`.
  Characters to strip: `"` `"` `„` `‟` `'` `'` `‚` `‛` `«` `»`

- **Lawyer name search must bypass filters**: The leaderboard has default filters
  (min_cases=5, exclude prosecutors, exclude criminal cases). When searching by name,
  ALL these filters must be bypassed — otherwise lawyers whose cases are all in the
  excluded categories (e.g., only criminal defense cases) become invisible.

- **Case number format**: Héraðsdómstólar uses `S-429/2009`, `E-3426/2012` etc.
  The first number is the case sequence (can be 4+ digits), the year comes after `/`.
  Naive regex `\d{4}` will match case numbers like 3426 instead of the year 2012.

- **Older héraðsdómstólar cases have no PDFs**: Cases before ~2019 have verdict text
  embedded in the HTML page (selector: `#verdict-text`) instead of a downloadable PDF.
  `download_all.py` handles this with `html_fallback: "#verdict-text"` — tries PDF first,
  falls back to HTML extraction. Without this, ~13,000 older cases would fail to download.

- **Phrase search with Icelandic variants**: Quoted queries like `"elísabet pétursdóttir"`
  trigger FTS5 phrase matching. `build_phrase_query()` generates a Cartesian product of
  Icelandic character variants for each word, ORing all phrase combinations. Capped at 64
  combinations to avoid query explosion.

- **lmfi.is has THREE lawyer lists**: Regular (`IsCorporate=0`, "Lögmenn á lögmannsstofum"),
  in-house/corporate (`IsCorporate=1`, "Innanhúslögmenn"), and the unfiltered list
  (no `IsCorporate` param, "Allir lögmenn"). The scraper must fetch ALL THREE. Some
  lawyers (like Valgeir Pálsson) only appear on the unfiltered "Allir lögmenn" list —
  they're bar members not categorized on either filtered list by lmfi.is. If they're
  not on the law-firm list (IsCorporate=0), they're flagged as innanhúslögmenn
  (e.g. Valgeir Pálsson). Lawyers can appear on multiple lists. The `is_corporate`
  column is populated from the `IsCorporate=1` list AND from unfiltered-only lawyers
  who aren't on the law-firm list.

- **lmfi.is search**: When manually searching on lmfi.is, put the full name in the
  "Nafn" box only (ignore "Eftirnafn"). If not found, try first name only to avoid
  mismatches from middle initials or middle names.

- **Route parameters must be passed through**: When adding filter UI (checkboxes, etc.),
  verify the route actually passes the query parameter to the backend function. Hardcoding
  `False` while the UI sends the value is a silent bug — the filter appears to work in the
  UI but has zero effect on results.

- **Verdict preamble causes dual-side lawyer extraction**: Many héraðsdómstólar PDFs
  have a compressed summary header listing ALL parties before the formal `gegn`-split
  header. Without skipping the preamble, lawyers get extracted on both sides. Fixed by
  finding the second `D Ó M U R` marker before `gegn` and only parsing from there.
  ~570 false dual-assignments were eliminated. The ~200 remaining are legitimate gagnsök
  (counterclaim) cases where a lawyer truly represents different parties on each side.

- **Manual lawyer overrides** (`data/lawyer_overrides.json`): Some lawyers (e.g. María
  Thejll, Valgeir Pálsson) aren't on lmfi.is or island.is but are known to be active
  innanhúslögmenn. This JSON file is applied last in the import pipeline and overrides
  license_status, is_corporate, license_type, and lmfi_url. Add entries here when a
  lawyer's external data is missing or wrong.

- **Regex `.` doesn't match newlines in Dómsorð**: The outcome patterns like
  `stefnd\w*.{0,120}?sýkn` use `.` which doesn't match `\n` by default. PDF-extracted
  Dómsorð text often has newlines mid-sentence (e.g., "Stefndi, Haukar,\ner sýkn").
  All patterns using `.{0,N}?` must use `re.DOTALL` so `.` matches newlines too.
  Without this, outcomes were misclassified as "unknown" when entity names or other
  text caused line breaks between "stefndi" and "sýkn"/"greiði".

- **Sýslumenn misclassified as plaintiff_lawyer**: District commissioners (sýslumenn)
  act as prosecutors at héraðsdómstólar but their title "sýslumaður" wasn't in the
  prosecutor detection patterns. They got classified as `plaintiff_lawyer` instead of
  `prosecutor`. Fixed by adding `sýslumaður` to both `ROLE_PATTERN` (title detection)
  and `CRIMINAL_PLAINTIFFS` (criminal case detection via entity name "Sýslumaðurinn").

- **Innanhús detection by verdict pattern**: Lawyers with very high win rates who
  consistently represent the same entity are likely innanhús (in-house). Categories found:
  - Ríkislögmenn (state lawyers): always represent "íslenska ríkið/ríkinu"
  - Municipal lawyers: always represent Reykjavíkurborg, Kópavogsbær, etc.
  - Insurance in-house: always represent tryggingafélög
  - Bank in-house: always represent specific banks (Glitnir, Arion, etc.)
  - Sýslumenn: acting prosecutors at district level
  These are flagged via `data/lawyer_overrides.json` with `is_corporate: true`.

- **Name deduplication**: `data/name_aliases.json` maps variant spellings to canonical
  forms. E.g., "Einar K. Hallvarðsson" → "Einar Karl Hallvarðsson". Without this,
  the same person gets two DB entries with split case counts.

- **Appeal chain deduplication**: When a case is appealed, the same dispute generates
  verdicts at multiple court levels. Without deduplication, win/loss counts are inflated.
  The `superseded_by` column on `verdicts` links lower-court verdicts to their appeal.
  All lawyer queries (leaderboard, profile, count) filter with `v.superseded_by IS NULL`
  so only the highest court's outcome counts. `build_appeal_chains.py` must run after
  `fetch_verdict_urls.py` (Stage 2 needs HR verdict URLs) and before `extract_lawyers.py`
  (aggregation uses `superseded_by`). Chains: HD->LR (text match), LR->HR (text match),
  HR->LR (web scrape). ~1,273 text matches + ~200 web-scraped links expected.

- **Héraðsdómstólar pagination was broken**: The old download script used
  `pageid/count/more` params which returned the same 20 results regardless of values.
  The correct API uses `pageitemid/offset/count` (same as the other two courts).
  Discovered by intercepting the actual AJAX call from the "Birta fleiri færslur" button.

## Notes

- All UI text is in Icelandic (UTF-8)
- Light mode UI
- Local search expands queries for Icelandic character variants (ð/d, þ/th, æ/ae)
- BIN (Beygingarlysing islensks nutimamals) used for inflection-aware search
