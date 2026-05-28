import math
import requests
from threading import Event, Lock, Thread
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from .database import (
    add_torrent_client,
    clear_tracker_buffers,
    complete_client_initializations,
    delete_torrent_client,
    get_app_options,
    get_torrent_rules,
    get_torrent_client,
    group_domains,
    init_database,
    list_torrent_clients,
    list_trackers,
    load_tracker_configuration,
    save_torrent_rules,
    unlink_domains,
    update_app_options,
    update_torrent_client,
    update_torrent_client_connection_status,
    update_trackers,
)
from .direct_clients import configured_client as build_torrent_client
from .collector import prowlarr_lookup_hashes, recent_torrent_hashes, torrent_rows
from .diagnostics import log_event, short_hash
from .prowlarr_client import ProwlarrClient
from .qui_client import QuiClient
from .formatters import aggregate_tracker_rows, compute_domain_rows, fmt_bytes
from .state_store import apply_domain_ledger, stored_domain_rows
from .units import has_byte_unit
from .config import (
    PORT,
)


app = Flask(__name__)
init_database()
_refresh_stop = Event()
_refresh_wake = Event()
_refresh_lock = Lock()


@app.context_processor
def inject_sidebar_clients():
    return {"sidebar_clients": list_torrent_clients()}


def refresh_rows(require_all_clients=False):
    log_event("refresh.lock.wait", require_all_clients=require_all_clients)
    with _refresh_lock:
        return _refresh_rows_locked(require_all_clients)


def cached_rows():
    domain_rows, legacy_adjustments = stored_domain_rows()
    return aggregate_tracker_rows(domain_rows, legacy_adjustments)


def _raw_totals_by_tracker():
    totals = {}
    for row in cached_rows():
        totals[row["_key"]] = {
            "uploaded": int(row["uploaded"]) - int(row.get("manual_buffer_uploaded", 0)),
            "downloaded": int(row["downloaded"]) - int(row.get("manual_buffer_downloaded", 0)),
        }
    return totals


def _missing_unit_message(updates):
    labels = {
        "uploaded_add": "Buffer upload",
        "downloaded_add": "Buffer download",
        "uploaded_target": "Valeur site upload",
        "downloaded_target": "Valeur site download",
    }
    missing = []
    for values in updates.values():
        tracker_name = str(values.get("display_name") or "").strip()
        for field, label in labels.items():
            value = str(values.get(field, "")).strip()
            if value and not has_byte_unit(value):
                missing.append(f"{tracker_name} / {label}")
    if not missing:
        return ""
    return "Unité manquante : ajoutez une unité comme GiB, Gio, Go, GB, G, TiB, Tio, To, TB ou T."


def _refresh_rows_locked(require_all_clients=False):
    options = get_app_options()
    configured_clients = list_torrent_clients()
    log_event(
        "refresh.start",
        clients=len(configured_clients),
        require_all_clients=require_all_clients,
    )
    pending_initializations = {
        client["id"]: client["initial_sync_mode"]
        for client in configured_clients
        if client["sync_pending"]
    }
    domain_rows = []
    successful_clients = set()
    errors = []
    for configured_client in configured_clients:
        try:
            log_event(
                "client.collect.start",
                client=configured_client["name"],
                client_type=configured_client["client_type"],
                sync_pending=configured_client["sync_pending"],
            )
            client = build_torrent_client(configured_client, options["http_timeout_seconds"])
            if configured_client["client_type"] == "QUI":
                payload = client.fetch_torrents_summary()
                client_rows = compute_domain_rows(payload)
                for row in client_rows:
                    row["ledger_key"] = f"client:{configured_client['id']}:{row['domain']}"
            else:
                transfers = client.fetch_torrents()
                hashes = [
                    str(transfer.get("hash", "")).strip().lower()
                    for transfer in transfers
                    if str(transfer.get("hash", "")).strip()
                ]
                rules = get_torrent_rules(configured_client["id"], hashes)
                missing = sorted({torrent_hash for torrent_hash in hashes if torrent_hash not in rules})
                new_rules = {
                    torrent_hash: {
                        "upload_multiplier": 1,
                        "download_multiplier": 1,
                        "label": "Normal",
                        "source": "detected",
                    }
                    for torrent_hash in missing
                }
                prowlarr_checks = []
                if options["prowlarr_enabled"]:
                    recent_hashes = recent_torrent_hashes(
                        transfers, options["refresh_interval_seconds"]
                    )
                    prowlarr_checks = prowlarr_lookup_hashes(hashes, rules, recent_hashes)
                    if prowlarr_checks:
                        log_event(
                            "prowlarr.lookup.start",
                            client=configured_client["name"],
                            client_type=configured_client["client_type"],
                            count=len(prowlarr_checks),
                            hashes=",".join(short_hash(value) for value in prowlarr_checks[:20]),
                        )
                if prowlarr_checks and options["prowlarr_enabled"]:
                    prowlarr = ProwlarrClient(
                        options["prowlarr_base_url"],
                        options["prowlarr_api_key"],
                        options["http_timeout_seconds"],
                    )
                    try:
                        prowlarr_rules = prowlarr.torrent_rules(prowlarr_checks)
                        new_rules.update(prowlarr_rules)
                    except Exception as error:
                        log_event(
                            "prowlarr.lookup.error",
                            client=configured_client["name"],
                            error=str(error),
                        )
                        raise
                rules_to_save = []
                for transfer in transfers:
                    torrent_hash = str(transfer.get("hash", "")).strip().lower()
                    if torrent_hash in new_rules:
                        rule = new_rules[torrent_hash]
                        log_event(
                            "torrent.rule.save",
                            client=configured_client["name"],
                            tracker=transfer.get("tracker", ""),
                            hash=short_hash(torrent_hash),
                            source=rule["source"],
                            label=rule["label"],
                            upload_multiplier=rule.get("upload_multiplier", 1),
                            download_multiplier=rule.get("download_multiplier", 1),
                        )
                        rules_to_save.append(
                            (
                                torrent_hash,
                                transfer.get("tracker", ""),
                                new_rules[torrent_hash],
                            )
                        )
                save_torrent_rules(configured_client["id"], rules_to_save)
                rules.update(new_rules)
                client_rows = torrent_rows(transfers, rules)
                for row in client_rows:
                    row["previous_ledger_key"] = (
                        f"client:{configured_client['id']}:{row['domain']}"
                    )
                    row["ledger_key"] = (
                        f"client:{configured_client['id']}:torrent:{row['hash']}"
                        if row["hash"]
                        else f"client:{configured_client['id']}:{row['domain']}"
                    )
            for row in client_rows:
                row["client_id"] = configured_client["id"]
            domain_rows.extend(client_rows)
            successful_clients.add(configured_client["id"])
            update_torrent_client_connection_status(configured_client["id"], True)
            log_event(
                "client.collect.success",
                client=configured_client["name"],
                client_type=configured_client["client_type"],
                rows=len(client_rows),
            )
        except Exception as error:
            update_torrent_client_connection_status(
                configured_client["id"], False, error
            )
            errors.append(f"{configured_client['name']}: {error}")
            log_event(
                "client.collect.error",
                client=configured_client["name"],
                client_type=configured_client["client_type"],
                error=str(error),
            )
    domain_to_key, trackers = load_tracker_configuration(row["domain"] for row in domain_rows)
    domain_rows, legacy_adjustments, replaced_keys, initialized_clients = apply_domain_ledger(
        domain_rows,
        domain_to_key,
        trackers,
        pending_initializations,
        successful_clients,
        {client["id"] for client in configured_clients if client["id"] not in successful_clients},
    )
    clear_tracker_buffers(replaced_keys)
    complete_client_initializations(initialized_clients)
    rows = aggregate_tracker_rows(domain_rows, legacy_adjustments)
    if errors:
        message = "Unable to collect some clients: " + "; ".join(errors)
        log_event("refresh.partial", errors="; ".join(errors), rows=len(rows))
        app.logger.warning(message)
        if require_all_clients:
            raise RuntimeError(message)
    else:
        log_event("refresh.success", rows=len(rows))
    return rows


def refresh_periodically():
    while not _refresh_stop.is_set():
        options = get_app_options()
        try:
            log_event("refresh.background.tick")
            refresh_rows()
        except Exception:
            log_event("refresh.background.error")
            app.logger.exception("Background ratio refresh failed")
        _refresh_wake.wait(options["refresh_interval_seconds"])
        _refresh_wake.clear()


def start_background_refresh():
    Thread(target=refresh_periodically, name="ratio-refresh", daemon=True).start()


def homarr_session_ok(options) -> bool:
    """
    Vérifie si l'utilisateur est authentifié sur Homarr,
    en appelant Homarr côté serveur avec le cookie du client.
    """
    cookie = request.headers.get("Cookie", "")
    if not cookie:
        return False
    if not options["homarr_base_url"]:
        return False

    try:
        r = requests.get(
            options["homarr_base_url"] + options["homarr_session_endpoint"],
            headers={"Cookie": cookie},
            timeout=options["http_timeout_seconds"],
            allow_redirects=False,
        )
        return r.status_code == 200
    except requests.RequestException:
        # fail-closed
        return False


@app.after_request
def add_headers(resp):
    ancestors = ["'self'"]
    homarr_url = get_app_options()["homarr_base_url"]
    parsed = urlsplit(homarr_url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        ancestors.append(f"{parsed.scheme}://{parsed.netloc}")
    resp.headers["Content-Security-Policy"] = "frame-ancestors " + " ".join(ancestors)
    return resp


@app.get("/health")
def health():
    return "ok"


@app.get("/api/ratios")
def api_ratios():
    rows = cached_rows()
    out = []
    for r in rows:
        rr = dict(r)
        if rr["ratio"] == math.inf:
            rr["ratio"] = None
        out.append(rr)
    return jsonify({"trackers": out})


@app.get("/widget")
@app.get("/iframe")
def iframe():
    options = get_app_options()
    if not options["iframe_enabled"]:
        abort(404)
    if options["homarr_auth_enabled"] and not homarr_session_ok(options):
        abort(401)
    rows = cached_rows()
    rows = [row for row in rows if row.get("widget_visible", True)]
    return render_template("widget.html", rows=rows, fmt_bytes=fmt_bytes, infinity=math.inf)


@app.get("/app/")
@app.get("/")
def dashboard():
    rows = cached_rows()
    rows = [row for row in rows if row.get("dashboard_visible", True)]
    return render_template(
        "dashboard.html",
        rows=rows,
        fmt_bytes=fmt_bytes,
        infinity=math.inf,
        options=get_app_options(),
    )


@app.get("/app/trackers")
@app.get("/trackers")
def trackers():
    return render_template(
        "trackers.html",
        trackers=list_trackers(),
    )


@app.post("/app/trackers/settings")
@app.post("/trackers/settings")
def save_tracker_settings():
    prefix = "display__"
    keys = [name[len(prefix):] for name in request.form if name.startswith(prefix)]
    current_settings = {tracker["key"]: tracker for tracker in list_trackers()}
    raw_totals = _raw_totals_by_tracker()
    updates = {}
    for key in keys:
        raw = raw_totals.get(key, {"uploaded": 0, "downloaded": 0})
        updates[key] = {
            "display_name": request.form.get(f"display__{key}", key),
            "visible_dashboard": request.form.get(f"dashboard__{key}") == "on",
            "visible_widget": request.form.get(f"widget__{key}") == "on",
            "uploaded_add": request.form.get(f"upload__{key}", "0"),
            "downloaded_add": request.form.get(f"download__{key}", "0"),
            "uploaded_target": request.form.get(f"site_upload__{key}", ""),
            "downloaded_target": request.form.get(f"site_download__{key}", ""),
            "raw_uploaded": raw["uploaded"],
            "raw_downloaded": raw["downloaded"],
            "event_uploaded_multiplier": request.form.get(f"event_upload__{key}", "1"),
            "event_downloaded_multiplier": request.form.get(f"event_download__{key}", "1"),
            "event_uploaded_hours_remaining": request.form.get(f"event_upload_hours__{key}", ""),
            "event_downloaded_hours_remaining": request.form.get(f"event_download_hours__{key}", ""),
            "original_event_uploaded_multiplier": request.form.get(f"original_event_upload__{key}", "1"),
            "original_event_downloaded_multiplier": request.form.get(f"original_event_download__{key}", "1"),
            "original_event_uploaded_hours_remaining": request.form.get(f"original_event_upload_hours__{key}", ""),
            "original_event_downloaded_hours_remaining": request.form.get(f"original_event_download_hours__{key}", ""),
            "original_event_uploaded_expires_at": request.form.get(f"original_event_upload_expires__{key}", ""),
            "original_event_downloaded_expires_at": request.form.get(f"original_event_download_expires__{key}", ""),
        }
    missing_unit = _missing_unit_message(updates)
    if missing_unit:
        return (
            render_template(
                "trackers.html",
                trackers=list_trackers(),
                save_error=missing_unit,
            ),
            400,
        )
    event_changed = any(
        float(values["event_uploaded_multiplier"])
        != current_settings.get(key, {}).get("event_uploaded_multiplier", 1)
        or float(values["event_downloaded_multiplier"])
        != current_settings.get(key, {}).get("event_downloaded_multiplier", 1)
        for key, values in updates.items()
    )
    if event_changed:
        try:
            refresh_rows(require_all_clients=True)
        except Exception as error:
            app.logger.exception("Unable to collect an event-change baseline")
            return (
                render_template(
                    "trackers.html",
                    trackers=list_trackers(),
                    save_error=(
                        "Événement non activé : impossible de relever toutes les "
                        "sources avant son démarrage. Réessayez lorsque les clients "
                        f"sont disponibles. ({error})"
                    ),
                ),
                503,
            )
    update_trackers(updates)
    log_event("trackers.settings.saved", count=len(updates))
    return redirect(url_for("trackers"))


@app.post("/app/trackers/group")
@app.post("/trackers/group")
def group_selected_domains():
    domains = request.form.getlist("domains")
    display_name = request.form.get("display_name", "")
    group_domains(domains, display_name)
    log_event("trackers.group", display_name=display_name, count=len(domains))
    return redirect(url_for("trackers"))


@app.post("/app/trackers/unlink")
@app.post("/trackers/unlink")
def unlink_selected_domains():
    domains = request.form.getlist("domains")
    unlink_domains(domains)
    log_event("trackers.unlink", count=len(domains))
    return redirect(url_for("trackers"))


@app.get("/clients")
def torrent_clients():
    return render_template("clients.html", clients=list_torrent_clients())


@app.post("/clients/add")
def add_client():
    name = request.form.get("name", "QUI")
    client_type = request.form.get("client_type", "QUI")
    add_torrent_client(
        name,
        request.form.get("address", ""),
        request.form.get("port", ""),
        request.form.get("api_key", ""),
        request.form.get("instance_id", ""),
        request.form.get("initial_sync_mode", "preserve"),
        client_type,
        request.form.get("username", ""),
        request.form.get("password", ""),
        request.form.get("rpc_path", ""),
    )
    log_event(
        "client.added",
        client=name,
        client_type=client_type,
        initial_sync_mode=request.form.get("initial_sync_mode", "preserve"),
    )
    return redirect(url_for("torrent_clients"))


@app.post("/clients/<int:client_id>/delete")
def delete_client(client_id):
    client = get_torrent_client(client_id) or {}
    delete_torrent_client(client_id)
    log_event(
        "client.deleted",
        client=client.get("name", client_id),
        client_type=client.get("client_type", ""),
    )
    return redirect(url_for("torrent_clients"))


@app.post("/clients/<int:client_id>/update")
def update_client(client_id):
    name = request.form.get("name", "QUI")
    client_type = request.form.get("client_type", "QUI")
    update_torrent_client(
        client_id,
        name,
        request.form.get("address", ""),
        request.form.get("port", ""),
        request.form.get("api_key", ""),
        request.form.get("instance_id", ""),
        request.form.get("initial_sync_mode", ""),
        client_type,
        request.form.get("username", ""),
        request.form.get("password", ""),
        request.form.get("rpc_path", ""),
    )
    log_event("client.updated", client=name, client_type=client_type)
    return redirect(url_for("torrent_clients"))


def _form_client_configuration():
    address = request.form.get("address", "").strip().rstrip("/")
    port = request.form.get("port", "").strip()
    if not address.startswith(("http://", "https://")):
        address = f"http://{address}"
    base_url = f"{address}:{port}" if port else address
    client_id = request.form.get("client_id", "").strip()
    current = get_torrent_client(client_id) if client_id.isdigit() else {}
    return {
        "client_type": request.form.get("client_type", "QUI"),
        "base_url": base_url,
        "api_key": request.form.get("api_key", "").strip() or current.get("api_key", ""),
        "instance_id": request.form.get("instance_id", "").strip() or current.get("instance_id", "1"),
        "username": request.form.get("username", "").strip(),
        "password": request.form.get("password", "") or current.get("password", ""),
        "rpc_path": request.form.get("rpc_path", "").strip(),
    }


@app.post("/clients/test")
def test_torrent_client_connection():
    configuration = _form_client_configuration()
    client_id = request.form.get("client_id", "").strip()
    if not request.form.get("address", "").strip():
        return jsonify({"error": "Adresse requise."}), 400
    try:
        log_event(
            "client.test.start",
            client_type=configuration["client_type"],
            client_id=client_id or "new",
        )
        client = build_torrent_client(configuration, get_app_options()["http_timeout_seconds"])
        if configuration["client_type"].upper() == "QUI":
            instances = client.list_instances()
            if client_id.isdigit():
                update_torrent_client_connection_status(int(client_id), True)
            log_event(
                "client.test.success",
                client_type=configuration["client_type"],
                client_id=client_id or "new",
                instances=len(instances),
            )
            return jsonify({"message": f"{len(instances)} instance(s) QUI disponible(s)."})
        payload = client.fetch_torrents_summary()
        transfers = payload.get("counts", {}).get("trackerTransfers", {})
        count = sum(int(transfer.get("count", 0)) for transfer in transfers.values())
        if client_id.isdigit():
            update_torrent_client_connection_status(int(client_id), True)
        log_event(
            "client.test.success",
            client_type=configuration["client_type"],
            client_id=client_id or "new",
            torrents=count,
            trackers=len(transfers),
        )
        return jsonify(
            {"message": f"Connexion réussie : {count} torrent(s), {len(transfers)} tracker(s)."}
        )
    except Exception as error:
        if client_id.isdigit():
            update_torrent_client_connection_status(int(client_id), False, error)
        log_event(
            "client.test.error",
            client_type=configuration["client_type"],
            client_id=client_id or "new",
            error=str(error),
        )
        return jsonify({"error": f"Connexion impossible : {error}"}), 400


@app.post("/clients/qui/instances")
def discover_qui_instances():
    address = request.form.get("address", "").strip()
    port = request.form.get("port", "").strip()
    api_key = request.form.get("api_key", "").strip()
    client_id = request.form.get("client_id", "").strip()
    if not api_key and client_id.isdigit():
        configured_client = get_torrent_client(client_id)
        api_key = configured_client["api_key"] if configured_client else ""
    if not address or not api_key:
        return jsonify({"error": "Adresse et clé API requises."}), 400
    base_url = address.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    if port:
        base_url = f"{base_url}:{port}"
    try:
        log_event("qui.instances.start", client_id=client_id or "new")
        instances = QuiClient(
            base_url, api_key, "1", get_app_options()["http_timeout_seconds"]
        ).list_instances()
    except Exception as error:
        if client_id.isdigit():
            update_torrent_client_connection_status(int(client_id), False, error)
        log_event("qui.instances.error", client_id=client_id or "new", error=str(error))
        return jsonify({"error": f"Connexion QUI impossible : {error}"}), 400
    if client_id.isdigit():
        update_torrent_client_connection_status(int(client_id), True)
    log_event("qui.instances.success", client_id=client_id or "new", instances=len(instances))
    return jsonify({"instances": instances})


@app.get("/options")
def options():
    return render_template("options.html", options=get_app_options())


@app.post("/options")
def save_options():
    current_options = get_app_options()
    prowlarr_api_key = request.form.get("prowlarr_api_key", "").strip()
    if not prowlarr_api_key:
        prowlarr_api_key = current_options["prowlarr_api_key"]
    update_app_options(
        request.form.get("iframe_enabled") == "on",
        request.form.get("homarr_auth_enabled") == "on",
        request.form.get("homarr_base_url", ""),
        request.form.get("homarr_session_endpoint", "/api/auth/session"),
        request.form.get("refresh_interval_minutes", "60"),
        request.form.get("http_timeout_seconds", "10"),
        request.form.get("prowlarr_enabled") == "on",
        request.form.get("prowlarr_base_url", ""),
        prowlarr_api_key,
    )
    log_event(
        "options.saved",
        iframe_enabled=request.form.get("iframe_enabled") == "on",
        homarr_auth_enabled=request.form.get("homarr_auth_enabled") == "on",
        prowlarr_enabled=request.form.get("prowlarr_enabled") == "on",
        refresh_interval_minutes=request.form.get("refresh_interval_minutes", "60"),
    )
    _refresh_wake.set()
    return redirect(url_for("options"))


@app.post("/options/prowlarr/test")
def test_prowlarr_connection():
    current_options = get_app_options()
    api_key = request.form.get("prowlarr_api_key", "").strip() or current_options["prowlarr_api_key"]
    try:
        log_event("prowlarr.test.start")
        ProwlarrClient(
            request.form.get("prowlarr_base_url", ""),
            api_key,
            current_options["http_timeout_seconds"],
        ).test_connection()
    except Exception as error:
        log_event("prowlarr.test.error", error=str(error))
        return jsonify({"error": f"Connexion Prowlarr impossible : {error}"}), 400
    log_event("prowlarr.test.success")
    return jsonify({"message": "Connexion Prowlarr réussie."})


@app.post("/options/sync")
def sync_now():
    try:
        log_event("refresh.manual.start")
        rows = refresh_rows()
    except Exception as error:
        log_event("refresh.manual.error", error=str(error))
        return jsonify({"error": f"Synchronisation impossible : {error}"}), 400
    failed_clients = [
        client["name"]
        for client in list_torrent_clients()
        if client["last_connection_success"] is False
    ]
    if failed_clients:
        log_event(
            "refresh.manual.partial",
            rows=len(rows),
            failed_clients=", ".join(failed_clients),
        )
        return jsonify(
            {
                "message": (
                    f"Synchronisation partielle : {len(rows)} tracker(s) mis à jour. "
                    "Source(s) indisponible(s) : "
                    + ", ".join(failed_clients)
                    + "."
                )
            }
        )
    log_event("refresh.manual.success", rows=len(rows))
    return jsonify({"message": f"Synchronisation terminée : {len(rows)} tracker(s) mis à jour."})


start_background_refresh()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
