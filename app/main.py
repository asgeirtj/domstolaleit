import os

import arel
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as search_router
from app.config import STATIC_DIR, TEMPLATES_DIR

app = FastAPI(title="Dómstólaleit", description="Unified court search for Iceland")

# Hot reload for development
if os.environ.get("AREL_HOT_RELOAD"):
    hot_reload = arel.HotReload(paths=[arel.Path(".")])
    app.add_websocket_route("/hot-reload", hot_reload, name="hot-reload")
    app.add_event_handler("startup", hot_reload.startup)
    app.add_event_handler("shutdown", hot_reload.shutdown)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(search_router)


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
