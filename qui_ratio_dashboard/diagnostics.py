import os
from datetime import datetime, timezone

from .config import LOG_DIRECTORY


LOG_PATH = os.path.join(LOG_DIRECTORY, "log.txt")
MAX_LOG_BYTES = 1024 * 1024
MAX_ROTATED_LOGS = 9


def _ensure_directory():
    os.makedirs(LOG_DIRECTORY, exist_ok=True)


def _rotate_logs():
    if not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) < MAX_LOG_BYTES:
        return
    oldest_path = os.path.join(LOG_DIRECTORY, f"log.{MAX_ROTATED_LOGS}.txt")
    if os.path.exists(oldest_path):
        os.remove(oldest_path)
    for index in range(MAX_ROTATED_LOGS - 1, 0, -1):
        source = os.path.join(LOG_DIRECTORY, f"log.{index}.txt")
        destination = os.path.join(LOG_DIRECTORY, f"log.{index + 1}.txt")
        if os.path.exists(source):
            os.replace(source, destination)
    os.replace(LOG_PATH, os.path.join(LOG_DIRECTORY, "log.1.txt"))


def log_event(message, **fields):
    try:
        _ensure_directory()
        _rotate_logs()
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{timestamp} {message}"
        if details:
            line = f"{line} {details}"
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except OSError:
        return


def short_hash(value):
    text = str(value or "")
    return f"{text[:8]}..." if len(text) > 8 else text
