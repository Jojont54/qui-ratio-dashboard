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
    state = load_state()
    trackers = state.setdefault("trackers", {})

    for r in rows:
        key = r["tracker"]

        cur_raw_u = int(r["uploaded"])
        cur_raw_d = int(r["downloaded"])

        t = trackers.setdefault(key, {
            "buf_u": 0,
            "buf_d": 0,
            "prev_raw_u": cur_raw_u,
            "prev_raw_d": cur_raw_d,
        })

        prev_raw_u = int(t.get("prev_raw_u", cur_raw_u))
        prev_raw_d = int(t.get("prev_raw_d", cur_raw_d))

        # Détection de baisse sur les RAW uniquement
        if cur_raw_u < prev_raw_u:
            t["buf_u"] = int(t.get("buf_u", 0)) + (prev_raw_u - cur_raw_u)

        if cur_raw_d < prev_raw_d:
            t["buf_d"] = int(t.get("buf_d", 0)) + (prev_raw_d - cur_raw_d)

        # Mise à jour du snapshot RAW uniquement
        t["prev_raw_u"] = cur_raw_u
        t["prev_raw_d"] = cur_raw_d

        # Valeurs affichées = RAW + BUFFER
        displayed_u = cur_raw_u + int(t["buf_u"])
        displayed_d = cur_raw_d + int(t["buf_d"])

        r["uploaded"] = displayed_u
        r["downloaded"] = displayed_d
        r["delta"] = displayed_u - displayed_d
        r["ratio"] = (displayed_u / displayed_d) if displayed_d > 0 else float("inf")

    save_state(state)
    return rows