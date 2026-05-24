import json
import os
from threading import Lock

STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")
STATE_VERSION = 2

_lock = Lock()


def _empty_state():
    return {"version": STATE_VERSION, "trackers": {}}


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


def load_state():
    with _lock:
        return _load_state_unlocked()


def save_state(state):
    with _lock:
        _save_state_unlocked(state)


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


def apply_state_ledger(rows):
    with _lock:
        state = _load_state_unlocked()
        state["version"] = STATE_VERSION
        trackers = state.setdefault("trackers", {})

        for r in rows:
            key = r["_key"] if "_key" in r else r["tracker"]
            current_u = int(r["uploaded"])
            current_d = int(r["downloaded"])
            manual_u = int(r.get("manual_buffer_uploaded", 0))
            manual_d = int(r.get("manual_buffer_downloaded", 0))

            tracker = _migrate_tracker_state(trackers.get(key, {}), current_u, current_d)
            previous_u = tracker["raw_uploaded"]
            previous_d = tracker["raw_downloaded"]

            if current_u < previous_u:
                tracker["carried_uploaded"] += previous_u - current_u
            if current_d < previous_d:
                tracker["carried_downloaded"] += previous_d - current_d

            tracker["raw_uploaded"] = current_u
            tracker["raw_downloaded"] = current_d
            trackers[key] = tracker

            tracked_u = current_u + tracker["carried_uploaded"]
            tracked_d = current_d + tracker["carried_downloaded"]
            displayed_u = tracked_u + manual_u
            displayed_d = tracked_d + manual_d

            r["raw_uploaded"] = current_u
            r["raw_downloaded"] = current_d
            r["tracked_uploaded"] = tracked_u
            r["tracked_downloaded"] = tracked_d
            r["floor_uploaded"] = tracked_u
            r["floor_downloaded"] = tracked_d
            r["carried_uploaded"] = tracker["carried_uploaded"]
            r["carried_downloaded"] = tracker["carried_downloaded"]
            r["uploaded"] = displayed_u
            r["downloaded"] = displayed_d
            r["delta"] = displayed_u - displayed_d
            r["ratio"] = (displayed_u / displayed_d) if displayed_d > 0 else float("inf")

        _save_state_unlocked(state)

    rows.sort(key=lambda r: (r["ratio"] if r["ratio"] != float("inf") else 1e99))
    return rows
