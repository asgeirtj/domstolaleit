from app.config import COURT_URLS
from app.scrapers.base import BaseScraper


class HaestiretturScraper(BaseScraper):
    """Scraper for Haestirettur (Supreme Court)."""

    court_name = "haestirettur"
    base_url = COURT_URLS["haestirettur"]
