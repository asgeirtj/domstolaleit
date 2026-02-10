import asyncio
from datetime import date

from app.models.court_case import CourtCase, SearchResult
from app.scrapers.haestirettur import HaestiretturScraper
from app.scrapers.heradsdomstolar import HeradsdomstolarScraper
from app.scrapers.landsrettur import LandsretturScraper

MAX_RESULTS_PER_COURT = 20


class SearchAggregator:
    """Coordinates parallel searches across all court websites."""

    def __init__(self) -> None:
        self.scrapers = [
            HaestiretturScraper(),
            LandsretturScraper(),
            HeradsdomstolarScraper(),
        ]
        self._scraper_map = {s.court_name: s for s in self.scrapers}

    async def close(self) -> None:
        await asyncio.gather(*[s.close() for s in self.scrapers])

    async def search(
        self,
        query: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[SearchResult]:
        """Execute parallel searches. Returns partial results if some courts fail."""
        tasks = [s.search(query, date_from, date_to) for s in self.scrapers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        search_results: list[SearchResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                search_results.append(
                    SearchResult(
                        court=self.scrapers[i].court_name,
                        cases=[],
                        error=f"Óvænt villa: {result}",
                    )
                )
            else:
                limited_cases = result.cases[:MAX_RESULTS_PER_COURT]
                search_results.append(
                    SearchResult(
                        court=result.court,
                        cases=limited_cases,
                        error=result.error,
                    )
                )

        # Enrich cases with snippets from verdict text
        search_results = await self._enrich_with_snippets(search_results, query)

        return search_results

    async def _enrich_with_snippets(
        self, results: list[SearchResult], query: str
    ) -> list[SearchResult]:
        """Fetch verdict text and extract snippets for all cases in parallel."""
        # Collect all cases with their scraper
        tasks = []
        case_info = []  # Track (result_idx, case_idx) for each task

        for result_idx, result in enumerate(results):
            if not result.success:
                continue
            scraper = self._scraper_map.get(result.court)
            if not scraper:
                continue
            for case_idx, case in enumerate(result.cases):
                tasks.append(scraper.enrich_with_snippets(case, query))
                case_info.append((result_idx, case_idx))

        if not tasks:
            return results

        # Fetch all snippets in parallel
        enriched_cases = await asyncio.gather(*tasks, return_exceptions=True)

        # Rebuild results with enriched cases
        new_results = []
        for result_idx, result in enumerate(results):
            if not result.success:
                new_results.append(result)
                continue

            new_cases = list(result.cases)
            for task_idx, (r_idx, c_idx) in enumerate(case_info):
                if r_idx == result_idx:
                    enriched = enriched_cases[task_idx]
                    if not isinstance(enriched, Exception):
                        new_cases[c_idx] = enriched

            new_results.append(
                SearchResult(court=result.court, cases=new_cases, error=result.error)
            )

        return new_results

    @staticmethod
    def merge_and_sort(results: list[SearchResult]) -> list[CourtCase]:
        """Merge all successful results and sort by date (newest first)."""
        all_cases: list[CourtCase] = []
        for result in results:
            if result.success:
                all_cases.extend(result.cases)

        return sorted(
            all_cases,
            key=lambda c: c.date or date.min,
            reverse=True,
        )
