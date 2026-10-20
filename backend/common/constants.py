from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COOKIE_NAME = "customer_session"
SESSION_TTL = 8 * 60 * 60
