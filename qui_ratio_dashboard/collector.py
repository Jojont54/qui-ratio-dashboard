from urllib.parse import urlsplit
from time import time


def tracker_domain(value):
    text = str(value or "").strip()
    if not text or text.startswith("**"):
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    return (parsed.hostname or text.split("/")[0]).lower()


def recent_torrent_hashes(transfers, max_age_seconds, current_time=None):
    cutoff = (current_time if current_time is not None else time()) - float(max_age_seconds)
    hashes = set()
    for transfer in transfers:
        torrent_hash = str(transfer.get("hash", "")).strip().lower()
        if not torrent_hash:
            continue
        added_at = transfer.get("added_at")
        try:
            added_at = float(added_at)
        except (TypeError, ValueError):
            hashes.add(torrent_hash)
            continue
        if added_at >= cutoff:
            hashes.add(torrent_hash)
    return hashes


def prowlarr_lookup_hashes(hashes, rules_by_hash, recent_hashes, max_attempts=3):
    existing = rules_by_hash or {}
    recent = set(recent_hashes or set())
    checks = {
        torrent_hash
        for torrent_hash in hashes
        if torrent_hash in recent and torrent_hash not in existing
    }
    checks.update(
        torrent_hash
        for torrent_hash, rule in existing.items()
        if torrent_hash in recent
        and rule.get("source") in {"detected", "prowlarr-miss"}
        and int(rule.get("lookup_attempts", 0)) < max_attempts
    )
    return sorted(checks)


def torrent_rows(transfers, rules_by_hash=None, apply_multipliers=False):
    rules_by_hash = rules_by_hash or {}
    rows = []
    for transfer in transfers:
        domain = tracker_domain(transfer.get("tracker"))
        if not domain:
            continue
        torrent_hash = str(transfer.get("hash", "")).strip().lower()
        rule = rules_by_hash.get(torrent_hash, {})
        uploaded = int(transfer.get("uploaded", 0))
        downloaded = int(transfer.get("downloaded", 0))
        upload_multiplier = float(transfer.get("upload_multiplier", rule.get("upload_multiplier", 1)))
        download_multiplier = float(
            transfer.get("download_multiplier", rule.get("download_multiplier", 1))
        )
        credited_uploaded = int(round(uploaded * upload_multiplier)) if apply_multipliers else uploaded
        credited_downloaded = (
            int(round(downloaded * download_multiplier)) if apply_multipliers else downloaded
        )
        rows.append(
            {
                "tracker": domain,
                "_key": domain,
                "domain": domain,
                "hash": torrent_hash,
                "uploaded": credited_uploaded,
                "downloaded": credited_downloaded,
                "upload_multiplier": upload_multiplier,
                "download_multiplier": download_multiplier,
                "manual_buffer_uploaded": 0,
                "manual_buffer_downloaded": 0,
                "count": 1,
                "total_size": int(transfer.get("total_size", 0)),
            }
        )
    return rows


def torrent_summary(transfers, rules_by_hash=None):
    aggregated = {}
    for row in torrent_rows(transfers, rules_by_hash, apply_multipliers=True):
        domain = row["domain"]
        totals = aggregated.setdefault(
            domain,
            {"uploaded": 0, "downloaded": 0, "count": 0, "totalSize": 0},
        )
        totals["uploaded"] += row["uploaded"]
        totals["downloaded"] += row["downloaded"]
        totals["count"] += row["count"]
        totals["totalSize"] += row["total_size"]
    return {"counts": {"trackerTransfers": aggregated}}
