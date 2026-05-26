from urllib.parse import urlsplit


def tracker_domain(value):
    text = str(value or "").strip()
    if not text or text.startswith("**"):
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    return (parsed.hostname or text.split("/")[0]).lower()


def torrent_summary(transfers, rules_by_hash=None):
    rules_by_hash = rules_by_hash or {}
    aggregated = {}
    for transfer in transfers:
        domain = tracker_domain(transfer.get("tracker"))
        if not domain:
            continue
        rule = rules_by_hash.get(str(transfer.get("hash", "")).lower(), {})
        uploaded = int(transfer.get("uploaded", 0))
        downloaded = int(transfer.get("downloaded", 0))
        upload_multiplier = float(transfer.get("upload_multiplier", rule.get("upload_multiplier", 1)))
        download_multiplier = float(
            transfer.get("download_multiplier", rule.get("download_multiplier", 1))
        )
        totals = aggregated.setdefault(
            domain,
            {"uploaded": 0, "downloaded": 0, "count": 0, "totalSize": 0},
        )
        totals["uploaded"] += int(round(uploaded * upload_multiplier))
        totals["downloaded"] += int(round(downloaded * download_multiplier))
        totals["count"] += 1
        totals["totalSize"] += int(transfer.get("total_size", 0))
    return {"counts": {"trackerTransfers": aggregated}}
