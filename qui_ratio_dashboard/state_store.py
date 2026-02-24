import json
import os
from threading import Lock

STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")

_lock = Lock()


def _ensure_state_file():
    directory = os.path.dirname(STATE_PATH)

    # crée le dossier si nécessaire
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    # crée le fichier si absent
    if not os.path.exists(STATE_PATH):
        with open(STATE_PATH, "w") as f:
            json.dump({"trackers": {}}, f, indent=2)


def load_state():
    _ensure_state_file()

    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        # si fichier corrompu → reset propre
        return {"trackers": {}}


def save_state(state):
    _ensure_state_file()

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

        # ---- RESET / BAISSE DETECTION ----
        if cur_u < t["prev_u"]:
            t["buf_u"] += (t["prev_u"] - cur_u)

        if cur_d < t["prev_d"]:
            t["buf_d"] += (t["prev_d"] - cur_d)

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