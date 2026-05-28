import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda _: {}
    sys.modules["yaml"] = yaml_stub

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.get = Mock()
    sys.modules["requests"] = requests_stub

from qui_ratio_dashboard import collector, database, formatters, prowlarr_client, state_store

if not hasattr(prowlarr_client.requests, "get"):
    prowlarr_client.requests.get = Mock()


class ProwlarrClientTests(unittest.TestCase):
    def test_freeleech_and_double_upload_flags_become_torrent_multipliers(self):
        record = {
            "data": {
                "downloadId": "A" * 40,
                "indexerFlags": ["FreeLeech", "DoubleUpload"],
            }
        }

        rule = prowlarr_client.rule_from_history_record(record)

        self.assertEqual(rule["download_multiplier"], 0)
        self.assertEqual(rule["upload_multiplier"], 2)
        self.assertIn("Freeleech", rule["label"])

    def test_explicit_volume_factors_are_used_when_available(self):
        rule = prowlarr_client.rule_from_history_record(
            {"data": {"downloadVolumeFactor": 0.5, "uploadVolumeFactor": 3}}
        )

        self.assertEqual(rule["download_multiplier"], 0.5)
        self.assertEqual(rule["upload_multiplier"], 3)

    def test_history_is_matched_to_the_new_torrent_hash(self):
        response = Mock(
            raise_for_status=Mock(),
            json=lambda: {
                "records": [
                    {"data": {"downloadId": "a" * 40, "indexerFlags": ["FreeLeech"]}},
                    {"data": {"downloadId": "b" * 40, "indexerFlags": ["DoubleUpload"]}},
                ]
            },
        )
        with patch.object(prowlarr_client.requests, "get", return_value=response):
            rules = prowlarr_client.ProwlarrClient(
                "http://prowlarr", "secret"
            ).torrent_rules(["A" * 40, "b" * 40, "c" * 40])

        self.assertEqual(rules["a" * 40]["download_multiplier"], 0)
        self.assertEqual(rules["b" * 40]["upload_multiplier"], 2)
        self.assertEqual(rules["c" * 40]["source"], "prowlarr-miss")

    def test_history_prefers_special_rule_over_newer_normal_record(self):
        response = Mock(
            raise_for_status=Mock(),
            json=lambda: {
                "records": [
                    {"data": {"downloadId": "a" * 40}},
                    {"data": {"downloadId": "a" * 40, "indexerFlags": ["FreeLeech"]}},
                ]
            },
        )
        with patch.object(prowlarr_client.requests, "get", return_value=response):
            rules = prowlarr_client.ProwlarrClient(
                "http://prowlarr", "secret"
            ).torrent_rules(["a" * 40])

        self.assertEqual(rules["a" * 40]["download_multiplier"], 0)
        self.assertEqual(rules["a" * 40]["label"], "Freeleech")

    def test_connection_falls_back_to_apikey_query_parameter(self):
        unauthorized = Mock(status_code=401, raise_for_status=Mock())
        response = Mock(status_code=200, raise_for_status=Mock())

        with patch.object(
            prowlarr_client.requests,
            "get",
            side_effect=[unauthorized, response],
        ) as get:
            self.assertTrue(
                prowlarr_client.ProwlarrClient(
                    "http://prowlarr", "secret"
                ).test_connection()
            )

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["params"]["apikey"], "secret")


class ProwlarrAccountingSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database_path = database.DATABASE_PATH
        self.previous_legacy_dirs = database.LEGACY_CONFIG_DIRECTORIES
        self.previous_state_path = state_store.STATE_PATH
        database.DATABASE_PATH = os.path.join(self.temp_dir.name, "dashboard.db")
        database.LEGACY_CONFIG_DIRECTORIES = (self.temp_dir.name,)
        state_store.STATE_PATH = os.path.join(self.temp_dir.name, "state.json")
        database.init_database()

    def tearDown(self):
        database.DATABASE_PATH = self.previous_database_path
        database.LEGACY_CONFIG_DIRECTORIES = self.previous_legacy_dirs
        state_store.STATE_PATH = self.previous_state_path
        self.temp_dir.cleanup()

    def refresh(self, transfers, rules=None):
        rows = collector.torrent_rows(transfers, rules)
        for row in rows:
            row["previous_ledger_key"] = f"client:1:{row['domain']}"
            row["ledger_key"] = f"client:1:torrent:{row['hash']}"
            row["client_id"] = 1
        mapping, settings = database.load_tracker_configuration(
            row["domain"] for row in rows
        )
        rows, legacy, _, _ = state_store.apply_domain_ledger(rows, mapping, settings)
        return formatters.aggregate_tracker_rows(rows, legacy)[0]

    def test_prowlarr_freeleech_double_upload_remains_coherent_after_removal(self):
        response = Mock(
            raise_for_status=Mock(),
            json=lambda: {
                "records": [
                    {
                        "data": {
                            "downloadId": "a" * 40,
                            "indexerFlags": ["FreeLeech", "DoubleUpload"],
                        }
                    }
                ]
            },
        )
        with patch.object(prowlarr_client.requests, "get", return_value=response):
            rules = prowlarr_client.ProwlarrClient(
                "http://prowlarr", "secret"
            ).torrent_rules(["a" * 40])

        started = self.refresh(
            [
                {
                    "hash": "a" * 40,
                    "tracker": "tracker.example",
                    "uploaded": 10,
                    "downloaded": 100,
                    "total_size": 1000,
                }
            ],
            rules,
        )
        progressed = self.refresh(
            [
                {
                    "hash": "a" * 40,
                    "tracker": "tracker.example",
                    "uploaded": 15,
                    "downloaded": 150,
                    "total_size": 1000,
                }
            ],
            rules,
        )
        removed = self.refresh([], rules)

        self.assertEqual((started["uploaded"], started["downloaded"]), (20, 0))
        self.assertEqual((progressed["uploaded"], progressed["downloaded"]), (30, 0))
        self.assertEqual((removed["uploaded"], removed["downloaded"]), (30, 0))
        self.assertEqual(removed["count"], 0)

    def test_new_torrent_is_not_lost_when_another_is_removed_on_same_tracker(self):
        first = self.refresh(
            [
                {
                    "hash": "old",
                    "tracker": "tracker.example",
                    "uploaded": 50,
                    "downloaded": 100,
                }
            ]
        )
        replaced = self.refresh(
            [
                {
                    "hash": "new",
                    "tracker": "tracker.example",
                    "uploaded": 10,
                    "downloaded": 30,
                }
            ]
        )
        progressed = self.refresh(
            [
                {
                    "hash": "new",
                    "tracker": "tracker.example",
                    "uploaded": 12,
                    "downloaded": 40,
                }
            ]
        )

        self.assertEqual((first["uploaded"], first["downloaded"]), (50, 100))
        self.assertEqual((replaced["uploaded"], replaced["downloaded"]), (60, 130))
        self.assertEqual((progressed["uploaded"], progressed["downloaded"]), (62, 140))
        self.assertEqual(progressed["count"], 1)

    def test_existing_aggregated_client_history_is_not_counted_again_after_upgrade(self):
        old_row = {
            "tracker": "tracker.example",
            "_key": "tracker.example",
            "domain": "tracker.example",
            "ledger_key": "client:1:tracker.example",
            "client_id": 1,
            "uploaded": 50,
            "downloaded": 100,
            "count": 1,
            "total_size": 1000,
        }
        mapping, settings = database.load_tracker_configuration(["tracker.example"])
        state_store.apply_domain_ledger([old_row], mapping, settings)

        upgraded = self.refresh(
            [
                {
                    "hash": "existing",
                    "tracker": "tracker.example",
                    "uploaded": 50,
                    "downloaded": 100,
                    "total_size": 1000,
                }
            ]
        )
        added_after_upgrade = self.refresh(
            [
                {
                    "hash": "existing",
                    "tracker": "tracker.example",
                    "uploaded": 50,
                    "downloaded": 100,
                    "total_size": 1000,
                },
                {
                    "hash": "new-after-upgrade",
                    "tracker": "tracker.example",
                    "uploaded": 10,
                    "downloaded": 30,
                    "total_size": 200,
                },
            ]
        )

        self.assertEqual((upgraded["uploaded"], upgraded["downloaded"]), (50, 100))
        self.assertEqual(upgraded["count"], 1)
        self.assertEqual(
            (added_after_upgrade["uploaded"], added_after_upgrade["downloaded"]),
            (60, 130),
        )
        self.assertEqual(added_after_upgrade["count"], 2)


if __name__ == "__main__":
    unittest.main()
