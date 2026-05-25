import json
import os
from threading import Lock

STATE_PATH = "/data/state.json"
STATE_VERSION = 4

_lock = Lock()


def _empty_state():
    return {"version": STATE_VERSION, "transfers": {}, "legacy_adjustments": {}}


def _ensure_state_file():
    directory = os.path.dirname(STATE_PATH)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    if not os.path.exists(STATE_PATH):
        with open(STATE_PATH, "w") as f:
            json.dump(_empty_state(), f, indent=2)


def _load_state_unlocked():
    _ensure_state_file()
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return _empty_state()


def _save_state_unlocked(state):
    _ensure_state_file()
    temp_path = STATE_PATH + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_path, STATE_PATH)


def _migrate_tracker_state(tracker, current_uploaded, current_downloaded):
    if "raw_uploaded" in tracker:
        return {
            "raw_uploaded": int(tracker["raw_uploaded"]),
            "raw_downloaded": int(tracker["raw_downloaded"]),
            "carried_uploaded": int(tracker.get("carried_uploaded", 0)),
            "carried_downloaded": int(tracker.get("carried_downloaded", 0)),
        }

    if "prev_raw_u" in tracker:
        old_displayed_u = max(int(tracker.get("prev_raw_u", current_uploaded)), current_uploaded)
        old_displayed_d = max(int(tracker.get("prev_raw_d", current_downloaded)), current_downloaded)
        return {
            "raw_uploaded": current_uploaded,
            "raw_downloaded": current_downloaded,
            "carried_uploaded": old_displayed_u - current_uploaded,
            "carried_downloaded": old_displayed_d - current_downloaded,
        }

    if "prev_u" in tracker:
        return {
            "raw_uploaded": int(tracker.get("prev_u", current_uploaded)),
            "raw_downloaded": int(tracker.get("prev_d", current_downloaded)),
            "carried_uploaded": int(tracker.get("buf_u", 0)),
            "carried_downloaded": int(tracker.get("buf_d", 0)),
        }

    return {
        "raw_uploaded": current_uploaded,
        "raw_downloaded": current_downloaded,
        "carried_uploaded": 0,
        "carried_downloaded": 0,
    }
def _legacy_adjustments(state, rows, domain_to_key):
    if "legacy_adjustments" in state:
        return state.get("legacy_adjustments", {})

    current_by_tracker = {}
    for row in rows:
        key = domain_to_key.get(row["domain"], row["domain"])
        totals = current_by_tracker.setdefault(key, {"uploaded": 0, "downloaded": 0})
        totals["uploaded"] += int(row["uploaded"])
        totals["downloaded"] += int(row["downloaded"])

    adjustments = {}
    for key, tracker in (state.get("trackers") or {}).items():
        current = current_by_tracker.get(key, {"uploaded": 0, "downloaded": 0})
        migrated = _migrate_tracker_state(
            tracker, current["uploaded"], current["downloaded"]
        )
        adjustments[key] = {
            "uploaded": migrated["carried_uploaded"],
            "downloaded": migrated["carried_downloaded"],
        }
    return adjustments


def _credited_transfer(tracker, current_uploaded, current_downloaded):
    if "credited_uploaded" in tracker:
        return {
            "raw_uploaded": int(tracker.get("raw_uploaded", current_uploaded)),
            "raw_downloaded": int(tracker.get("raw_downloaded", current_downloaded)),
            "credited_uploaded": int(tracker["credited_uploaded"]),
            "credited_downloaded": int(tracker["credited_downloaded"]),
        }

    migrated = _migrate_tracker_state(tracker, current_uploaded, current_downloaded)
    return {
        "raw_uploaded": migrated["raw_uploaded"],
        "raw_downloaded": migrated["raw_downloaded"],
        "credited_uploaded": migrated["raw_uploaded"] + migrated["carried_uploaded"],
        "credited_downloaded": migrated["raw_downloaded"] + migrated["carried_downloaded"],
    }


def _credited_delta(value, multiplier):
    return int(round(int(value) * float(multiplier)))


def apply_domain_ledger(
    rows, domain_to_key, trackers=None, client_initializations=None, successful_client_ids=None
):
    trackers = trackers or {}
    client_initializations = client_initializations or {}
    if successful_client_ids is None:
        successful_client_ids = {row.get("client_id") for row in rows}
    active_initializations = {
        client_id: mode
        for client_id, mode in client_initializations.items()
        if client_id in successful_client_ids and mode in {"replace", "add", "preserve"}
    }
    initialization_by_ledger = {}
    replace_all = "replace" in active_initializations.values()
    replace_keys = (set(trackers) | set(domain_to_key.values())) if replace_all else set()
    initialized_client_ids = set(active_initializations)
    for row in rows:
        client_id = row.get("client_id")
        mode = active_initializations.get(client_id)
        if mode is None:
            continue
        ledger_key = row.get("ledger_key", row["domain"])
        initialization_by_ledger[ledger_key] = mode
    with _lock:
        state = _load_state_unlocked()
        adjustments = _legacy_adjustments(state, rows, domain_to_key)
        if replace_all:
            adjustments = {}
        transfers = state.get("transfers") if int(state.get("version", 0)) >= 3 else {}
        transfers = transfers or {}
        if replace_all:
            transfers = {}
        seen_transfers = {row.get("ledger_key", row["domain"]) for row in rows}
        for row in rows:
            ledger_key = row.get("ledger_key", row["domain"])
            domain = row["domain"]
            if ledger_key != domain and ledger_key not in transfers and domain in transfers:
                transfers[ledger_key] = transfers.pop(domain)
        for ledger_key, transfer in transfers.items():
            if ledger_key not in seen_transfers:
                domain = transfer.get("domain", ledger_key)
                rows.append(
                    {
                        "tracker": domain,
                        "_key": domain,
                        "domain": domain,
                        "ledger_key": ledger_key,
                        "uploaded": 0,
                        "downloaded": 0,
                        "manual_buffer_uploaded": 0,
                        "manual_buffer_downloaded": 0,
                        "count": 0,
                        "total_size": 0,
                    }
                )

        for row in rows:
            domain = row["domain"]
            ledger_key = row.get("ledger_key", domain)
            current_u = int(row["uploaded"])
            current_d = int(row["downloaded"])
            key = domain_to_key.get(domain, domain)
            config = trackers.get(key, {})
            multiplier_u = float(config.get("event_uploaded_multiplier", 1))
            multiplier_d = float(config.get("event_downloaded_multiplier", 1))
            initialization = initialization_by_ledger.get(ledger_key)
            if initialization == "preserve" and not replace_all:
                existing = _credited_transfer(transfers.get(ledger_key, {}), current_u, current_d)
                tracker = {
                    "raw_uploaded": current_u,
                    "raw_downloaded": current_d,
                    "credited_uploaded": (
                        existing["credited_uploaded"] if ledger_key in transfers else 0
                    ),
                    "credited_downloaded": (
                        existing["credited_downloaded"] if ledger_key in transfers else 0
                    ),
                }
            elif initialization == "add" and not replace_all:
                existing = _credited_transfer(transfers.get(ledger_key, {}), current_u, current_d)
                tracker = {
                    "raw_uploaded": current_u,
                    "raw_downloaded": current_d,
                    "credited_uploaded": (
                        existing["credited_uploaded"] if ledger_key in transfers else 0
                    )
                    + current_u,
                    "credited_downloaded": (
                        existing["credited_downloaded"] if ledger_key in transfers else 0
                    )
                    + current_d,
                }
            else:
                tracker = _credited_transfer(transfers.get(ledger_key, {}), current_u, current_d)
            previous_u = tracker["raw_uploaded"]
            previous_d = tracker["raw_downloaded"]
            if current_u > previous_u:
                tracker["credited_uploaded"] += _credited_delta(
                    current_u - previous_u, multiplier_u
                )
            if current_d > previous_d:
                tracker["credited_downloaded"] += _credited_delta(
                    current_d - previous_d, multiplier_d
                )
            tracker["raw_uploaded"] = current_u
            tracker["raw_downloaded"] = current_d
            tracker["domain"] = domain
            transfers[ledger_key] = tracker
            row["raw_uploaded"] = current_u
            row["raw_downloaded"] = current_d
            row["uploaded"] = tracker["credited_uploaded"]
            row["downloaded"] = tracker["credited_downloaded"]
            row["carried_uploaded"] = tracker["credited_uploaded"] - current_u
            row["carried_downloaded"] = tracker["credited_downloaded"] - current_d

        state = {
            "version": STATE_VERSION,
            "transfers": transfers,
            "legacy_adjustments": adjustments,
        }
        _save_state_unlocked(state)
    return rows, adjustments, replace_keys, initialized_client_ids
