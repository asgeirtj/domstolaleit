import re
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.config import TEMPLATES_DIR
from app.scrapers.aggregator import SearchAggregator

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def highlight_query(text: str, query: str) -> Markup:
    """Highlight search query in text with <mark> tags.

    Uses BÍN (Icelandic inflection database) to find all inflected forms
    of each word in the query.
    """
    if not query or not text:
        return Markup(text)

    from markupsafe import escape

    from app.utils.icelandic import get_all_query_forms

    escaped_text = str(escape(text))

    # Get all inflected forms for each word in the query
    word_forms = get_all_query_forms(query)

    # Create pattern matching all forms of all words
    all_forms = []
    for forms in word_forms.values():
        all_forms.extend(forms)

    if not all_forms:
        return Markup(escaped_text)

    # Sort by length (longest first) to match longer forms before shorter ones
    all_forms = sorted(set(all_forms), key=len, reverse=True)

    # Create pattern that matches any of the forms
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(f) for f in all_forms) + r")\b",
        re.IGNORECASE,
    )

    escaped_text = pattern.sub(
        r"<strong>\1</strong>",
        escaped_text,
    )

    return Markup(escaped_text)


templates.env.filters["highlight_query"] = highlight_query


def parse_date(date_str: str | None):
    """Parse date from form input."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


@router.post("/leit", response_class=HTMLResponse)
async def search(
    request: Request,
    query: str = Form(...),
    date_from: str | None = Form(None),
    date_to: str | None = Form(None),
):
    """Execute search and return HTML partial with results."""
    aggregator = SearchAggregator()
    try:
        date_from_parsed = parse_date(date_from)
        date_to_parsed = parse_date(date_to)

        results = await aggregator.search(
            query=query.strip(),
            date_from=date_from_parsed,
            date_to=date_to_parsed,
        )

        all_cases = SearchAggregator.merge_and_sort(results)

        return templates.TemplateResponse(
            "partials/results.html",
            {
                "request": request,
                "results": results,
                "all_cases": all_cases,
                "query": query,
            },
        )
    finally:
        await aggregator.close()
