from dataclasses import dataclass
from datetime import date
from typing import Literal

CourtName = Literal["haestirettur", "landsrettur", "heradsdomstolar"]

COURT_DISPLAY_NAMES: dict[CourtName, str] = {
    "haestirettur": "Hæstiréttur",
    "landsrettur": "Landsréttur",
    "heradsdomstolar": "Héraðsdómstólar",
}


@dataclass(frozen=True)
class CourtCase:
    court: CourtName
    case_number: str
    title: str
    date: date | None
    url: str
    summary: str = ""
    keywords: str = ""
    snippets: list[str] = ()  # Snippets from verdict text with search term

    @property
    def court_display_name(self) -> str:
        return COURT_DISPLAY_NAMES[self.court]


@dataclass(frozen=True)
class SearchResult:
    court: CourtName
    cases: list[CourtCase]
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def court_display_name(self) -> str:
        return COURT_DISPLAY_NAMES[self.court]
