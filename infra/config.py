"""
infra/config.py — reads ALL credentials from environment variables.
No secrets stored here. Set vars in Railway dashboard or .env locally.
"""
import os

# --- Database ---
# Railway injects DATABASE_URL automatically for Postgres service
_DATABASE_URL = os.environ.get('DATABASE_URL', '')

if _DATABASE_URL:
    # Parse DATABASE_URL -> DB_CONFIG dict for psycopg2
    import urllib.parse
    _u = urllib.parse.urlparse(_DATABASE_URL)
    DB_CONFIG = {
        'host': _u.hostname,
        'port': _u.port or 5432,
        'dbname': _u.path.lstrip('/'),
        'user': _u.username,
        'password': _u.password,
    }
else:
    # Local fallback
    DB_CONFIG = {
        'host': os.environ.get('DB_HOST', 'localhost'),
        'port': int(os.environ.get('DB_PORT', 5432)),
        'dbname': os.environ.get('DB_NAME', 'marketing_dronor'),
        'user': os.environ.get('DB_USER', 'postgres'),
        'password': os.environ.get('DB_PASSWORD', ''),
    }

# --- API Keys ---
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
TWITTER_BEARER_TOKEN = os.environ.get('TWITTER_BEARER_TOKEN', '')
TWITTERAPI_IO_KEY = os.environ.get('TWITTERAPI_IO_KEY', '')
