import json
import os
from threading import Lock

STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")

_lock = Lock()


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"trackers": {}}

    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"trackers": {}}


def save_state(state):
    with _lock:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)

def apply_ledger(rows):
    """
    rows: liste issue de compute_tracker_rows()
    Modifie les uploaded/downloaded en ajoutant le buffer.
    Détecte les resets automatiquement.
    """
    state = load_state()
    trackers = state.setdefault("trackers", {})

    for r in rows:
        key = r["tracker"]

        cur_u = r["uploaded"]
        cur_d = r["downloaded"]

        t = trackers.setdefault(key, {
            "buf_u": 0,
            "buf_d": 0,
            "prev_u": cur_u,
            "prev_d": cur_d,
        })

        # ---- RESET DETECTION ----
        if cur_u < t["prev_u"]:
            # compteur reset -> on ajoute tout le cycle précédent
            t["buf_u"] += t["prev_u"]

        if cur_d < t["prev_d"]:
            t["buf_d"] += t["prev_d"]

        # ---- UPDATE PREVIOUS ----
        t["prev_u"] = cur_u
        t["prev_d"] = cur_d

        # ---- APPLY BUFFER ----
        r["uploaded"] = cur_u + t["buf_u"]
        r["downloaded"] = cur_d + t["buf_d"]

        # recalcul delta et ratio
        if r["downloaded"] > 0:
            r["ratio"] = r["uploaded"] / r["downloaded"]
        else:
            r["ratio"] = float("inf")

        r["delta"] = r["uploaded"] - r["downloaded"]

    save_state(state)
    return rows