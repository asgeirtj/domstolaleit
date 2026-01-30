from datetime import date

from app.config import COURT_URLS
from app.models.court_case import CourtCase
from app.scrapers.base import BaseScraper


class LandsretturScraper(BaseScraper):
    """Scraper for Landsrettur (Court of Appeals).

    Note: Landsréttur uses 'Text' parameter instead of 'Verdict' for text search.
    """

    court_name = "landsrettur"
    base_url = COURT_URLS["landsrettur"]

    async def _do_search(
        self,
        query: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[CourtCase]:
        """Execute AJAX search request with Landsréttur-specific parameters."""
        params = {
            "pageid": self.search_page_id,
            "searchaction": "search",
            "Text": query,  # Landsréttur uses 'Text' instead of 'Verdict'
        }

        if date_from:
            params["FromDate"] = date_from.strftime("%d.%m.%Y")
        if date_to:
            params["ToDate"] = date_to.strftime("%d.%m.%Y")

        response = await self.client.get(f"{self.base_url}/", params=params)
        response.raise_for_status()

        return self._parse_results(response.text)
