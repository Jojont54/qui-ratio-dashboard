import requests

class QuiClient:
    def __init__(self, base_url, api_key, instance_id="1", timeout=10.0) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.api_key = str(api_key)
        self.instance_id = str(instance_id)
        self.timeout = float(timeout)
        if not self.base_url:
            raise RuntimeError("Adresse QUI requise")
        if not self.api_key:
            raise RuntimeError("Cle API QUI requise")
        self.headers = {"x-api-key": self.api_key}

    def fetch_torrents_summary(self) -> dict:
        url = (
            f"{self.base_url}/api/instances/{self.instance_id}/torrents"
            "?page=0&limit=1&sort=added_on&order=desc"
        )
        r = requests.get(url, headers=self.headers, timeout=self.timeout)
        r.raise_for_status()

        data = r.json()
        if isinstance(data, list):
            if not data:
                raise RuntimeError("QUI returned an empty list")
            data = data[0]
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected QUI response format")
        return data

    def list_instances(self) -> list[dict]:
        response = requests.get(
            f"{self.base_url}/api/instances",
            headers=self.headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("Unexpected QUI instances response format")
        return [
            {
                "id": str(instance.get("id", "")),
                "name": str(instance.get("name") or f"Instance {instance.get('id', '')}"),
            }
            for instance in data
            if instance.get("id") is not None
        ]
