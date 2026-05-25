import math

from .database import load_tracker_configuration
from .units import fmt_bytes


def compute_domain_rows(payload: dict) -> list[dict]:
    transfers = (((payload or {}).get("counts") or {}).get("trackerTransfers")) or {}
    rows = []
    for domain, transfer in transfers.items():
        rows.append(
            {
                "tracker": domain,
                "_key": domain,
                "domain": domain,
                "uploaded": int(transfer.get("uploaded", 0)),
                "downloaded": int(transfer.get("downloaded", 0)),
                "manual_buffer_uploaded": 0,
                "manual_buffer_downloaded": 0,
                "count": int(transfer.get("count", 0)),
                "total_size": int(transfer.get("totalSize", 0)),
            }
        )
    return rows


def combine_domain_rows(rows: list[dict]) -> list[dict]:
    combined = {}
    for row in rows:
        domain = row["domain"]
        current = combined.setdefault(
            domain,
            {
                "tracker": domain,
                "_key": domain,
                "domain": domain,
                "uploaded": 0,
                "downloaded": 0,
                "manual_buffer_uploaded": 0,
                "manual_buffer_downloaded": 0,
                "count": 0,
                "total_size": 0,
            },
        )
        current["uploaded"] += int(row["uploaded"])
        current["downloaded"] += int(row["downloaded"])
        current["count"] += int(row.get("count", 0))
        current["total_size"] += int(row.get("total_size", 0))
    return list(combined.values())


def aggregate_tracker_rows(domain_rows, legacy_adjustments=None) -> list[dict]:
    legacy_adjustments = legacy_adjustments or {}
    domain_to_key, trackers = load_tracker_configuration(
        row.get("domain", row["_key"]) for row in domain_rows
    )
    aggregate = {}

    for row in domain_rows:
        domain = row.get("domain", row["_key"])
        key = domain_to_key.get(domain, domain)
        current = aggregate.setdefault(
            key, {"uploaded": 0, "downloaded": 0, "total_size": 0, "count": 0}
        )
        current["uploaded"] += int(row["uploaded"])
        current["downloaded"] += int(row["downloaded"])
        current["total_size"] += int(row.get("total_size", 0))
        current["count"] += int(row.get("count", 0))

    for key, adjustment in legacy_adjustments.items():
        if key == "tracker_name":
            continue
        current = aggregate.setdefault(
            key, {"uploaded": 0, "downloaded": 0, "total_size": 0, "count": 0}
        )
        current["uploaded"] += int(adjustment.get("uploaded", 0))
        current["downloaded"] += int(adjustment.get("downloaded", 0))

    rows = []
    for key, totals in aggregate.items():
        config = trackers.get(key, {})
        manual_u = int(config.get("uploaded_add", 0))
        manual_d = int(config.get("downloaded_add", 0))
        displayed_u = totals["uploaded"] + manual_u
        displayed_d = totals["downloaded"] + manual_d
        ratio = (displayed_u / displayed_d) if displayed_d > 0 else math.inf
        rows.append(
            {
                "tracker": config.get("display", key),
                "_key": key,
                "dashboard_visible": config.get("visible_dashboard", True),
                "widget_visible": config.get("visible_widget", True),
                "web_visible": config.get("visible_widget", True),
                "uploaded": displayed_u,
                "downloaded": displayed_d,
                "manual_buffer_uploaded": manual_u,
                "manual_buffer_downloaded": manual_d,
                "delta": displayed_u - displayed_d,
                "ratio": ratio,
                "count": totals["count"],
                "total_size": totals["total_size"],
            }
        )

    rows.sort(key=lambda row: row["ratio"] if row["ratio"] != math.inf else 1e99)
    return rows


def compute_tracker_rows(payload: dict) -> list[dict]:
    return aggregate_tracker_rows(compute_domain_rows(payload))
