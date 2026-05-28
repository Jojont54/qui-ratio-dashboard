import re

import requests

from .diagnostics import log_event, short_hash


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


def _is_special_rule(rule):
    return (
        float(rule.get("upload_multiplier", 1)) != 1
        or float(rule.get("download_multiplier", 1)) != 1
    )


class ProwlarrClient:
    def __init__(self, base_url, api_key, timeout=10.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = float(timeout)
        if not self.base_url or not self.api_key:
            raise RuntimeError("Adresse et clé API Prowlarr requises")
        self.headers = {"X-Api-Key": self.api_key}

    def _get(self, path, params=None):
        params = dict(params or {})
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )
        if getattr(response, "status_code", None) in {401, 403}:
            fallback_params = dict(params)
            fallback_params["apikey"] = self.api_key
            response = requests.get(
                f"{self.base_url}{path}",
                headers=self.headers,
                params=fallback_params,
                timeout=self.timeout,
            )
        return response

    def test_connection(self):
        response = self._get("/api/v1/system/status")
        try:
            response.raise_for_status()
        except requests.RequestException as error:
            status = getattr(response, "status_code", "")
            detail = f"HTTP {status}" if status else str(error)
            raise RuntimeError(f"Prowlarr ne répond pas correctement ({detail})") from error
        return True

    def torrent_rules(self, torrent_hashes):
        clean_hashes = [_normalized_hash(torrent_hash) for torrent_hash in torrent_hashes]
        response = self._get(
            "/api/v1/history",
            params={
                "page": 1,
                "pageSize": 250,
                "sortKey": "date",
                "sortDirection": "descending",
            },
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("records", payload) if isinstance(payload, dict) else payload
        requested = set(clean_hashes)
        rules_by_hash = {}
        for record in records or []:
            rule = rule_from_history_record(record)
            for torrent_hash in _hashes_from_record(record) & requested:
                current_rule = rules_by_hash.get(torrent_hash)
                if current_rule is None or (
                    not _is_special_rule(current_rule) and _is_special_rule(rule)
                ):
                    rules_by_hash[torrent_hash] = rule
                    log_event(
                        "prowlarr.history.match",
                        hash=short_hash(torrent_hash),
                        label=rule["label"],
                        upload_multiplier=rule["upload_multiplier"],
                        download_multiplier=rule["download_multiplier"],
                    )
        rules = {}
        for clean_hash in clean_hashes:
            rules[clean_hash] = (
                rules_by_hash[clean_hash]
                if clean_hash in rules_by_hash
                else {
                    "upload_multiplier": 1,
                    "download_multiplier": 1,
                    "label": "Non trouvé dans Prowlarr",
                    "source": "prowlarr-miss",
                }
            )
            rule = rules[clean_hash]
            log_event(
                "prowlarr.rule",
                hash=short_hash(clean_hash),
                source=rule["source"],
                label=rule["label"],
                upload_multiplier=rule["upload_multiplier"],
                download_multiplier=rule["download_multiplier"],
            )
        return rules

    def torrent_rule(self, torrent_hash):
        clean_hash = _normalized_hash(torrent_hash)
        return self.torrent_rules([clean_hash])[clean_hash]
