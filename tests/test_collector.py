import unittest
import sys
import types
from unittest.mock import Mock, patch

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = Mock
    requests_stub.post = Mock()
    sys.modules["requests"] = requests_stub

from qui_ratio_dashboard import collector, direct_clients


class CollectorTests(unittest.TestCase):
    def test_torrents_are_grouped_into_the_existing_qui_summary_format(self):
        payload = collector.torrent_summary(
            [
                {
                    "hash": "a",
                    "tracker": "https://tracker.example/announce",
                    "uploaded": 100,
                    "downloaded": 50,
                    "total_size": 80,
                },
                {
                    "hash": "b",
                    "tracker": "udp://tracker.example:1337/announce",
                    "uploaded": 20,
                    "downloaded": 10,
                    "total_size": 30,
                },
            ]
        )

        transfer = payload["counts"]["trackerTransfers"]["tracker.example"]
        self.assertEqual(transfer["uploaded"], 120)
        self.assertEqual(transfer["downloaded"], 60)
        self.assertEqual(transfer["count"], 2)
        self.assertEqual(transfer["totalSize"], 110)

    def test_torrent_event_factors_are_applied_before_tracker_aggregation(self):
        payload = collector.torrent_summary(
            [
                {
                    "tracker": "tracker.example",
                    "uploaded": 100,
                    "downloaded": 50,
                    "upload_multiplier": 2,
                    "download_multiplier": 0,
                }
            ]
        )

        transfer = payload["counts"]["trackerTransfers"]["tracker.example"]
        self.assertEqual(transfer["uploaded"], 200)
        self.assertEqual(transfer["downloaded"], 0)

    def test_qbittorrent_pseudo_tracker_is_not_exposed_as_a_tracker(self):
        payload = collector.torrent_summary(
            [{"tracker": "** [DHT] **", "uploaded": 100, "downloaded": 50}]
        )

        self.assertEqual(payload["counts"]["trackerTransfers"], {})

    def test_future_freeleech_rule_can_be_applied_by_torrent_hash(self):
        payload = collector.torrent_summary(
            [{"hash": "ABC", "tracker": "tracker.example", "downloaded": 50}],
            {"abc": {"download_multiplier": 0}},
        )

        self.assertEqual(payload["counts"]["trackerTransfers"]["tracker.example"]["downloaded"], 0)

    def test_prowlarr_candidates_are_limited_to_the_collection_window(self):
        candidates = collector.recent_torrent_hashes(
            [
                {"hash": "new", "added_at": 980},
                {"hash": "old", "added_at": 800},
                {"hash": "undated"},
            ],
            60,
            current_time=1000,
        )

        self.assertEqual(candidates, {"new", "undated"})


class DirectClientTests(unittest.TestCase):
    def test_qbittorrent_client_builds_tracker_summary(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200, text="Ok.", raise_for_status=Mock())
        session.get.return_value = Mock(
            raise_for_status=Mock(),
            json=lambda: [
                {
                    "hash": "one",
                    "tracker": "https://qbit.example/announce",
                    "uploaded": 14,
                    "downloaded": 7,
                    "total_size": 20,
                    "added_on": 42,
                }
            ],
        )
        with patch.object(direct_clients.requests, "Session", return_value=session):
            payload = direct_clients.QBittorrentClient(
                "http://qbit", "user", "password"
            ).fetch_torrents_summary()

        self.assertEqual(payload["counts"]["trackerTransfers"]["qbit.example"]["uploaded"], 14)
        with patch.object(direct_clients.requests, "Session", return_value=session):
            transfers = direct_clients.QBittorrentClient(
                "http://qbit", "user", "password"
            ).fetch_torrents()
        self.assertEqual(transfers[0]["added_at"], 42)

    def test_transmission_client_handles_session_challenge_and_builds_summary(self):
        rejected = Mock(
            status_code=409,
            headers={"X-Transmission-Session-Id": "session"},
            raise_for_status=Mock(),
        )
        accepted = Mock(
            status_code=200,
            raise_for_status=Mock(),
            json=lambda: {
                "arguments": {
                    "torrents": [
                        {
                            "hashString": "one",
                            "uploadedEver": 14,
                            "downloadedEver": 7,
                            "totalSize": 20,
                            "addedDate": 42,
                            "trackers": [{"announce": "udp://transmission.example/announce"}],
                        }
                    ]
                }
            },
        )
        with patch.object(direct_clients.requests, "post", side_effect=[rejected, accepted]):
            payload = direct_clients.TransmissionClient("http://transmission").fetch_torrents_summary()

        self.assertEqual(
            payload["counts"]["trackerTransfers"]["transmission.example"]["downloaded"], 7
        )
        self.assertIn("addedDate", accepted.json()["arguments"]["torrents"][0])

    def test_deluge_client_builds_tracker_summary(self):
        session = Mock()
        session.post.side_effect = [
            Mock(raise_for_status=Mock(), json=lambda: {"result": True}),
            Mock(raise_for_status=Mock(), json=lambda: {"result": True}),
            Mock(
                raise_for_status=Mock(),
                json=lambda: {
                    "result": {
                        "one": {
                            "tracker_host": "deluge.example",
                            "total_uploaded": 14,
                            "total_done": 7,
                            "total_size": 20,
                            "time_added": 42,
                        }
                    }
                },
            ),
        ]
        with patch.object(direct_clients.requests, "Session", return_value=session):
            payload = direct_clients.DelugeClient("http://deluge", "password").fetch_torrents_summary()

        self.assertEqual(payload["counts"]["trackerTransfers"]["deluge.example"]["uploaded"], 14)

    def test_rtorrent_client_builds_tracker_summary(self):
        class FakeServer:
            def __getattr__(self, name):
                if name == "d.multicall2":
                    return lambda *_: [["one", 14, 7, 20]]
                if name == "t.multicall":
                    return lambda *_: [["udp://rtorrent.example/announce"]]
                raise AttributeError(name)

        with patch.object(direct_clients, "ServerProxy", return_value=FakeServer()):
            payload = direct_clients.RTorrentClient("http://rtorrent").fetch_torrents_summary()

        self.assertEqual(payload["counts"]["trackerTransfers"]["rtorrent.example"]["uploaded"], 14)


if __name__ == "__main__":
    unittest.main()
