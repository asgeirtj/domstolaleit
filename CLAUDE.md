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
- **Scraping**: Async parallel requests to all 3 courts

## Court Websites

| Court | URL | Notes |
|-------|-----|-------|
| Hæstiréttur | haestirettur.is | Supreme Court |
| Landsréttur | landsrettur.is | Court of Appeals |
| Héraðsdómstólar | heradsdomstolar.is | District Courts |

## Key Files

- `app/main.py` - FastAPI entry point
- `app/scrapers/` - Per-court scrapers
- `app/scrapers/aggregator.py` - Parallel search coordinator
- `templates/` - Jinja2 templates with htmx

## Testing

```bash
uv run pytest
```

## Notes

- All text is in Icelandic (UTF-8)
- Light mode UI
- Results link to original court pages
