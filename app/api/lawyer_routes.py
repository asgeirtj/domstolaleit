"""Routes for lawyer leaderboard and profile pages."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.lawyers import (
    EVENT_TYPE_DISPLAY,
    LICENSE_STATUS_DISPLAY,
    LICENSE_TYPE_DISPLAY,
    ROLE_DISPLAY,
    get_lawyer,
    get_lawyer_count,
    get_leaderboard,
)

router = APIRouter(prefix="/logmenn", tags=["lawyers"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("", response_class=HTMLResponse)
async def leaderboard_page(
    request: Request,
    sort: str = Query("case_count", pattern="^(case_count|wins|losses|win_rate|name|years_active|age)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    min_cases: int = Query(5, ge=1, le=500),
    q: str | None = Query(None),
    include_prosecutors: bool = Query(False),
    include_criminal: bool = Query(False),
    include_retired: bool = Query(False),
    exclude_corporate: bool = Query(False),
):
    """Render the lawyer leaderboard page."""
    # When searching by name, bypass filters so we always find the person
    has_name_search = bool(q and q.strip())
    effective_min = 1 if has_name_search else min_cases

    lawyers = get_leaderboard(
        sort_by=sort,
        sort_dir=sort_dir,
        min_cases=effective_min,
        name_query=q,
        exclude_prosecutors=False if has_name_search else not include_prosecutors,
        exclude_criminal=False if has_name_search else not include_criminal,
        exclude_retired=False if has_name_search else not include_retired,
        exclude_corporate=False if has_name_search else exclude_corporate,
    )
    total = get_lawyer_count(
        min_cases=effective_min,
        exclude_prosecutors=False if has_name_search else not include_prosecutors,
        exclude_criminal=False if has_name_search else not include_criminal,
        exclude_retired=False if has_name_search else not include_retired,
        exclude_corporate=False if has_name_search else exclude_corporate,
    )

    return templates.TemplateResponse(
        "logmenn.html",
        {
            "request": request,
            "lawyers": lawyers,
            "total": total,
            "sort": sort,
            "sort_dir": sort_dir,
            "min_cases": min_cases,
            "q": q or "",
            "include_prosecutors": include_prosecutors,
            "include_criminal": include_criminal,
            "include_retired": include_retired,
            "exclude_corporate": exclude_corporate,
        },
    )


@router.get("/leit", response_class=HTMLResponse)
async def leaderboard_search(
    request: Request,
    sort: str = Query("case_count", pattern="^(case_count|wins|losses|win_rate|name|years_active|age)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    min_cases: int = Query(5, ge=1, le=500),
    q: str | None = Query(None),
    include_prosecutors: bool = Query(False),
    include_criminal: bool = Query(False),
    include_retired: bool = Query(False),
    exclude_corporate: bool = Query(False),
):
    """htmx partial: filtered leaderboard rows."""
    # When searching by name, bypass filters so we always find the person
    has_name_search = bool(q and q.strip())
    effective_min = 1 if has_name_search else min_cases

    lawyers = get_leaderboard(
        sort_by=sort,
        sort_dir=sort_dir,
        min_cases=effective_min,
        name_query=q,
        exclude_prosecutors=False if has_name_search else not include_prosecutors,
        exclude_criminal=False if has_name_search else not include_criminal,
        exclude_retired=False if has_name_search else not include_retired,
        exclude_corporate=False if has_name_search else exclude_corporate,
    )
    total = get_lawyer_count(
        min_cases=effective_min,
        exclude_prosecutors=False if has_name_search else not include_prosecutors,
        exclude_criminal=False if has_name_search else not include_criminal,
        exclude_retired=False if has_name_search else not include_retired,
        exclude_corporate=False if has_name_search else exclude_corporate,
    )

    return templates.TemplateResponse(
        "partials/lawyer_results.html",
        {
            "request": request,
            "lawyers": lawyers,
            "total": total,
            "sort": sort,
            "sort_dir": sort_dir,
            "min_cases": min_cases,
        },
    )


@router.get("/{lawyer_id}", response_class=HTMLResponse)
async def lawyer_profile(request: Request, lawyer_id: int):
    """Render individual lawyer profile page."""
    lawyer = get_lawyer(lawyer_id)

    if not lawyer:
        return templates.TemplateResponse(
            "partials/not_found.html",
            {"request": request},
            status_code=404,
        )

    return templates.TemplateResponse(
        "logmadur.html",
        {
            "request": request,
            "lawyer": lawyer,
            "role_display": ROLE_DISPLAY,
            "license_type_display": LICENSE_TYPE_DISPLAY,
            "license_status_display": LICENSE_STATUS_DISPLAY,
            "event_type_display": EVENT_TYPE_DISPLAY,
        },
    )
