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

        cur_raw_u = int(r["uploaded"])
        cur_raw_d = int(r["downloaded"])

        t = trackers.setdefault(key, {
            "prev_raw_u": cur_raw_u,
            "prev_raw_d": cur_raw_d,
        })

        prev_raw_u = int(t.get("prev_raw_u", cur_raw_u))
        prev_raw_d = int(t.get("prev_raw_d", cur_raw_d))

        # On ne laisse jamais une baisse écraser l'historique
        effective_u = max(cur_raw_u, prev_raw_u)
        effective_d = max(cur_raw_d, prev_raw_d)

        # On ne sauvegarde que si ça monte
        if cur_raw_u > prev_raw_u:
            t["prev_raw_u"] = cur_raw_u

        if cur_raw_d > prev_raw_d:
            t["prev_raw_d"] = cur_raw_d

        r["raw_uploaded"] = cur_raw_u
        r["raw_downloaded"] = cur_raw_d

        r["uploaded"] = effective_u
        r["downloaded"] = effective_d
        r["delta"] = effective_u - effective_d
        r["ratio"] = (effective_u / effective_d) if effective_d > 0 else float("inf")

    save_state(state)
    return rows