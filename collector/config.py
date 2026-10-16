"""Settings, all read from environment variables (GitHub Actions secrets)."""
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")   # secret — never put in the app
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Model that reads timetables. Haiku is cheap and good at tables; switch to
# claude-sonnet-5-5 if some timetables come back wrong.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")

USER_AGENT = os.environ.get(
    "COLLECTOR_USER_AGENT",
    "MyMosqueProBot/1.0 (+https://mymosquepro.com/bot; jamaat timetable collector)",
)
REQUEST_TIMEOUT = 30          # seconds per request
PER_HOST_DELAY = 3.0          # seconds between requests to the same website
MAX_BYTES = 15 * 1024 * 1024  # skip anything bigger (huge PDFs)
MAX_AI_CALLS = int(os.environ.get("MAX_AI_CALLS", "400"))  # safety cap per daily run
MAX_AI_CALLS_LEARN = int(os.environ.get("MAX_AI_CALLS_LEARN", "500"))  # safety cap per learning run
WORKERS = int(os.environ.get("COLLECTOR_WORKERS", "8"))   # websites checked at the same time
DISABLE_AFTER_FAILS = 14      # stop checking a source after two weeks of errors
TIMEZONE = "Europe/London"
