from urllib.parse import quote, urlsplit, urlunsplit
from xmlrpc.client import ServerProxy

import requests

from .collector import torrent_summary
from .qui_client import QuiClient


def _first_tracker(trackers):
    for tracker in trackers or []:
        if isinstance(tracker, (list, tuple)):
            value = tracker[0] if tracker else ""
        elif isinstance(tracker, dict):
            value = tracker.get("announce") or tracker.get("url")
        else:
            value = tracker
        if value:
            return value
    return ""


class QBittorrentClient:
    def __init__(self, base_url, username="", password="", timeout=10.0):
        self.base_url = str(base_url).rstrip("/")
        self.username = str(username)
        self.password = str(password)
        self.timeout = float(timeout)

    def fetch_torrents(self):
        session = requests.Session()
        if self.username or self.password:
            response = session.post(
                f"{self.base_url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
            response.raise_for_status()
            if response.text.strip().lower().startswith("fail"):
                raise RuntimeError("Authentification qBittorrent refusee")
        response = session.get(f"{self.base_url}/api/v2/torrents/info", timeout=self.timeout)
        response.raise_for_status()
        return [
            {
                "hash": torrent.get("hash", ""),
                "tracker": torrent.get("tracker", ""),
                "uploaded": torrent.get("uploaded", 0),
                "downloaded": torrent.get("downloaded", 0),
                "total_size": torrent.get("total_size", torrent.get("size", 0)),
                "added_at": torrent.get("added_on"),
            }
            for torrent in response.json()
        ]

    def fetch_torrents_summary(self, rules_by_hash=None):
        return torrent_summary(self.fetch_torrents(), rules_by_hash)


class TransmissionClient:
    def __init__(self, base_url, username="", password="", rpc_path="/transmission/rpc", timeout=10.0):
        self.url = f"{str(base_url).rstrip('/')}/{str(rpc_path).strip('/')}"
        self.auth = (str(username), str(password)) if username or password else None
        self.timeout = float(timeout)
        self.session_id = ""

    def _post(self, body):
        headers = {}
        if self.session_id:
            headers["X-Transmission-Session-Id"] = self.session_id
        response = requests.post(
            self.url, json=body, auth=self.auth, headers=headers, timeout=self.timeout
        )
        if response.status_code == 409:
            self.session_id = response.headers.get("X-Transmission-Session-Id", "")
            headers["X-Transmission-Session-Id"] = self.session_id
            response = requests.post(
                self.url, json=body, auth=self.auth, headers=headers, timeout=self.timeout
            )
        response.raise_for_status()
        return response.json()

    def fetch_torrents(self):
        data = self._post(
            {
                "method": "torrent-get",
                "arguments": {
                    "fields": [
                        "hashString",
                        "uploadedEver",
                        "downloadedEver",
                        "totalSize",
                        "addedDate",
                        "trackers",
                    ]
                },
            }
        )
        transfers = []
        for torrent in data.get("arguments", {}).get("torrents", []):
            transfers.append(
                {
                    "hash": torrent.get("hashString", torrent.get("hash_string", "")),
                    "tracker": _first_tracker(torrent.get("trackers")),
                    "uploaded": torrent.get("uploadedEver", torrent.get("uploaded_ever", 0)),
                    "downloaded": torrent.get(
                        "downloadedEver", torrent.get("downloaded_ever", 0)
                    ),
                    "total_size": torrent.get("totalSize", torrent.get("total_size", 0)),
                    "added_at": torrent.get("addedDate", torrent.get("added_date")),
                }
            )
        return transfers

    def fetch_torrents_summary(self, rules_by_hash=None):
        return torrent_summary(self.fetch_torrents(), rules_by_hash)


class DelugeClient:
    def __init__(self, base_url, password="", daemon_id="", timeout=10.0):
        self.base_url = str(base_url).rstrip("/")
        self.password = str(password)
        self.daemon_id = str(daemon_id).strip()
        self.timeout = float(timeout)
        self.session = requests.Session()
        self.request_id = 0

    def _call(self, method, params=None):
        self.request_id += 1
        response = self.session.post(
            f"{self.base_url}/json",
            json={"method": method, "params": params or [], "id": self.request_id},
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result.get("result")

    def fetch_torrents(self):
        if not self._call("auth.login", [self.password]):
            raise RuntimeError("Authentification Deluge refusee")
        if not self._call("web.connected"):
            if not self.daemon_id:
                raise RuntimeError("Aucun daemon Deluge connecté")
            self._call("web.connect", [self.daemon_id])
        data = self._call(
            "core.get_torrents_status",
            [
                {},
                [
                    "hash",
                    "tracker",
                    "tracker_host",
                    "total_uploaded",
                    "total_done",
                    "total_size",
                    "time_added",
                ],
            ],
        ) or {}
        return [
            {
                "hash": status.get("hash", torrent_hash),
                "tracker": status.get("tracker_host") or status.get("tracker", ""),
                "uploaded": status.get("total_uploaded", 0),
                "downloaded": status.get("total_done", 0),
                "total_size": status.get("total_size", 0),
                "added_at": status.get("time_added"),
            }
            for torrent_hash, status in data.items()
        ]

    def fetch_torrents_summary(self, rules_by_hash=None):
        return torrent_summary(self.fetch_torrents(), rules_by_hash)


class RTorrentClient:
    def __init__(self, base_url, username="", password="", rpc_path="/RPC2", timeout=10.0):
        parsed = urlsplit(f"{str(base_url).rstrip('/')}/{str(rpc_path).strip('/')}")
        if username or password:
            credentials = f"{quote(str(username), safe='')}:{quote(str(password), safe='')}@"
            parsed = parsed._replace(netloc=credentials + parsed.netloc)
        self.server = ServerProxy(urlunsplit(parsed), allow_none=True)

    def fetch_torrents(self):
        rows = getattr(self.server, "d.multicall2")(
            "",
            "main",
            "d.hash=",
            "d.up.total=",
            "d.down.total=",
            "d.size_bytes=",
        )
        transfers = []
        for torrent_hash, uploaded, downloaded, total_size in rows:
            trackers = getattr(self.server, "t.multicall")(torrent_hash, "", "t.url=")
            transfers.append(
                {
                    "hash": torrent_hash,
                    "tracker": _first_tracker(trackers),
                    "uploaded": uploaded,
                    "downloaded": downloaded,
                    "total_size": total_size,
                }
            )
        return transfers

    def fetch_torrents_summary(self, rules_by_hash=None):
        return torrent_summary(self.fetch_torrents(), rules_by_hash)


def configured_client(configuration, timeout):
    client_type = str(configuration.get("client_type", "QUI")).upper()
    if client_type == "QUI":
        return QuiClient(
            configuration["base_url"],
            configuration["api_key"],
            configuration["instance_id"],
            timeout,
        )
    if client_type == "QBITTORRENT":
        return QBittorrentClient(
            configuration["base_url"], configuration["username"], configuration["password"], timeout
        )
    if client_type == "TRANSMISSION":
        return TransmissionClient(
            configuration["base_url"],
            configuration["username"],
            configuration["password"],
            configuration["rpc_path"] or "/transmission/rpc",
            timeout,
        )
    if client_type == "DELUGE":
        return DelugeClient(
            configuration["base_url"], configuration["password"], configuration["instance_id"], timeout
        )
    if client_type == "RTORRENT":
        return RTorrentClient(
            configuration["base_url"],
            configuration["username"],
            configuration["password"],
            configuration["rpc_path"] or "/RPC2",
            timeout,
        )
    raise RuntimeError(f"Client torrent non supporte : {client_type}")
