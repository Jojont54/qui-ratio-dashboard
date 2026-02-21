import math
import os
import re
import yaml
from .config import BUFFERS_PATH
from .config import TRACKERS_PATH


_SUFFIX = {
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
    "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
}

def parse_bytes(v) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if not s:
        return 0
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]{2,4})$", s)
    if not m:
        try:
            return int(s)
        except:
            return 0
    num = float(m.group(1))
    suf = m.group(2).upper()
    return int(num * _SUFFIX.get(suf, 1))

def load_buffers() -> dict:
    if not os.path.exists(BUFFERS_PATH):
        return {}
    data = yaml.safe_load(open(BUFFERS_PATH, "r", encoding="utf-8")) or {}
    buf = data.get("buffers", {}) or {}
    out = {}
    for tracker, v in buf.items():
        v = v or {}
        out[tracker] = {
            "uploaded_add": parse_bytes(v.get("uploaded_add", 0)),
            "downloaded_add": parse_bytes(v.get("downloaded_add", 0)),
        }
    return out

def fmt_bytes(n: int) -> str:
    n = int(n or 0)

    sign = "-" if n < 0 else ""
    n = abs(n)

    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    v = float(n)
    i = 0

    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1

    return f"{sign}{v:.2f} {units[i]}"


def compute_tracker_rows(payload: dict) -> list[dict]:
    buffers = load_buffers()
    transfers = (((payload or {}).get("counts") or {}).get("trackerTransfers")) or {}

    rows = []
    for tracker_domain, s in transfers.items():
        up = int(s.get("uploaded", 0))
        dl = int(s.get("downloaded", 0))
        total_size = int(s.get("totalSize", 0))
        count = int(s.get("count", 0))

        b = buffers.get(tracker_domain, {"uploaded_add": 0, "downloaded_add": 0})
        up2 = up + b["uploaded_add"]
        dl2 = dl + b["downloaded_add"]

        if dl2 <= 0:
            ratio = math.inf if up2 > 0 else 0.0
        else:
            ratio = up2 / dl2

        rows.append({
            "tracker": tracker_domain,   # domaine uniquement (pas de passkey)
            "uploaded": up2,
            "downloaded": dl2,
            "ratio": ratio,
            "delta": up2 - dl2,
            "count": count,
            "total_size": total_size,
        })

    rows.sort(key=lambda r: (r["ratio"] if r["ratio"] != math.inf else 1e99))
    return rows

def load_tracker_map():
    if not os.path.exists(TRACKERS_PATH):
        return {}, {}
    data = yaml.safe_load(open(TRACKERS_PATH, "r", encoding="utf-8")) or {}
    trackers = (data.get("trackers") or {})

    domain_to_key = {}
    key_to_display = {}

    for key, cfg in trackers.items():
        key_to_display[key] = cfg.get("display", key)
        for d in (cfg.get("domains") or []):
            domain_to_key[d] = key

    return domain_to_key, key_to_display

def compute_tracker_rows(payload: dict) -> list[dict]:
    buffers = load_buffers()
    domain_to_key, key_to_display = load_tracker_map()

    transfers = (((payload or {}).get("counts") or {}).get("trackerTransfers")) or {}

    # aggregate by logical key
    agg = {}

    for domain, s in transfers.items():
        key = domain_to_key.get(domain, domain)  # if not mapped, keep domain as key
        up = int(s.get("uploaded", 0))
        dl = int(s.get("downloaded", 0))
        total_size = int(s.get("totalSize", 0))
        count = int(s.get("count", 0))

        if key not in agg:
            agg[key] = {"uploaded": 0, "downloaded": 0, "total_size": 0, "count": 0}

        agg[key]["uploaded"] += up
        agg[key]["downloaded"] += dl
        agg[key]["total_size"] += total_size
        agg[key]["count"] += count

    rows = []
    for key, a in agg.items():
        # buffers can be keyed either by logical key (recommended) or by domain fallback
        b = buffers.get(key, {"uploaded_add": 0, "downloaded_add": 0})

        up2 = a["uploaded"] + b["uploaded_add"]
        dl2 = a["downloaded"] + b["downloaded_add"]

        if dl2 <= 0:
            ratio = math.inf if up2 > 0 else 0.0
        else:
            ratio = up2 / dl2

        rows.append({
            "tracker": key_to_display.get(key, key),  # pretty name if available
            "uploaded": up2,
            "downloaded": dl2,
            "ratio": ratio,
            "delta": up2 - dl2,
            "count": a["count"],
            "total_size": a["total_size"],
            "_key": key,  # optional: internal key if you want JSON stable
        })

    rows.sort(key=lambda r: (r["ratio"] if r["ratio"] != math.inf else 1e99))
    return rows

