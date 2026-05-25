import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from math import ceil
from threading import RLock

import yaml

from .config import DATABASE_PATH, LEGACY_CONFIG_DIRECTORIES
from .units import fmt_bytes, parse_bytes


_lock = RLock()
_LEGACY_MIGRATION_KEY = "legacy_yaml_migration"


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_expiry(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _expiry_from_hours(multiplier, hours):
    if float(multiplier) == 1:
        return None
    text = str(hours or "").strip()
    if not text:
        return None
    try:
        duration = float(text)
    except ValueError:
        return None
    if duration <= 0:
        return _now_utc().isoformat()
    return (_now_utc() + timedelta(hours=duration)).isoformat()


def _hours_remaining(value):
    expiry = _parse_expiry(value)
    if expiry is None:
        return ""
    seconds = (expiry - _now_utc()).total_seconds()
    if seconds <= 0:
        return ""
    return str(ceil(seconds / 3600))


def _updated_event_expiry(values, direction):
    multiplier = float(values.get(f"event_{direction}_multiplier", 1))
    hours = str(values.get(f"event_{direction}_hours_remaining", "")).strip()
    if f"original_event_{direction}_multiplier" not in values:
        return _expiry_from_hours(multiplier, hours)
    original_multiplier = float(
        values.get(f"original_event_{direction}_multiplier", multiplier)
    )
    original_hours = str(values.get(f"original_event_{direction}_hours_remaining", "")).strip()
    if multiplier == original_multiplier and hours == original_hours:
        return values.get(f"original_event_{direction}_expires_at") or None
    return _expiry_from_hours(multiplier, hours)


def _connect():
    directory = os.path.dirname(DATABASE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _database():
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "tracker"


def _legacy_file(filename):
    for directory in LEGACY_CONFIG_DIRECTORIES:
        path = os.path.join(directory, filename)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return None


def _load_legacy_yaml(path):
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as source_file:
        return yaml.safe_load(source_file) or {}


def _remove_migrated_yaml_files(paths):
    cleanup_complete = True
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            os.remove(path)
        except OSError:
            cleanup_complete = False
    return cleanup_complete


def init_database():
    with _lock, _database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trackers (
                key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                visible_dashboard INTEGER NOT NULL DEFAULT 1,
                visible_widget INTEGER NOT NULL DEFAULT 1,
                buffer_uploaded INTEGER NOT NULL DEFAULT 0,
                buffer_downloaded INTEGER NOT NULL DEFAULT 0,
                buffer_uploaded_text TEXT NOT NULL DEFAULT '0 B',
                buffer_downloaded_text TEXT NOT NULL DEFAULT '0 B',
                event_uploaded_multiplier REAL NOT NULL DEFAULT 1.0,
                event_downloaded_multiplier REAL NOT NULL DEFAULT 1.0,
                event_uploaded_expires_at TEXT,
                event_downloaded_expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tracker_domains (
                domain TEXT PRIMARY KEY,
                tracker_key TEXT NOT NULL REFERENCES trackers(key) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS torrent_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                client_type TEXT NOT NULL DEFAULT 'QUI',
                address TEXT NOT NULL,
                port INTEGER,
                api_key TEXT NOT NULL,
                instance_id TEXT NOT NULL DEFAULT '1'
            );
            """
        )
        _add_column_if_missing(
            connection, "trackers", "buffer_uploaded_text", "TEXT NOT NULL DEFAULT '0 B'"
        )
        _add_column_if_missing(
            connection, "trackers", "buffer_downloaded_text", "TEXT NOT NULL DEFAULT '0 B'"
        )
        _add_column_if_missing(
            connection, "trackers", "event_uploaded_multiplier", "REAL NOT NULL DEFAULT 1.0"
        )
        _add_column_if_missing(
            connection, "trackers", "event_downloaded_multiplier", "REAL NOT NULL DEFAULT 1.0"
        )
        _add_column_if_missing(
            connection, "trackers", "event_uploaded_expires_at", "TEXT"
        )
        _add_column_if_missing(
            connection, "trackers", "event_downloaded_expires_at", "TEXT"
        )
        _migrate_legacy_yaml_once(connection)
        _remove_example_placeholder(connection)
        _merge_duplicate_display_names(connection)


def _expire_events(connection):
    now = _now_utc().isoformat()
    connection.execute(
        """
        UPDATE trackers
        SET event_uploaded_multiplier = 1.0, event_uploaded_expires_at = NULL
        WHERE event_uploaded_expires_at IS NOT NULL
          AND event_uploaded_expires_at <= ?
        """,
        (now,),
    )
    connection.execute(
        """
        UPDATE trackers
        SET event_downloaded_multiplier = 1.0, event_downloaded_expires_at = NULL
        WHERE event_downloaded_expires_at IS NOT NULL
          AND event_downloaded_expires_at <= ?
        """,
        (now,),
    )


def _add_column_if_missing(connection, table, column, declaration):
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _migrate_legacy_yaml_once(connection):
    migration = connection.execute(
        "SELECT value FROM app_metadata WHERE key = ?", (_LEGACY_MIGRATION_KEY,)
    ).fetchone()
    if migration is not None:
        if migration["value"] == "imported_cleanup_pending":
            paths = (_legacy_file("trackers.yml"), _legacy_file("buffers.yml"))
            if _remove_migrated_yaml_files(paths):
                connection.execute(
                    "UPDATE app_metadata SET value = 'imported' WHERE key = ?",
                    (_LEGACY_MIGRATION_KEY,),
                )
        return

    trackers_path = _legacy_file("trackers.yml")
    buffers_path = _legacy_file("buffers.yml")
    if not trackers_path and not buffers_path:
        return

    existing_trackers = connection.execute("SELECT COUNT(*) FROM trackers").fetchone()[0]
    if existing_trackers:
        status = "skipped_existing_database"
    else:
        _import_legacy_yaml(connection, trackers_path, buffers_path)
        status = "imported_cleanup_pending"
    connection.execute(
        "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
        (_LEGACY_MIGRATION_KEY, status),
    )
    if status == "imported_cleanup_pending":
        connection.commit()
        if _remove_migrated_yaml_files((trackers_path, buffers_path)):
            connection.execute(
                "UPDATE app_metadata SET value = 'imported' WHERE key = ?",
                (_LEGACY_MIGRATION_KEY,),
            )


def _client_base_url(address, port):
    base = str(address).strip().rstrip("/")
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = f"http://{base}"
    if port:
        return f"{base}:{int(port)}"
    return base


def list_torrent_clients():
    init_database()
    with _lock, _database() as connection:
        rows = connection.execute(
            "SELECT * FROM torrent_clients ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "client_type": row["client_type"],
            "address": row["address"],
            "port": row["port"] or "",
            "base_url": _client_base_url(row["address"], row["port"]),
            "api_key": row["api_key"],
            "api_key_hint": (
                ("*" * (len(row["api_key"]) - 4)) + row["api_key"][-4:]
                if len(row["api_key"]) > 4
                else "*" * len(row["api_key"])
            ),
            "instance_id": row["instance_id"],
        }
        for row in rows
    ]


def add_torrent_client(name, address, port, api_key, instance_id="1"):
    init_database()
    clean_address = str(address).strip().rstrip("/")
    clean_api_key = str(api_key).strip()
    if not clean_address or not clean_api_key:
        return None
    try:
        clean_port = int(port) if str(port or "").strip() else None
    except ValueError:
        return None
    with _lock, _database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO torrent_clients (name, client_type, address, port, api_key, instance_id)
            VALUES (?, 'QUI', ?, ?, ?, ?)
            """,
            (
                str(name).strip() or "QUI",
                clean_address,
                clean_port,
                clean_api_key,
                str(instance_id).strip() or "1",
            ),
        )
        return int(cursor.lastrowid)


def update_torrent_client(client_id, name, address, port, api_key, instance_id):
    init_database()
    clean_address = str(address).strip().rstrip("/")
    if not clean_address:
        return False
    try:
        clean_port = int(port) if str(port or "").strip() else None
    except ValueError:
        return False
    with _lock, _database() as connection:
        current = connection.execute(
            "SELECT api_key FROM torrent_clients WHERE id = ?", (int(client_id),)
        ).fetchone()
        if current is None:
            return False
        clean_api_key = str(api_key).strip() or current["api_key"]
        connection.execute(
            """
            UPDATE torrent_clients
            SET name = ?, address = ?, port = ?, api_key = ?, instance_id = ?
            WHERE id = ?
            """,
            (
                str(name).strip() or "QUI",
                clean_address,
                clean_port,
                clean_api_key,
                str(instance_id).strip() or "1",
                int(client_id),
            ),
        )
    return True


def get_torrent_client(client_id):
    return next(
        (client for client in list_torrent_clients() if client["id"] == int(client_id)),
        None,
    )


def delete_torrent_client(client_id):
    init_database()
    with _lock, _database() as connection:
        connection.execute("DELETE FROM torrent_clients WHERE id = ?", (int(client_id),))


def _stored_int(stored, key, default):
    try:
        return int(stored.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _stored_float(stored, key, default):
    try:
        return float(stored.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_app_options():
    init_database()
    with _lock, _database() as connection:
        rows = connection.execute(
            """
            SELECT key, value FROM app_metadata
            WHERE key IN (
                'iframe_enabled',
                'homarr_auth_enabled',
                'homarr_base_url',
                'homarr_session_endpoint',
                'background_refresh_enabled',
                'refresh_interval_seconds',
                'http_timeout_seconds'
            )
            """
        ).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    return {
        "iframe_enabled": stored.get("iframe_enabled", "1") != "0",
        "homarr_auth_enabled": stored.get("homarr_auth_enabled", "0") == "1",
        "homarr_base_url": stored.get("homarr_base_url", ""),
        "homarr_session_endpoint": stored.get(
            "homarr_session_endpoint", "/api/auth/session"
        ),
        "background_refresh_enabled": stored.get("background_refresh_enabled", "1") != "0",
        "refresh_interval_seconds": max(60, _stored_int(stored, "refresh_interval_seconds", 3600)),
        "refresh_interval_hours": max(60, _stored_int(stored, "refresh_interval_seconds", 3600))
        / 3600,
        "http_timeout_seconds": max(1.0, _stored_float(stored, "http_timeout_seconds", 10)),
    }


def update_app_options(
    iframe_enabled,
    homarr_auth_enabled=False,
    homarr_base_url="",
    homarr_session_endpoint="/api/auth/session",
    background_refresh_enabled=True,
    refresh_interval_hours=1,
    http_timeout_seconds=10,
):
    init_database()
    try:
        refresh_interval_seconds = max(60, int(float(refresh_interval_hours) * 3600))
    except (TypeError, ValueError):
        refresh_interval_seconds = 3600
    try:
        clean_timeout = max(1.0, float(http_timeout_seconds))
    except (TypeError, ValueError):
        clean_timeout = 10.0
    settings = {
        "iframe_enabled": "1" if iframe_enabled else "0",
        "homarr_auth_enabled": "1" if homarr_auth_enabled else "0",
        "homarr_base_url": str(homarr_base_url).strip().rstrip("/"),
        "homarr_session_endpoint": str(homarr_session_endpoint).strip() or "/api/auth/session",
        "background_refresh_enabled": "1" if background_refresh_enabled else "0",
        "refresh_interval_seconds": str(refresh_interval_seconds),
        "http_timeout_seconds": str(clean_timeout),
    }
    with _lock, _database() as connection:
        for key, value in settings.items():
            connection.execute(
                """
                INSERT INTO app_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )


def _import_legacy_yaml(connection, trackers_path, buffers_path):
    tracker_data = (_load_legacy_yaml(trackers_path).get("trackers") or {})
    for key, config in tracker_data.items():
        config = config or {}
        if key == "tracker_name" and config.get("display", key) == "Name_Displayed":
            continue
        visible = config.get("visible", True) is not False
        connection.execute(
            """
            INSERT INTO trackers (key, display_name, visible_dashboard, visible_widget)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                display_name = excluded.display_name,
                visible_dashboard = excluded.visible_dashboard,
                visible_widget = excluded.visible_widget
            """,
            (key, config.get("display", key), int(visible), int(visible)),
        )
        for domain in config.get("domains") or []:
            connection.execute(
                "INSERT OR REPLACE INTO tracker_domains (domain, tracker_key) VALUES (?, ?)",
                (str(domain), key),
            )

    buffer_data = (_load_legacy_yaml(buffers_path).get("buffers") or {})
    for key, config in buffer_data.items():
        config = config or {}
        if key == "tracker_name":
            continue
        connection.execute(
            "INSERT OR IGNORE INTO trackers (key, display_name) VALUES (?, ?)",
            (key, key),
        )
        connection.execute(
            """
            UPDATE trackers
            SET buffer_uploaded = ?, buffer_downloaded = ?,
                buffer_uploaded_text = ?, buffer_downloaded_text = ?
            WHERE key = ?
            """,
            (
                parse_bytes(config.get("uploaded_add", 0)),
                parse_bytes(config.get("downloaded_add", 0)),
                str(config.get("uploaded_add", "0 B")),
                str(config.get("downloaded_add", "0 B")),
                key,
            ),
        )


def _remove_example_placeholder(connection):
    connection.execute(
        """
        DELETE FROM trackers
        WHERE key = 'tracker_name'
          AND NOT EXISTS (
              SELECT 1 FROM tracker_domains WHERE tracker_key = trackers.key
          )
        """
    )


def ensure_discovered_domains(domains):
    init_database()
    with _lock, _database() as connection:
        for domain in domains:
            existing = connection.execute(
                "SELECT tracker_key FROM tracker_domains WHERE domain = ?", (domain,)
            ).fetchone()
            if existing is not None:
                continue
            key = domain
            connection.execute(
                "INSERT OR IGNORE INTO trackers (key, display_name) VALUES (?, ?)",
                (key, domain),
            )
            connection.execute(
                "INSERT INTO tracker_domains (domain, tracker_key) VALUES (?, ?)",
                (domain, key),
            )


def load_tracker_configuration(domains=()):
    ensure_discovered_domains(domains)
    with _lock, _database() as connection:
        _expire_events(connection)
        tracker_rows = connection.execute("SELECT * FROM trackers").fetchall()
        domain_rows = connection.execute("SELECT domain, tracker_key FROM tracker_domains").fetchall()
    trackers = {
        row["key"]: {
            "display": row["display_name"],
            "visible_dashboard": bool(row["visible_dashboard"]),
            "visible_widget": bool(row["visible_widget"]),
            "uploaded_add": int(row["buffer_uploaded"]),
            "downloaded_add": int(row["buffer_downloaded"]),
            "event_uploaded_multiplier": float(row["event_uploaded_multiplier"]),
            "event_downloaded_multiplier": float(row["event_downloaded_multiplier"]),
            "event_uploaded_expires_at": row["event_uploaded_expires_at"],
            "event_downloaded_expires_at": row["event_downloaded_expires_at"],
        }
        for row in tracker_rows
    }
    domain_to_key = {row["domain"]: row["tracker_key"] for row in domain_rows}
    return domain_to_key, trackers


def list_trackers():
    init_database()
    with _lock, _database() as connection:
        _expire_events(connection)
        trackers = connection.execute(
            """
            SELECT *
            FROM trackers AS t
            WHERE EXISTS (
                SELECT 1 FROM tracker_domains AS d WHERE d.tracker_key = t.key
            )
            ORDER BY display_name COLLATE NOCASE
            """
        ).fetchall()
        domains = connection.execute(
            "SELECT tracker_key, domain FROM tracker_domains ORDER BY domain COLLATE NOCASE"
        ).fetchall()
    grouped_domains = {}
    for row in domains:
        grouped_domains.setdefault(row["tracker_key"], []).append(row["domain"])
    return [
        {
            "key": row["key"],
            "display_name": row["display_name"],
            "visible_dashboard": bool(row["visible_dashboard"]),
            "visible_widget": bool(row["visible_widget"]),
            "buffer_uploaded": int(row["buffer_uploaded"]),
            "buffer_downloaded": int(row["buffer_downloaded"]),
            "buffer_uploaded_text": row["buffer_uploaded_text"],
            "buffer_downloaded_text": row["buffer_downloaded_text"],
            "event_uploaded_multiplier": float(row["event_uploaded_multiplier"]),
            "event_downloaded_multiplier": float(row["event_downloaded_multiplier"]),
            "event_uploaded_expires_at": row["event_uploaded_expires_at"] or "",
            "event_downloaded_expires_at": row["event_downloaded_expires_at"] or "",
            "event_uploaded_hours_remaining": _hours_remaining(row["event_uploaded_expires_at"]),
            "event_downloaded_hours_remaining": _hours_remaining(row["event_downloaded_expires_at"]),
            "domains": grouped_domains.get(row["key"], []),
        }
        for row in trackers
    ]


def list_domains():
    init_database()
    with _lock, _database() as connection:
        rows = connection.execute(
            """
            SELECT d.domain, d.tracker_key, t.display_name
            FROM tracker_domains AS d
            JOIN trackers AS t ON t.key = d.tracker_key
            ORDER BY d.domain COLLATE NOCASE
            """
        ).fetchall()
    return [
        {
            "domain": row["domain"],
            "tracker_key": row["tracker_key"],
            "display_name": row["display_name"],
        }
        for row in rows
    ]


def _create_tracker_in_connection(connection, display_name):
    key_base = _slugify(display_name)
    key = key_base
    suffix = 2
    while connection.execute("SELECT 1 FROM trackers WHERE key = ?", (key,)).fetchone():
        key = f"{key_base}-{suffix}"
        suffix += 1
    connection.execute(
        "INSERT INTO trackers (key, display_name) VALUES (?, ?)",
        (key, str(display_name).strip() or key),
    )
    return key


def create_tracker(display_name):
    init_database()
    with _lock, _database() as connection:
        return _create_tracker_in_connection(connection, display_name)


def _resolve_target_key(connection, target):
    value = str(target).strip()
    if not value:
        return None
    row = connection.execute("SELECT key FROM trackers WHERE key = ?", (value,)).fetchone()
    if row is not None:
        return row["key"]
    row = connection.execute(
        "SELECT key FROM trackers WHERE display_name = ? COLLATE NOCASE ORDER BY key LIMIT 1",
        (value,),
    ).fetchone()
    if row is not None:
        return row["key"]
    return _create_tracker_in_connection(connection, value)


def _move_empty_tracker_buffers(connection, source_key, target_key):
    if source_key == target_key:
        return
    if connection.execute(
        "SELECT 1 FROM tracker_domains WHERE tracker_key = ? LIMIT 1", (source_key,)
    ).fetchone():
        return
    source = connection.execute(
        "SELECT buffer_uploaded, buffer_downloaded FROM trackers WHERE key = ?",
        (source_key,),
    ).fetchone()
    target = connection.execute(
        "SELECT buffer_uploaded, buffer_downloaded FROM trackers WHERE key = ?",
        (target_key,),
    ).fetchone()
    if source is None or target is None:
        return
    uploaded = int(target["buffer_uploaded"]) + int(source["buffer_uploaded"])
    downloaded = int(target["buffer_downloaded"]) + int(source["buffer_downloaded"])
    connection.execute(
        """
        UPDATE trackers
        SET buffer_uploaded = ?, buffer_downloaded = ?,
            buffer_uploaded_text = ?, buffer_downloaded_text = ?
        WHERE key = ?
        """,
        (uploaded, downloaded, fmt_bytes(uploaded), fmt_bytes(downloaded), target_key),
    )
    connection.execute("DELETE FROM trackers WHERE key = ?", (source_key,))


def _merge_duplicate_display_names(connection):
    duplicates = connection.execute(
        """
        SELECT display_name
        FROM trackers AS t
        WHERE EXISTS (
            SELECT 1 FROM tracker_domains WHERE tracker_key = t.key
        )
        GROUP BY display_name COLLATE NOCASE
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for duplicate in duplicates:
        keys = connection.execute(
            """
            SELECT key
            FROM trackers AS t
            WHERE display_name = ? COLLATE NOCASE
              AND EXISTS (
                  SELECT 1 FROM tracker_domains WHERE tracker_key = t.key
              )
            ORDER BY key
            """,
            (duplicate["display_name"],),
        ).fetchall()
        target_key = keys[0]["key"]
        for source in keys[1:]:
            source_key = source["key"]
            connection.execute(
                "UPDATE tracker_domains SET tracker_key = ? WHERE tracker_key = ?",
                (target_key, source_key),
            )
            _move_empty_tracker_buffers(connection, source_key, target_key)


def group_domains(domains, display_name):
    init_database()
    selected_domains = sorted({str(domain).strip() for domain in domains if str(domain).strip()})
    if not selected_domains or not str(display_name).strip():
        return None
    with _lock, _database() as connection:
        source_rows = connection.execute(
            f"""
            SELECT tracker_key, COUNT(*) AS selected_count
            FROM tracker_domains
            WHERE domain IN ({",".join("?" for _ in selected_domains)})
            GROUP BY tracker_key
            """,
            selected_domains,
        ).fetchall()
        target_name = str(display_name).strip()
        existing_target = connection.execute(
            "SELECT key FROM trackers WHERE display_name = ? COLLATE NOCASE ORDER BY key LIMIT 1",
            (target_name,),
        ).fetchone()
        if existing_target is None and len(source_rows) == 1:
            source_key = source_rows[0]["tracker_key"]
            domain_count = connection.execute(
                "SELECT COUNT(*) AS count FROM tracker_domains WHERE tracker_key = ?",
                (source_key,),
            ).fetchone()["count"]
            if int(domain_count) == len(selected_domains):
                connection.execute(
                    "UPDATE trackers SET display_name = ? WHERE key = ?",
                    (target_name, source_key),
                )
                return source_key
        tracker_key = _resolve_target_key(connection, display_name)
        previous_keys = set()
        for domain in selected_domains:
            previous = connection.execute(
                "SELECT tracker_key FROM tracker_domains WHERE domain = ?",
                (domain,),
            ).fetchone()
            if previous is not None:
                previous_keys.add(previous["tracker_key"])
            connection.execute(
                "UPDATE tracker_domains SET tracker_key = ? WHERE domain = ?",
                (tracker_key, domain),
            )
        for key in previous_keys:
            _move_empty_tracker_buffers(connection, key, tracker_key)
            connection.execute(
                """
                DELETE FROM trackers
                WHERE key = ?
                  AND buffer_uploaded = 0
                  AND buffer_downloaded = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM tracker_domains WHERE tracker_key = trackers.key
                  )
                """,
                (key,),
            )
    return tracker_key


def unlink_domains(domains):
    init_database()
    selected_domains = sorted({str(domain).strip() for domain in domains if str(domain).strip()})
    with _lock, _database() as connection:
        previous_keys = set()
        replacement_keys = {}
        for domain in selected_domains:
            previous = connection.execute(
                "SELECT tracker_key FROM tracker_domains WHERE domain = ?",
                (domain,),
            ).fetchone()
            if previous is None:
                continue
            previous_keys.add(previous["tracker_key"])
            replacement_keys.setdefault(previous["tracker_key"], domain)
            connection.execute(
                "INSERT OR IGNORE INTO trackers (key, display_name) VALUES (?, ?)",
                (domain, domain),
            )
            connection.execute(
                "UPDATE tracker_domains SET tracker_key = ? WHERE domain = ?",
                (domain, domain),
            )
        for key in previous_keys:
            _move_empty_tracker_buffers(connection, key, replacement_keys[key])
            connection.execute(
                """
                DELETE FROM trackers
                WHERE key = ?
                  AND buffer_uploaded = 0
                  AND buffer_downloaded = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM tracker_domains WHERE tracker_key = trackers.key
                  )
                """,
                (key,),
            )


def update_trackers(updates):
    init_database()
    with _lock, _database() as connection:
        for key, values in updates.items():
            connection.execute(
                """
                UPDATE trackers
                SET display_name = ?, visible_dashboard = ?, visible_widget = ?,
                    buffer_uploaded = ?, buffer_downloaded = ?,
                    buffer_uploaded_text = ?, buffer_downloaded_text = ?,
                    event_uploaded_multiplier = ?, event_downloaded_multiplier = ?,
                    event_uploaded_expires_at = ?, event_downloaded_expires_at = ?
                WHERE key = ?
                """,
                (
                    str(values["display_name"]).strip() or key,
                    int(bool(values["visible_dashboard"])),
                    int(bool(values["visible_widget"])),
                    parse_bytes(values["uploaded_add"]),
                    parse_bytes(values["downloaded_add"]),
                    str(values["uploaded_add"]).strip() or "0 B",
                    str(values["downloaded_add"]).strip() or "0 B",
                    float(values.get("event_uploaded_multiplier", 1)),
                    float(values.get("event_downloaded_multiplier", 1)),
                    _updated_event_expiry(values, "uploaded"),
                    _updated_event_expiry(values, "downloaded"),
                    key,
                )
            )
        _merge_duplicate_display_names(connection)
