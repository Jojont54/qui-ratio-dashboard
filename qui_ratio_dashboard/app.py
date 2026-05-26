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
    get_torrent_client,
    group_domains,
    init_database,
    list_torrent_clients,
    list_trackers,
    load_tracker_configuration,
    unlink_domains,
    update_app_options,
    update_torrent_client,
    update_trackers,
)
from .direct_clients import configured_client as build_torrent_client
from .qui_client import QuiClient
from .formatters import aggregate_tracker_rows, compute_domain_rows, fmt_bytes
from .state_store import apply_domain_ledger
from .config import (
    PORT,
)


app = Flask(__name__)
init_database()
_refresh_stop = Event()
_refresh_wake = Event()
_refresh_lock = Lock()


def refresh_rows():
    with _refresh_lock:
        return _refresh_rows_locked()


def _refresh_rows_locked():
    options = get_app_options()
    configured_clients = list_torrent_clients()
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
            client = build_torrent_client(configured_client, options["http_timeout_seconds"])
            client_rows = compute_domain_rows(client.fetch_torrents_summary())
            for row in client_rows:
                row["ledger_key"] = f"client:{configured_client['id']}:{row['domain']}"
                row["client_id"] = configured_client["id"]
            domain_rows.extend(client_rows)
            successful_clients.add(configured_client["id"])
        except Exception as error:
            errors.append(f"{configured_client['name']}: {error}")
    if errors:
        raise RuntimeError("Unable to collect all clients: " + "; ".join(errors))
    domain_to_key, trackers = load_tracker_configuration(row["domain"] for row in domain_rows)
    domain_rows, legacy_adjustments, replaced_keys, initialized_clients = apply_domain_ledger(
        domain_rows, domain_to_key, trackers, pending_initializations, successful_clients
    )
    clear_tracker_buffers(replaced_keys)
    complete_client_initializations(initialized_clients)
    return aggregate_tracker_rows(domain_rows, legacy_adjustments)


def refresh_periodically():
    while not _refresh_stop.is_set():
        options = get_app_options()
        if options["background_refresh_enabled"]:
            try:
                refresh_rows()
            except Exception:
                app.logger.exception("Background ratio refresh failed")
            wait_seconds = options["refresh_interval_seconds"]
        else:
            wait_seconds = None
        _refresh_wake.wait(wait_seconds)
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
    rows = refresh_rows()
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
    rows = refresh_rows()
    rows = [row for row in rows if row.get("widget_visible", True)]
    return render_template("widget.html", rows=rows, fmt_bytes=fmt_bytes, infinity=math.inf)


@app.get("/app/")
@app.get("/")
def dashboard():
    rows = refresh_rows()
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
    try:
        refresh_rows()
    except Exception:
        app.logger.exception("Unable to discover tracker domains for management view")
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
    updates = {}
    for key in keys:
        updates[key] = {
            "display_name": request.form.get(f"display__{key}", key),
            "visible_dashboard": request.form.get(f"dashboard__{key}") == "on",
            "visible_widget": request.form.get(f"widget__{key}") == "on",
            "uploaded_add": request.form.get(f"upload__{key}", "0"),
            "downloaded_add": request.form.get(f"download__{key}", "0"),
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
    event_changed = any(
        float(values["event_uploaded_multiplier"])
        != current_settings.get(key, {}).get("event_uploaded_multiplier", 1)
        or float(values["event_downloaded_multiplier"])
        != current_settings.get(key, {}).get("event_downloaded_multiplier", 1)
        for key, values in updates.items()
    )
    if event_changed:
        try:
            refresh_rows()
        except Exception:
            app.logger.exception("Unable to collect an event-change baseline")
    update_trackers(updates)
    return redirect(url_for("trackers"))


@app.post("/app/trackers/group")
@app.post("/trackers/group")
def group_selected_domains():
    group_domains(request.form.getlist("domains"), request.form.get("display_name", ""))
    return redirect(url_for("trackers"))


@app.post("/app/trackers/unlink")
@app.post("/trackers/unlink")
def unlink_selected_domains():
    unlink_domains(request.form.getlist("domains"))
    return redirect(url_for("trackers"))


@app.get("/clients")
def torrent_clients():
    return render_template("clients.html", clients=list_torrent_clients())


@app.post("/clients/add")
def add_client():
    add_torrent_client(
        request.form.get("name", "QUI"),
        request.form.get("address", ""),
        request.form.get("port", ""),
        request.form.get("api_key", ""),
        request.form.get("instance_id", ""),
        request.form.get("initial_sync_mode", "preserve"),
        request.form.get("client_type", "QUI"),
        request.form.get("username", ""),
        request.form.get("password", ""),
        request.form.get("rpc_path", ""),
    )
    return redirect(url_for("torrent_clients"))


@app.post("/clients/<int:client_id>/delete")
def delete_client(client_id):
    delete_torrent_client(client_id)
    return redirect(url_for("torrent_clients"))


@app.post("/clients/<int:client_id>/update")
def update_client(client_id):
    update_torrent_client(
        client_id,
        request.form.get("name", "QUI"),
        request.form.get("address", ""),
        request.form.get("port", ""),
        request.form.get("api_key", ""),
        request.form.get("instance_id", ""),
        request.form.get("initial_sync_mode", ""),
        request.form.get("client_type", "QUI"),
        request.form.get("username", ""),
        request.form.get("password", ""),
        request.form.get("rpc_path", ""),
    )
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
    if not request.form.get("address", "").strip():
        return jsonify({"error": "Adresse requise."}), 400
    try:
        client = build_torrent_client(configuration, get_app_options()["http_timeout_seconds"])
        if configuration["client_type"].upper() == "QUI":
            instances = client.list_instances()
            return jsonify({"message": f"{len(instances)} instance(s) QUI disponible(s)."})
        payload = client.fetch_torrents_summary()
        transfers = payload.get("counts", {}).get("trackerTransfers", {})
        count = sum(int(transfer.get("count", 0)) for transfer in transfers.values())
        return jsonify(
            {"message": f"Connexion reussie : {count} torrent(s), {len(transfers)} tracker(s)."}
        )
    except Exception as error:
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
        return jsonify({"error": "Adresse et cle API requises."}), 400
    base_url = address.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    if port:
        base_url = f"{base_url}:{port}"
    try:
        instances = QuiClient(
            base_url, api_key, "1", get_app_options()["http_timeout_seconds"]
        ).list_instances()
    except Exception as error:
        return jsonify({"error": f"Connexion QUI impossible : {error}"}), 400
    return jsonify({"instances": instances})


@app.get("/options")
def options():
    return render_template("options.html", options=get_app_options())


@app.post("/options")
def save_options():
    update_app_options(
        request.form.get("iframe_enabled") == "on",
        request.form.get("homarr_auth_enabled") == "on",
        request.form.get("homarr_base_url", ""),
        request.form.get("homarr_session_endpoint", "/api/auth/session"),
        request.form.get("background_refresh_enabled") == "on",
        request.form.get("refresh_interval_minutes", "60"),
        request.form.get("http_timeout_seconds", "10"),
    )
    _refresh_wake.set()
    return redirect(url_for("options"))


start_background_refresh()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
