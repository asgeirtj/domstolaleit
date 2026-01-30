import io
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
import pdfplumber

from app.config import REQUEST_TIMEOUT, USER_AGENT
from app.models.court_case import CourtCase, CourtName, SearchResult

# Number of characters to show around each search term match
SNIPPET_CONTEXT = 80
MAX_SNIPPETS_PER_CASE = 3

# Valid short Icelandic words (1-2 chars) that should NOT be joined to following text
VALID_SHORT_WORDS = frozenset({
    # 1-char words
    'á', 'í', 'ó', 'ú', 'a', 'i', 'o', 'u',
    # 2-char words (common)
    'að', 'af', 'án', 'ef', 'ég', 'ei', 'en', 'er', 'fy', 'hé', 'já', 'né', 'nú',
    'og', 'sé', 'um', 'úr', 'þá', 'þó', 'þú', 'ör', 'nr', 'gr', 'sl', 'kr',
})


def fix_broken_words(text: str) -> str:
    """Fix words broken by PDF text extraction.

    Court websites store PDF-extracted text with justification spaces that
    break words like 'við' into 'v ið'. This rejoins fragments when the
    first part isn't a valid standalone Icelandic word.
    """
    # Pattern 1: Word boundary + 1-2 char fragment + space + 1-4 char fragment
    # Handles: "v ið" -> "við"
    pattern1 = re.compile(
        r'\b([a-záðéíóúýþæö]{1,2})\s+([a-záðéíóúýþæö]{1,4})\b',
        re.IGNORECASE
    )

    # Pattern 2: Mid-word break - letters + 1-2 char + space + 2-4 char + letters
    # Handles: "fjársk ipti" -> "fjárskipti"
    pattern2 = re.compile(
        r'([a-záðéíóúýþæö]{2,})([a-záðéíóúýþæö]{1,2})\s+([a-záðéíóúýþæö]{2,4})([a-záðéíóúýþæö]*)',
        re.IGNORECASE
    )

    def should_join_boundary(match: re.Match) -> str:
        frag1, frag2 = match.group(1), match.group(2)
        if frag1.lower() not in VALID_SHORT_WORDS:
            return frag1 + frag2
        return match.group(0)

    def should_join_midword(match: re.Match) -> str:
        prefix, frag1, frag2, suffix = match.groups()
        # Always join mid-word breaks (they're never valid word boundaries)
        return prefix + frag1 + frag2 + suffix

    # Apply mid-word fix first, then boundary fix
    text = pattern2.sub(should_join_midword, text)
    text = pattern1.sub(should_join_boundary, text)
    return text


class BaseScraper(ABC):
    """Abstract base class for court website scrapers."""

    court_name: CourtName
    base_url: str
    search_page_id: str = "deb3ce16-7d66-11e5-80c6-005056bc6a40"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "text/html, */*",
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def search(
        self,
        query: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> SearchResult:
        """Execute search and return results. Catches exceptions for graceful degradation."""
        try:
            cases = await self._do_search(query, date_from, date_to)
            return SearchResult(court=self.court_name, cases=cases)
        except httpx.TimeoutException:
            return SearchResult(
                court=self.court_name,
                cases=[],
                error="Tenging vid vefinn rann ut a tima",
            )
        except httpx.HTTPError as e:
            return SearchResult(
                court=self.court_name,
                cases=[],
                error=f"Villa vid ad na i gogn: {e}",
            )
        except Exception as e:
            return SearchResult(
                court=self.court_name,
                cases=[],
                error=f"Ovaent villa: {e}",
            )

    async def _do_search(
        self,
        query: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[CourtCase]:
        """Execute AJAX search request."""
        params = {
            "pageid": self.search_page_id,
            "searchaction": "search",
            "Verdict": query,
        }

        if date_from:
            params["FromDate"] = date_from.strftime("%d.%m.%Y")
        if date_to:
            params["ToDate"] = date_to.strftime("%d.%m.%Y")

        response = await self.client.get(f"{self.base_url}/", params=params)
        response.raise_for_status()

        return self._parse_results(response.text)

    def _parse_results(self, html: str) -> list[CourtCase]:
        """Parse search results HTML from AJAX response."""
        soup = BeautifulSoup(html, "lxml")
        cases: list[CourtCase] = []

        for result_div in soup.select("div.result"):
            case = self._parse_single_result(result_div)
            if case:
                cases.append(case)

        return cases

    def _parse_single_result(self, result_div) -> CourtCase | None:
        """Parse a single result div into a CourtCase."""
        case_link = result_div.select_one("a.casenumber")
        if not case_link:
            return None

        href = case_link.get("href", "")
        url = urljoin(self.base_url, href)

        case_number_elem = case_link.select_one("h2")
        case_number = case_number_elem.get_text(strip=True) if case_number_elem else ""

        title_elem = result_div.select_one("p > a")
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
                clean_line = " ".join(line.split())  # Collapse whitespace in each line
                if clean_line:
                    lines.append(clean_line)
            title = "\n".join(lines)

        case_date = None
        time_elem = result_div.select_one("time.media-date")
        if time_elem:
            datetime_str = time_elem.get("datetime", "")
            case_date = self._parse_datetime(datetime_str)

        summary = ""
        abstract_elem = result_div.select_one("div.case-abstract")
        if abstract_elem:
            summary = abstract_elem.get_text(strip=True)

        # Extract keywords from <small> element
        keywords = ""
        small_elem = result_div.select_one("small")
        if small_elem:
            keywords = small_elem.get_text(separator=" ", strip=True)
            # Clean up extra whitespace
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

    def _parse_datetime(self, datetime_str: str) -> date | None:
        """Parse datetime from ISO format or other formats."""
        if not datetime_str:
            return None

        # Clean up the datetime string
        dt_str = datetime_str.strip()

        # Handle ISO format with timezone: 2026-01-29T00:00:00.0000000+00:00
        # Python's %f only handles 6 decimal places, so truncate if needed
        if "T" in dt_str and "." in dt_str:
            # Split at decimal point and truncate microseconds to 6 digits
            base, rest = dt_str.split(".", 1)
            # Find where timezone starts (+ or - after decimal)
            tz_pos = -1
            for i, c in enumerate(rest):
                if c in "+-" and i > 0:
                    tz_pos = i
                    break
            if tz_pos > 0:
                micros = rest[:min(6, tz_pos)]
                tz = rest[tz_pos:]
                dt_str = f"{base}.{micros.ljust(6, '0')}{tz}"
            elif len(rest) > 6:
                dt_str = f"{base}.{rest[:6]}"

        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y",
        ):
            try:
                dt = datetime.strptime(dt_str, fmt)
                return dt.date()
            except ValueError:
                continue

        return None

    def _parse_icelandic_date(self, date_str: str) -> date | None:
        """Parse common Icelandic date formats."""
        if not date_str:
            return None
        date_str = date_str.strip()
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    async def fetch_verdict_text(self, url: str) -> str:
        """Fetch the full verdict text, preferring PDF download over HTML scraping.

        Court pages often use PDF.js to render verdicts, which breaks words across
        HTML elements. Downloading the source PDF gives much cleaner text.
        """
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            # Try to find and download the PDF (much cleaner than HTML text layer)
            pdf_text = await self._extract_pdf_text(soup, url)
            if pdf_text:
                return pdf_text

            # Fall back to HTML extraction with word-fixing heuristics
            verdict_elem = soup.select_one("#verdict-text") or soup.select_one(".verdict__body")
            if verdict_elem:
                text = verdict_elem.get_text()
                text = " ".join(text.split())
                return fix_broken_words(text)

            content_elem = soup.select_one(".session-content")
            if content_elem:
                text = content_elem.get_text()
                text = " ".join(text.split())
                return fix_broken_words(text)

            return ""
        except Exception:
            return ""

    async def _extract_pdf_text(self, soup: BeautifulSoup, page_url: str) -> str | None:
        """Find PDF download link and extract text from the PDF."""
        # Look for PDF download link (common patterns on court sites)
        pdf_link = soup.select_one('a.pdflink[href*="Download"]')
        if not pdf_link:
            pdf_link = soup.select_one('a[href*=".pdf"]')
        if not pdf_link:
            pdf_link = soup.select_one('a[href*="Download"][href*="docId"]')

        if not pdf_link:
            return None

        href = pdf_link.get("href", "")
        if not href:
            return None

        pdf_url = urljoin(page_url, href)

        try:
            pdf_response = await self.client.get(pdf_url)
            pdf_response.raise_for_status()

            # Check content type
            content_type = pdf_response.headers.get("content-type", "")
            if "pdf" not in content_type.lower() and not href.endswith(".pdf"):
                return None

            # Extract text from PDF using pdfplumber (better text extraction)
            pdf_file = io.BytesIO(pdf_response.content)
            with pdfplumber.open(pdf_file) as pdf:
                text_parts = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

                if text_parts:
                    return " ".join(" ".join(text_parts).split())

        except Exception:
            pass

        return None

    def extract_snippets(self, text: str, query: str) -> list[str]:
        """Extract snippets from text where query appears, with context.

        Uses BÍN (Icelandic inflection database) to find all inflected forms.
        """
        if not text or not query:
            return []

        from app.utils.icelandic import get_all_query_forms

        snippets = []
        used_positions = set()  # Track positions to avoid overlapping snippets

        # Get all inflected forms for each word in the query
        word_forms = get_all_query_forms(query)

        # Collect all forms
        all_forms = []
        for forms in word_forms.values():
            all_forms.extend(forms)

        if not all_forms:
            return []

        # Sort by length (longest first) to match longer forms before shorter
        all_forms = sorted(set(all_forms), key=len, reverse=True)

        # Create pattern that matches any form
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(f) for f in all_forms) + r")\b",
            re.IGNORECASE,
        )

        for match in pattern.finditer(text):
            if len(snippets) >= MAX_SNIPPETS_PER_CASE:
                break

            pos = match.start()

            # Skip if this position overlaps with an existing snippet
            if any(abs(pos - p) < SNIPPET_CONTEXT for p in used_positions):
                continue

            match_end = match.end()

            # Extract snippet with context
            snippet_start = max(0, pos - SNIPPET_CONTEXT)
            snippet_end = min(len(text), match_end + SNIPPET_CONTEXT)

            # Extend to word boundaries
            if snippet_start > 0:
                space_pos = text.rfind(" ", 0, snippet_start)
                if space_pos > snippet_start - 30:
                    snippet_start = space_pos + 1

            if snippet_end < len(text):
                space_pos = text.find(" ", snippet_end)
                if space_pos != -1 and space_pos < snippet_end + 30:
                    snippet_end = space_pos

            snippet = text[snippet_start:snippet_end]

            # Add ellipsis
            if snippet_start > 0:
                snippet = "..." + snippet
            if snippet_end < len(text):
                snippet = snippet + "..."

            snippets.append(snippet)
            used_positions.add(pos)

        return snippets

    async def enrich_with_snippets(self, case: CourtCase, query: str) -> CourtCase:
        """Fetch verdict and add snippets to case."""
        verdict_text = await self.fetch_verdict_text(case.url)
        snippets = self.extract_snippets(verdict_text, query)
        return replace(case, snippets=tuple(snippets))
