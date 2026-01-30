from pathlib import Path

APP_DIR = Path(__file__).parent
PROJECT_DIR = APP_DIR.parent
TEMPLATES_DIR = PROJECT_DIR / "templates"
STATIC_DIR = PROJECT_DIR / "static"

COURT_URLS = {
    "haestirettur": "https://www.haestirettur.is",
    "landsrettur": "https://www.landsrettur.is",
    "heradsdomstolar": "https://www.heradsdomstolar.is",
}

REQUEST_TIMEOUT = 15.0
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
