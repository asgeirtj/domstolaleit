from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.config import COURT_URLS
from app.models.court_case import CourtCase
from app.scrapers.base import BaseScraper


class HeradsdomstolarScraper(BaseScraper):
    """Scraper for Heradsdomstolar (District Courts).

    Note: Heradsdomstolar uses a different HTML structure than the other courts.
    Results are in <a class="sentence"> instead of <a class="casenumber">.
    """

    court_name = "heradsdomstolar"
    base_url = COURT_URLS["heradsdomstolar"]

    def _parse_results(self, html: str) -> list[CourtCase]:
        """Parse search results HTML - override for heradsdomstolar's different structure."""
        soup = BeautifulSoup(html, "lxml")
        cases: list[CourtCase] = []

        for result_div in soup.select("div.result"):
            case = self._parse_single_result(result_div)
            if case:
                cases.append(case)

        return cases

    def _parse_single_result(self, result_div) -> CourtCase | None:
        """Parse a single result div - heradsdomstolar uses <a class='sentence'>."""
        sentence_link = result_div.select_one("a.sentence")
        if not sentence_link:
            return None

        href = sentence_link.get("href", "")
        url = urljoin(self.base_url, href)

        case_number_elem = sentence_link.select_one("h2")
        case_number = case_number_elem.get_text(strip=True) if case_number_elem else ""

        title_elem = sentence_link.select_one("p.ellipsis")
        title = ""
        if title_elem:
            # Replace <br> tags with newlines to preserve structure
            for br in title_elem.find_all("br"):
                br.replace_with("\n")
            # Get text WITHOUT stripping to preserve spaces between elements
            raw_text = title_elem.get_text()
            # Clean up each line individually (remove indentation/extra spaces)
            # while preserving the newlines from <br> tags
            lines = []
            for line in raw_text.split("\n"):
                clean_line = " ".join(line.split())
                if clean_line:
                    lines.append(clean_line)
            title = "\n".join(lines)

        case_date = None
        time_elem = sentence_link.select_one("time.media-date")
        if time_elem:
            datetime_str = time_elem.get("datetime", "")
            case_date = self._parse_heradsdomstolar_date(datetime_str)

        summary = ""
        modal = result_div.select_one("div.case-abstract")
        if modal:
            summary = modal.get_text(strip=True)

        # Extract keywords if present (héraðsdómstólar usually doesn't have them)
        keywords = ""
        small_elem = result_div.select_one("small")
        if small_elem:
            keywords = small_elem.get_text(separator=" ", strip=True)
            keywords = " ".join(keywords.split())

        return CourtCase(
            court=self.court_name,
            case_number=case_number,
            title=title,
            date=case_date,
            url=url,
            summary=summary,
            keywords=keywords,
        )

    def _parse_heradsdomstolar_date(self, datetime_str: str) -> date | None:
        """Parse heradsdomstolar's date format: '22.1.2026 00:00:00'."""
        if not datetime_str:
            return None

        for fmt in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(datetime_str.strip(), fmt)
                return dt.date()
            except ValueError:
                continue

        return None
