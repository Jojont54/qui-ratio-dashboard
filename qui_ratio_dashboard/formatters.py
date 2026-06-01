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


def ratio_margin(uploaded, downloaded, minimum_ratio):
    return int(uploaded / max(float(minimum_ratio), 0.01) - downloaded)


def ratio_status_class(ratio, minimum_ratio):
    threshold = max(float(minimum_ratio), 0.01)
    if ratio == math.inf or ratio >= threshold * 1.1:
        return "good"
    if ratio >= threshold:
        return "warn"
    return "danger"


def ratio_margin_class(value, warning_threshold=0):
    if value >= 0:
        return "warn" if value <= int(warning_threshold) else "good"
    return "danger"


def _credit_warning_threshold(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def aggregate_tracker_rows(
    domain_rows, legacy_adjustments=None, credit_warning_threshold=0
) -> list[dict]:
    credit_warning_threshold = _credit_warning_threshold(credit_warning_threshold)
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
        key = domain_to_key.get(key, key)
        current = aggregate.setdefault(
            key, {"uploaded": 0, "downloaded": 0, "total_size": 0, "count": 0}
        )
        current["uploaded"] += int(adjustment.get("uploaded", 0))
        current["downloaded"] += int(adjustment.get("downloaded", 0))

    for key, config in trackers.items():
        if key == "tracker_name":
            continue
        if int(config.get("uploaded_add", 0)) or int(config.get("downloaded_add", 0)):
            aggregate.setdefault(
                key, {"uploaded": 0, "downloaded": 0, "total_size": 0, "count": 0}
            )

    rows = []
    for key, totals in aggregate.items():
        config = trackers.get(key, {})
        manual_u = int(config.get("uploaded_add", 0))
        manual_d = int(config.get("downloaded_add", 0))
        displayed_u = totals["uploaded"] + manual_u
        displayed_d = totals["downloaded"] + manual_d
        ratio = (displayed_u / displayed_d) if displayed_d > 0 else math.inf
        minimum_ratio = float(config.get("minimum_ratio", 1))
        margin = ratio_margin(displayed_u, displayed_d, minimum_ratio)
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
                "ratio_margin": margin,
                "ratio_margin_class": ratio_margin_class(margin, credit_warning_threshold),
                "minimum_ratio": minimum_ratio,
                "ratio": ratio,
                "ratio_class": ratio_status_class(ratio, minimum_ratio),
                "count": totals["count"],
                "total_size": totals["total_size"],
            }
        )

    rows.sort(key=lambda row: row["ratio"] if row["ratio"] != math.inf else 1e99)
    return rows


def compute_tracker_rows(payload: dict) -> list[dict]:
    return aggregate_tracker_rows(compute_domain_rows(payload))
