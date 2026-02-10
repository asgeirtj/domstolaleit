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
  - **Local**: SQLite FTS5 index of 15,000+ downloaded verdicts

## Court Websites

| Court | URL | Notes |
|-------|-----|-------|
| Hæstiréttur | haestirettur.is | Supreme Court |
| Landsréttur | landsrettur.is | Court of Appeals |
| Héraðsdómstólar | heradsdomstolar.is | District Courts |

## Key Files

```
app/
  main.py              - FastAPI entry point
  search.py            - Local SQLite FTS5 search
  api/routes.py        - Search endpoints (/leit, /local)
  scrapers/
    base.py            - Base scraper with PDF extraction
    aggregator.py      - Parallel search coordinator
    haestirettur.py    - Supreme Court scraper
    landsrettur.py     - Court of Appeals scraper
    heradsdomstolar.py - District Courts scraper
  utils/
    icelandic.py       - BIN inflection lookup

scripts/
  download_pdfs.py     - Download PDFs from courts
  build_index.py       - Build SQLite FTS5 index
  fetch_verdict_urls.py - Populate verdict_url column

data/
  verdicts.db          - SQLite database with FTS5
  pdfs/                - Downloaded court PDFs

templates/
  base.html            - Layout template
  index.html           - Search form with mode toggle
  verdict.html         - Single verdict view
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
    verdict_url TEXT  -- Direct link to court website
);

CREATE VIRTUAL TABLE verdicts_fts USING fts5(
    case_number,
    content
);
```

## Scripts

```bash
# Download PDFs from courts (2020-present)
uv run python scripts/download_pdfs.py

# Build/rebuild search index
uv run python scripts/build_index.py

# Fetch verdict URLs for direct linking
uv run python scripts/fetch_verdict_urls.py
```

## Testing

```bash
uv run pytest
```

## Notes

- All UI text is in Icelandic (UTF-8)
- Light mode UI
- Local search expands queries for Icelandic character variants (ð/d, þ/th, æ/ae)
- BIN (Beygingarlysing islensks nutimamals) used for inflection-aware search
