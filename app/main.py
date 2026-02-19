import logging
import os
import time

import arel
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as search_router
from app.api.lawyer_routes import router as lawyer_router
from app.config import STATIC_DIR, TEMPLATES_DIR

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# Enable httpx request logging (outgoing requests to courts)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)

logger = logging.getLogger("domstolaleit")

app = FastAPI(title="Dómstólaleit", description="Unified court search for Iceland")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests with timing."""
    start = time.perf_counter()
    logger.info(f">>> {request.method} {request.url.path}?{request.query_params}")

    response = await call_next(request)

    duration = (time.perf_counter() - start) * 1000
    logger.info(f"<<< {response.status_code} in {duration:.0f}ms")
    return response


# Hot reload for development
if os.environ.get("AREL_HOT_RELOAD"):
    hot_reload = arel.HotReload(paths=[arel.Path(".")])
    app.add_websocket_route("/hot-reload", hot_reload, name="hot-reload")
    app.add_event_handler("startup", hot_reload.startup)
    app.add_event_handler("shutdown", hot_reload.shutdown)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(search_router)
app.include_router(lawyer_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main search page."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "hot_reload": os.environ.get("AREL_HOT_RELOAD"),
        },
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
