import requests
from .config import QUI_BASE_URL, QUI_INSTANCE_ID, QUI_API_KEY, HTTP_TIMEOUT

class QuiClient:
    def __init__(self) -> None:
        if not QUI_BASE_URL:
            raise RuntimeError("QUI_BASE_URL is required")
        if not QUI_API_KEY:
            raise RuntimeError("QUI_API_KEY is required")
        self.headers = {"x-api-key": QUI_API_KEY}

    def fetch_torrents_summary(self) -> dict:
        url = (
            f"{QUI_BASE_URL}/api/instances/{QUI_INSTANCE_ID}/torrents"
            "?page=0&limit=1&sort=added_on&order=desc"
        )
        r = requests.get(url, headers=self.headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()

        data = r.json()
        if isinstance(data, list):
            if not data:
                raise RuntimeError("QUI returned an empty list")
            data = data[0]
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected QUI response format")
        return data
