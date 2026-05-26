import os

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

QUI_BASE_URL = env("QUI_BASE_URL").rstrip("/")
QUI_INSTANCE_ID = env("QUI_INSTANCE_ID", "1")
QUI_API_KEY = env("QUI_API_KEY", "")
HTTP_TIMEOUT = float(env("HTTP_TIMEOUT", "10"))
BACKGROUND_REFRESH_ENABLED = env("BACKGROUND_REFRESH_ENABLED", "1") == "1"
REFRESH_INTERVAL_SECONDS = max(1, int(env("REFRESH_INTERVAL_SECONDS", "3600")))
BUFFERS_PATH = env("BUFFERS_PATH", "/data/buffers.yml")
PORT = int(env("PORT", "8787"))
TRACKERS_PATH = env("TRACKERS_PATH", "/data/trackers.yml")
HOMARR_AUTH_ENABLED = env("HOMARR_AUTH_ENABLED", "0") == "1"
HOMARR_BASE_URL = env("HOMARR_BASE_URL", "http://homarr:7575").rstrip("/")
HOMARR_SESSION_ENDPOINT = env("HOMARR_SESSION_ENDPOINT", "/api/auth/session")
FRAME_ANCESTORS = env("FRAME_ANCESTORS", "'self'")
