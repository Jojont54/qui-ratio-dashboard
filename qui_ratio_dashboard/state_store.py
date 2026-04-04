import json
import os
from threading import Lock

STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")

_lock = Lock()


def _ensure_state_file():
    directory = os.path.dirname(STATE_PATH)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    if not os.path.exists(STATE_PATH):
        with open(STATE_PATH, "w") as f:
            json.dump({"trackers": {}}, f, indent=2)


def load_state():
    _ensure_state_file()
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"trackers": {}}


def save_state(state):
    _ensure_state_file()
    with _lock:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)


def apply_state_floor(rows):
    state = load_state()
    trackers = state.setdefault("trackers", {})

    for r in rows:
        key = r["_key"] if "_key" in r else r["tracker"]

        # API brute uniquement
        cur_api_u = int(r["uploaded"])
        cur_api_d = int(r["downloaded"])

        # Buffer manuel fixe
        manual_u = int(r.get("manual_buffer_uploaded", 0))
        manual_d = int(r.get("manual_buffer_downloaded", 0))

        t = trackers.setdefault(key, {
            "prev_raw_u": cur_api_u,
            "prev_raw_d": cur_api_d,
        })

        prev_raw_u = int(t.get("prev_raw_u", cur_api_u))
        prev_raw_d = int(t.get("prev_raw_d", cur_api_d))

        # L'historique dépend UNIQUEMENT de l'API brute
        effective_raw_u = max(cur_api_u, prev_raw_u)
        effective_raw_d = max(cur_api_d, prev_raw_d)

        # On ne sauvegarde que les hausses API
        if cur_api_u > prev_raw_u:
            t["prev_raw_u"] = cur_api_u

        if cur_api_d > prev_raw_d:
            t["prev_raw_d"] = cur_api_d

        # Debug / transparence interne API
        r["raw_uploaded"] = cur_api_u
        r["raw_downloaded"] = cur_api_d
        r["floor_uploaded"] = effective_raw_u
        r["floor_downloaded"] = effective_raw_d

        # Le buffer manuel s'applique APRES, sans toucher au state
        displayed_u = effective_raw_u + manual_u
        displayed_d = effective_raw_d + manual_d

        r["uploaded"] = displayed_u
        r["downloaded"] = displayed_d
        r["delta"] = displayed_u - displayed_d
        r["ratio"] = (displayed_u / displayed_d) if displayed_d > 0 else float("inf")

    save_state(state)

    # Tri final sur le vrai ratio affiché
    rows.sort(key=lambda r: (r["ratio"] if r["ratio"] != float("inf") else 1e99))
    return rows