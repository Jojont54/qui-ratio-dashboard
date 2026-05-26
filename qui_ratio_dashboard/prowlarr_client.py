import re

import requests


def _normalized_hash(value):
    match = re.search(r"(?<![a-fA-F0-9])[a-fA-F0-9]{32,64}(?![a-fA-F0-9])", str(value or ""))
    return match.group(0).lower() if match else str(value or "").strip().lower()


def _values_for_keys(value, keys):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower().replace("_", "") in keys:
                found.append(child)
            found.extend(_values_for_keys(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_values_for_keys(child, keys))
    return found


def _float_value(values):
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _flag_text(record):
    values = _values_for_keys(record, {"indexerflags", "flags"})
    return " ".join(str(item) for value in values for item in (value if isinstance(value, list) else [value])).lower()


def _matches_hash(record, torrent_hash):
    candidates = _values_for_keys(
        record, {"downloadid", "infohash", "torrenthash", "downloadclientid", "hash"}
    )
    return any(_normalized_hash(value) == torrent_hash for value in candidates)


def _hashes_from_record(record):
    candidates = _values_for_keys(
        record, {"downloadid", "infohash", "torrenthash", "downloadclientid", "hash"}
    )
    return {_normalized_hash(value) for value in candidates}


def rule_from_history_record(record):
    upload = _float_value(_values_for_keys(record, {"uploadvolumefactor", "uploadfactor"}))
    download = _float_value(
        _values_for_keys(record, {"downloadvolumefactor", "downloadfactor"})
    )
    flags = _flag_text(record)
    labels = []
    if upload is None:
        upload = 2 if "doubleupload" in flags.replace(" ", "") else 1
    if download is None:
        compact_flags = flags.replace(" ", "")
        if "freeleech" in compact_flags:
            download = 0
        elif "halfleech" in compact_flags or "silverleech" in compact_flags:
            download = 0.5
        else:
            download = 1
    if upload != 1:
        labels.append(f"Upload x{upload:g}")
    if download == 0:
        labels.append("Freeleech")
    elif download != 1:
        labels.append(f"Download x{download:g}")
    return {
        "upload_multiplier": upload,
        "download_multiplier": download,
        "label": " + ".join(labels) or "Normal",
        "source": "prowlarr",
    }


class ProwlarrClient:
    def __init__(self, base_url, api_key, timeout=10.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = float(timeout)
        if not self.base_url or not self.api_key:
            raise RuntimeError("Adresse et cle API Prowlarr requises")
        self.headers = {"X-Api-Key": self.api_key}

    def test_connection(self):
        response = requests.get(
            f"{self.base_url}/api/v1/system/status",
            headers=self.headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return True

    def torrent_rules(self, torrent_hashes):
        clean_hashes = [_normalized_hash(torrent_hash) for torrent_hash in torrent_hashes]
        response = requests.get(
            f"{self.base_url}/api/v1/history",
            headers=self.headers,
            params={
                "page": 1,
                "pageSize": 250,
                "sortKey": "date",
                "sortDirection": "descending",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("records", payload) if isinstance(payload, dict) else payload
        requested = set(clean_hashes)
        records_by_hash = {}
        for record in records or []:
            for torrent_hash in _hashes_from_record(record) & requested:
                records_by_hash.setdefault(torrent_hash, record)
        rules = {}
        for clean_hash in clean_hashes:
            matching = records_by_hash.get(clean_hash)
            rules[clean_hash] = (
                rule_from_history_record(matching)
                if matching is not None
                else {
                    "upload_multiplier": 1,
                    "download_multiplier": 1,
                    "label": "Non trouve dans Prowlarr",
                    "source": "prowlarr-miss",
                }
            )
        return rules

    def torrent_rule(self, torrent_hash):
        clean_hash = _normalized_hash(torrent_hash)
        return self.torrent_rules([clean_hash])[clean_hash]
