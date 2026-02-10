# Domstolaleit

Unified search across Iceland's three court websites: Haestirettur (Supreme Court), Landsrettur (Court of Appeals), and Heradsdomstolar (District Courts).

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

## Getting Started

```bash
# Install dependencies
uv sync

# Start the development server
uv run uvicorn app.main:app --reload --port 8000
```

The app will be available at [http://localhost:8000](http://localhost:8000).

## Search Modes

**Live search** scrapes court websites in real-time and returns matching verdicts.

**Local search** queries a pre-built SQLite FTS5 index of 15,000+ downloaded verdicts for faster, offline results. To set up local search:

```bash
# 1. Download verdict PDFs from court websites (2020-present)
uv run python scripts/download_pdfs.py

# 2. Build the full-text search index
uv run python scripts/build_index.py

# 3. Fetch direct links to verdicts on court websites
uv run python scripts/fetch_verdict_urls.py
```

## Running Tests

```bash
uv sync --dev
uv run pytest
```
