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
_AUTO_PARENT_DOMAIN_GROUPING_KEY = "automatic_parent_domain_grouping_v1"


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


def _updated_buffer_values(values, direction):
    target = str(values.get(f"{direction}_target", "")).strip()
    if target:
        raw_value = int(values.get(f"raw_{direction}", 0))
        buffer_value = parse_bytes(target) - raw_value
        return buffer_value, fmt_bytes(buffer_value)
    text = str(values.get(f"{direction}_add", "0 B")).strip() or "0 B"
    return parse_bytes(text), text


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
                minimum_ratio REAL NOT NULL DEFAULT 1.0,
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
                instance_id TEXT NOT NULL DEFAULT '1',
                initial_sync_mode TEXT NOT NULL DEFAULT 'preserve',
                sync_pending INTEGER NOT NULL DEFAULT 0,
                username TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                rpc_path TEXT NOT NULL DEFAULT '',
                last_connection_success INTEGER,
                last_connection_at TEXT,
                last_connection_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS torrent_rules (
                client_id INTEGER NOT NULL REFERENCES torrent_clients(id) ON DELETE CASCADE,
                torrent_hash TEXT NOT NULL,
                tracker TEXT NOT NULL DEFAULT '',
                upload_multiplier REAL NOT NULL DEFAULT 1.0,
                download_multiplier REAL NOT NULL DEFAULT 1.0,
                label TEXT NOT NULL DEFAULT 'Normal',
                source TEXT NOT NULL DEFAULT 'detected',
                created_at TEXT NOT NULL,
                lookup_attempts INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (client_id, torrent_hash)
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
            connection, "trackers", "minimum_ratio", "REAL NOT NULL DEFAULT 1.0"
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
        _add_column_if_missing(
            connection, "torrent_clients", "initial_sync_mode", "TEXT NOT NULL DEFAULT 'preserve'"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "sync_pending", "INTEGER NOT NULL DEFAULT 0"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "username", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "password", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "rpc_path", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "last_connection_success", "INTEGER"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "last_connection_at", "TEXT"
        )
        _add_column_if_missing(
            connection, "torrent_clients", "last_connection_error", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "torrent_rules", "lookup_attempts", "INTEGER NOT NULL DEFAULT 1"
        )
        connection.execute(
            "DELETE FROM app_metadata WHERE key = 'background_refresh_enabled'"
        )
        _migrate_legacy_yaml_once(connection)
        _remove_example_placeholder(connection)
        _merge_duplicate_display_names(connection)
        _auto_group_existing_parent_domains_once(connection)


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


def _initial_sync_mode(value, default="preserve"):
    mode = str(value or "").strip().lower()
    return mode if mode in {"replace", "add", "preserve"} else default


def _client_type(value):
    client_type = str(value or "QUI").strip().upper()
    return (
        client_type
        if client_type in {"QUI", "QBITTORRENT", "TRANSMISSION", "DELUGE", "RTORRENT"}
        else "QUI"
    )


def _secret_hint(secret):
    secret = str(secret or "")
    return (
        ("*" * (len(secret) - 4)) + secret[-4:]
        if len(secret) > 4
        else "*" * len(secret)
    )


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
            "client_type": _client_type(row["client_type"]),
            "address": row["address"],
            "port": row["port"] or "",
            "base_url": _client_base_url(row["address"], row["port"]),
            "api_key": row["api_key"],
            "api_key_hint": _secret_hint(row["api_key"]),
            "username": row["username"],
            "password": row["password"],
            "password_hint": _secret_hint(row["password"]),
            "rpc_path": row["rpc_path"],
            "instance_id": row["instance_id"],
            "initial_sync_mode": _initial_sync_mode(row["initial_sync_mode"]),
            "sync_pending": bool(row["sync_pending"]),
            "last_connection_success": (
                None
                if row["last_connection_success"] is None
                else bool(row["last_connection_success"])
            ),
            "last_connection_at": row["last_connection_at"] or "",
            "last_connection_error": row["last_connection_error"] or "",
        }
        for row in rows
    ]


def add_torrent_client(
    name,
    address,
    port,
    api_key="",
    instance_id="1",
    initial_sync_mode="preserve",
    client_type="QUI",
    username="",
    password="",
    rpc_path="",
):
    init_database()
    clean_type = _client_type(client_type)
    clean_address = str(address).strip().rstrip("/")
    clean_api_key = str(api_key).strip()
    if not clean_address or (clean_type == "QUI" and not clean_api_key):
        return None
    try:
        clean_port = int(port) if str(port or "").strip() else None
    except ValueError:
        return None
    with _lock, _database() as connection:
        clean_instance_id = str(instance_id).strip() or ("1" if clean_type == "QUI" else "")
        cursor = connection.execute(
            """
            INSERT INTO torrent_clients (
                name, client_type, address, port, api_key, instance_id,
                initial_sync_mode, sync_pending, username, password, rpc_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                str(name).strip() or clean_type,
                clean_type,
                clean_address,
                clean_port,
                clean_api_key,
                clean_instance_id,
                _initial_sync_mode(initial_sync_mode),
                str(username).strip(),
                str(password),
                str(rpc_path).strip(),
            ),
        )
        return int(cursor.lastrowid)


def update_torrent_client(
    client_id,
    name,
    address,
    port,
    api_key,
    instance_id,
    initial_sync_mode="",
    client_type="QUI",
    username="",
    password="",
    rpc_path="",
):
    init_database()
    clean_type = _client_type(client_type)
    clean_address = str(address).strip().rstrip("/")
    if not clean_address:
        return False
    try:
        clean_port = int(port) if str(port or "").strip() else None
    except ValueError:
        return False
    with _lock, _database() as connection:
        current = connection.execute(
            """
            SELECT api_key, password, initial_sync_mode, sync_pending
            FROM torrent_clients WHERE id = ?
            """,
            (int(client_id),),
        ).fetchone()
        if current is None:
            return False
        clean_api_key = str(api_key).strip() or current["api_key"]
        clean_password = str(password) or current["password"]
        if clean_type == "QUI" and not clean_api_key:
            return False
        requested_mode = str(initial_sync_mode or "").strip().lower()
        reset_requested = requested_mode in {"replace", "add", "preserve"}
        clean_mode = (
            _initial_sync_mode(requested_mode)
            if reset_requested
            else _initial_sync_mode(current["initial_sync_mode"])
        )
        sync_pending = 1 if reset_requested else int(current["sync_pending"])
        clean_instance_id = str(instance_id).strip() or ("1" if clean_type == "QUI" else "")
        connection.execute(
            """
            UPDATE torrent_clients
            SET name = ?, client_type = ?, address = ?, port = ?, api_key = ?, instance_id = ?,
                initial_sync_mode = ?, sync_pending = ?, username = ?, password = ?, rpc_path = ?
            WHERE id = ?
            """,
            (
                str(name).strip() or clean_type,
                clean_type,
                clean_address,
                clean_port,
                clean_api_key,
                clean_instance_id,
                clean_mode,
                sync_pending,
                str(username).strip(),
                clean_password,
                str(rpc_path).strip(),
                int(client_id),
            ),
        )
    return True


def update_torrent_client_connection_status(client_id, success, error=""):
    init_database()
    with _lock, _database() as connection:
        connection.execute(
            """
            UPDATE torrent_clients
            SET last_connection_success = ?, last_connection_at = ?,
                last_connection_error = ?
            WHERE id = ?
            """,
            (
                1 if success else 0,
                _now_utc().isoformat(),
                "" if success else str(error or "")[:500],
                int(client_id),
            ),
        )


def get_torrent_client(client_id):
    return next(
        (client for client in list_torrent_clients() if client["id"] == int(client_id)),
        None,
    )


def delete_torrent_client(client_id):
    init_database()
    with _lock, _database() as connection:
        connection.execute("DELETE FROM torrent_clients WHERE id = ?", (int(client_id),))


def clear_tracker_buffers(keys):
    clean_keys = sorted({str(key) for key in keys if str(key)})
    if not clean_keys:
        return
    init_database()
    with _lock, _database() as connection:
        connection.execute(
            f"""
            UPDATE trackers
            SET buffer_uploaded = 0, buffer_downloaded = 0,
                buffer_uploaded_text = '0 B', buffer_downloaded_text = '0 B'
            WHERE key IN ({",".join("?" for _ in clean_keys)})
            """,
            clean_keys,
        )


def complete_client_initializations(client_ids):
    ids = sorted({int(client_id) for client_id in client_ids})
    if not ids:
        return
    init_database()
    with _lock, _database() as connection:
        connection.execute(
            f"""
            UPDATE torrent_clients
            SET sync_pending = 0
            WHERE id IN ({",".join("?" for _ in ids)})
            """,
            ids,
        )


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
                'refresh_interval_seconds',
                'http_timeout_seconds',
                'credit_warning_threshold',
                'credit_warning_threshold_text',
                'prowlarr_enabled',
                'prowlarr_base_url',
                'prowlarr_api_key'
            )
            """
        ).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    refresh_interval_seconds = max(
        60, _stored_int(stored, "refresh_interval_seconds", 3600)
    )
    return {
        "iframe_enabled": stored.get("iframe_enabled", "1") != "0",
        "homarr_auth_enabled": stored.get("homarr_auth_enabled", "0") == "1",
        "homarr_base_url": stored.get("homarr_base_url", ""),
        "homarr_session_endpoint": stored.get(
            "homarr_session_endpoint", "/api/auth/session"
        ),
        "refresh_interval_seconds": refresh_interval_seconds,
        "refresh_interval_minutes": max(1, int(round(refresh_interval_seconds / 60))),
        "http_timeout_seconds": max(1.0, _stored_float(stored, "http_timeout_seconds", 10)),
        "credit_warning_threshold": max(
            0, _stored_int(stored, "credit_warning_threshold", 0)
        ),
        "credit_warning_threshold_text": stored.get(
            "credit_warning_threshold_text", "0 B"
        ),
        "prowlarr_enabled": stored.get("prowlarr_enabled", "0") == "1",
        "prowlarr_base_url": stored.get("prowlarr_base_url", ""),
        "prowlarr_api_key": stored.get("prowlarr_api_key", ""),
        "prowlarr_api_key_hint": _secret_hint(stored.get("prowlarr_api_key", "")),
    }


def update_app_options(
    iframe_enabled,
    homarr_auth_enabled=False,
    homarr_base_url="",
    homarr_session_endpoint="/api/auth/session",
    refresh_interval_minutes=60,
    http_timeout_seconds=10,
    credit_warning_threshold="0 B",
    prowlarr_enabled=False,
    prowlarr_base_url="",
    prowlarr_api_key="",
):
    init_database()
    try:
        refresh_interval_seconds = max(60, int(float(refresh_interval_minutes) * 60))
    except (TypeError, ValueError):
        refresh_interval_seconds = 3600
    try:
        clean_timeout = max(1.0, float(http_timeout_seconds))
    except (TypeError, ValueError):
        clean_timeout = 10.0
    credit_warning_text = str(credit_warning_threshold).strip() or "0 B"
    settings = {
        "iframe_enabled": "1" if iframe_enabled else "0",
        "homarr_auth_enabled": "1" if homarr_auth_enabled else "0",
        "homarr_base_url": str(homarr_base_url).strip().rstrip("/"),
        "homarr_session_endpoint": str(homarr_session_endpoint).strip() or "/api/auth/session",
        "refresh_interval_seconds": str(refresh_interval_seconds),
        "http_timeout_seconds": str(clean_timeout),
        "credit_warning_threshold": str(max(0, parse_bytes(credit_warning_text))),
        "credit_warning_threshold_text": credit_warning_text,
        "prowlarr_enabled": "1" if prowlarr_enabled else "0",
        "prowlarr_base_url": str(prowlarr_base_url).strip().rstrip("/"),
        "prowlarr_api_key": str(prowlarr_api_key).strip(),
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


def get_torrent_rules(client_id, torrent_hashes=()):
    init_database()
    hashes = sorted({str(value or "").strip().lower() for value in torrent_hashes if str(value or "").strip()})
    if not hashes:
        return {}
    with _lock, _database() as connection:
        rows = connection.execute(
            f"""
            SELECT torrent_hash, upload_multiplier, download_multiplier, label, source,
                   lookup_attempts
            FROM torrent_rules
            WHERE client_id = ? AND torrent_hash IN ({",".join("?" for _ in hashes)})
            """,
            [int(client_id), *hashes],
        ).fetchall()
    return {
        row["torrent_hash"]: {
            "upload_multiplier": float(row["upload_multiplier"]),
            "download_multiplier": float(row["download_multiplier"]),
            "label": row["label"],
            "source": row["source"],
            "lookup_attempts": int(row["lookup_attempts"]),
        }
        for row in rows
    }


def save_torrent_rule(client_id, torrent_hash, tracker, rule):
    save_torrent_rules(client_id, [(torrent_hash, tracker, rule)])


def save_torrent_rules(client_id, rules):
    values = []
    created_at = _now_utc().isoformat()
    for torrent_hash, tracker, rule in rules:
        clean_hash = str(torrent_hash or "").strip().lower()
        if not clean_hash:
            continue
        values.append(
            (
                int(client_id),
                clean_hash,
                str(tracker or ""),
                float(rule.get("upload_multiplier", 1)),
                float(rule.get("download_multiplier", 1)),
                str(rule.get("label", "Normal")),
                str(rule.get("source", "detected")),
                created_at,
            )
        )
    if not values:
        return
    init_database()
    with _lock, _database() as connection:
        connection.executemany(
            """
            INSERT INTO torrent_rules (
                client_id, torrent_hash, tracker, upload_multiplier,
                download_multiplier, label, source, created_at, lookup_attempts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(client_id, torrent_hash) DO UPDATE SET
                upload_multiplier = excluded.upload_multiplier,
                download_multiplier = excluded.download_multiplier,
                label = excluded.label,
                source = excluded.source,
                lookup_attempts = torrent_rules.lookup_attempts + 1
            WHERE torrent_rules.source IN ('detected', 'prowlarr-miss')
            """,
            values,
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


def _is_parent_domain(parent, child):
    clean_parent = str(parent).strip().lower().rstrip(".")
    clean_child = str(child).strip().lower().rstrip(".")
    return bool(clean_parent and clean_child.endswith(f".{clean_parent}"))


def _merge_related_domains(connection, parent_domain, child_domain):
    rows = {
        row["domain"]: row
        for row in connection.execute(
            """
            SELECT d.domain, d.tracker_key, t.display_name
            FROM tracker_domains AS d
            JOIN trackers AS t ON t.key = d.tracker_key
            WHERE d.domain IN (?, ?)
            """,
            (parent_domain, child_domain),
        ).fetchall()
    }
    parent = rows.get(parent_domain)
    child = rows.get(child_domain)
    if parent is None or child is None or parent["tracker_key"] == child["tracker_key"]:
        return
    parent_named = parent["display_name"].casefold() != parent_domain.casefold()
    child_named = child["display_name"].casefold() != child_domain.casefold()
    if parent_named and child_named:
        return
    target = child if child_named else parent
    source = parent if target is child else child
    connection.execute(
        "UPDATE tracker_domains SET tracker_key = ? WHERE domain = ?",
        (target["tracker_key"], source["domain"]),
    )
    _move_empty_tracker_buffers(connection, source["tracker_key"], target["tracker_key"])
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
        (source["tracker_key"],),
    )


def _auto_group_parent_domains(connection, candidate_domains):
    domains = [
        row["domain"]
        for row in connection.execute("SELECT domain FROM tracker_domains").fetchall()
    ]
    candidates = {str(domain).strip().lower() for domain in candidate_domains}
    for parent in sorted(domains, key=lambda value: (value.count("."), len(value), value)):
        for child in domains:
            if (
                (parent in candidates or child in candidates)
                and _is_parent_domain(parent, child)
            ):
                _merge_related_domains(connection, parent, child)


def _auto_group_existing_parent_domains_once(connection):
    already_done = connection.execute(
        "SELECT 1 FROM app_metadata WHERE key = ?",
        (_AUTO_PARENT_DOMAIN_GROUPING_KEY,),
    ).fetchone()
    if already_done is not None:
        return
    domains = [
        row["domain"]
        for row in connection.execute("SELECT domain FROM tracker_domains").fetchall()
    ]
    _auto_group_parent_domains(connection, domains)
    connection.execute(
        "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
        (_AUTO_PARENT_DOMAIN_GROUPING_KEY, "done"),
    )


def ensure_discovered_domains(domains):
    init_database()
    new_domains = set()
    with _lock, _database() as connection:
        for domain in sorted({str(domain).strip().lower() for domain in domains if str(domain).strip()}):
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
            new_domains.add(domain)
        if new_domains:
            _auto_group_parent_domains(connection, new_domains)


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
            "minimum_ratio": float(row["minimum_ratio"]),
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
            "minimum_ratio": float(row["minimum_ratio"]),
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
            uploaded_value, uploaded_text = _updated_buffer_values(values, "uploaded")
            downloaded_value, downloaded_text = _updated_buffer_values(values, "downloaded")
            connection.execute(
                """
                UPDATE trackers
                SET display_name = ?, visible_dashboard = ?, visible_widget = ?,
                    buffer_uploaded = ?, buffer_downloaded = ?,
                    buffer_uploaded_text = ?, buffer_downloaded_text = ?,
                    minimum_ratio = ?,
                    event_uploaded_multiplier = ?, event_downloaded_multiplier = ?,
                    event_uploaded_expires_at = ?, event_downloaded_expires_at = ?
                WHERE key = ?
                """,
                (
                    str(values["display_name"]).strip() or key,
                    int(bool(values["visible_dashboard"])),
                    int(bool(values["visible_widget"])),
                    uploaded_value,
                    downloaded_value,
                    uploaded_text,
                    downloaded_text,
                    max(0.01, float(values.get("minimum_ratio", 1) or 1)),
                    float(values.get("event_uploaded_multiplier", 1)),
                    float(values.get("event_downloaded_multiplier", 1)),
                    _updated_event_expiry(values, "uploaded"),
                    _updated_event_expiry(values, "downloaded"),
                    key,
                )
            )
        _merge_duplicate_display_names(connection)
