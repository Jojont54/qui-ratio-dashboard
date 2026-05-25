import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda _: {}
    sys.modules["yaml"] = yaml_stub

from qui_ratio_dashboard import database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database_path = database.DATABASE_PATH
        self.previous_trackers_path = database.TRACKERS_PATH
        self.previous_buffers_path = database.BUFFERS_PATH
        self.previous_loader = database._safe_load_yaml
        self.previous_now = database._now_utc
        database.DATABASE_PATH = os.path.join(self.temp_dir.name, "dashboard.db")
        database.TRACKERS_PATH = "trackers.yml"
        database.BUFFERS_PATH = "buffers.yml"

    def tearDown(self):
        database.DATABASE_PATH = self.previous_database_path
        database.TRACKERS_PATH = self.previous_trackers_path
        database.BUFFERS_PATH = self.previous_buffers_path
        database._safe_load_yaml = self.previous_loader
        database._now_utc = self.previous_now
        self.temp_dir.cleanup()

    def test_legacy_yaml_is_imported_only_once(self):
        contents = {
            "trackers.yml": {
                "trackers": {
                    "ygg": {
                        "display": "YGG",
                        "visible": False,
                        "domains": ["tracker.ygg.example"],
                    }
                }
            },
            "buffers.yml": {
                "buffers": {
                    "ygg": {"uploaded_add": "10 TiB", "downloaded_add": "2 TiB"}
                }
            },
        }
        database._safe_load_yaml = lambda path: contents.get(path, {})
        database.init_database()

        _, config = database.load_tracker_configuration()
        self.assertEqual(config["ygg"]["display"], "YGG")
        self.assertFalse(config["ygg"]["visible_widget"])
        self.assertEqual(config["ygg"]["uploaded_add"], 10 * 1024**4)
        self.assertEqual(database.list_trackers()[0]["buffer_uploaded_text"], "10 TiB")

        contents["buffers.yml"]["buffers"]["ygg"]["uploaded_add"] = "99 TiB"
        database.init_database()
        _, config = database.load_tracker_configuration()
        self.assertEqual(config["ygg"]["uploaded_add"], 10 * 1024**4)

    def test_discovered_domain_becomes_manageable_tracker(self):
        database._safe_load_yaml = lambda path: {}
        _, config = database.load_tracker_configuration(["new.tracker.example"])

        self.assertIn("new.tracker.example", config)
        self.assertEqual(database.list_trackers()[0]["domains"], ["new.tracker.example"])

    def test_selected_domains_can_be_grouped_and_empty_discovered_entries_removed(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example", "two.example"])

        database.group_domains(["one.example", "two.example"], "My Tracker")

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["display_name"], "My Tracker")
        self.assertEqual(trackers[0]["domains"], ["one.example", "two.example"])

    def test_settings_update_keeps_existing_domain_links(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example"])
        database.group_domains(["one.example"], "My Tracker")
        tracker = database.list_trackers()[0]

        database.update_trackers(
            {
                tracker["key"]: {
                    "display_name": "Renamed",
                    "visible_dashboard": False,
                    "visible_widget": True,
                    "uploaded_add": "2 TiB",
                    "downloaded_add": "1 TiB",
                    "event_uploaded_multiplier": "2",
                    "event_downloaded_multiplier": "0.5",
                }
            }
        )

        updated = database.list_trackers()[0]
        self.assertEqual(updated["display_name"], "Renamed")
        self.assertEqual(updated["domains"], ["one.example"])
        self.assertEqual(updated["event_uploaded_multiplier"], 2)
        self.assertEqual(updated["event_downloaded_multiplier"], 0.5)

    def test_linking_all_domains_to_new_name_renames_existing_group(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example", "two.example"])
        database.group_domains(["one.example", "two.example"], "Old Name")
        tracker = database.list_trackers()[0]
        database.update_trackers(
            {
                tracker["key"]: {
                    "display_name": "Old Name",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "5 TiB",
                    "downloaded_add": "1 TiB",
                }
            }
        )

        database.group_domains(["one.example", "two.example"], "New Name")

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["key"], tracker["key"])
        self.assertEqual(trackers[0]["display_name"], "New Name")
        self.assertEqual(trackers[0]["buffer_uploaded_text"], "5 TiB")

    def test_merging_existing_groups_preserves_their_buffers(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example", "two.example"])
        database.group_domains(["one.example"], "First")
        database.group_domains(["two.example"], "Second")
        trackers = {tracker["display_name"]: tracker for tracker in database.list_trackers()}
        for name, uploaded, downloaded in (("First", "5 TiB", "1 TiB"), ("Second", "2 TiB", "3 TiB")):
            tracker = trackers[name]
            database.update_trackers(
                {
                    tracker["key"]: {
                        "display_name": name,
                        "visible_dashboard": True,
                        "visible_widget": True,
                        "uploaded_add": uploaded,
                        "downloaded_add": downloaded,
                    }
                }
            )

        database.group_domains(["one.example"], "Second")

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["domains"], ["one.example", "two.example"])
        self.assertEqual(trackers[0]["buffer_uploaded_text"], "7.00 TiB")
        self.assertEqual(trackers[0]["buffer_downloaded_text"], "4.00 TiB")

    def test_editing_a_name_to_an_existing_name_merges_the_groups(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example", "two.example"])
        database.group_domains(["one.example"], "First")
        database.group_domains(["two.example"], "Second")
        trackers = {tracker["display_name"]: tracker for tracker in database.list_trackers()}

        database.update_trackers(
            {
                trackers["First"]["key"]: {
                    "display_name": "Second",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "5 TiB",
                    "downloaded_add": "1 TiB",
                },
                trackers["Second"]["key"]: {
                    "display_name": "Second",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "2 TiB",
                    "downloaded_add": "3 TiB",
                },
            }
        )

        merged = database.list_trackers()
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["display_name"], "Second")
        self.assertEqual(merged[0]["domains"], ["one.example", "two.example"])
        self.assertEqual(merged[0]["buffer_uploaded_text"], "7.00 TiB")
        self.assertEqual(merged[0]["buffer_downloaded_text"], "4.00 TiB")

    def test_selected_domains_can_be_unlinked_from_group(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example", "two.example"])
        database.group_domains(["one.example", "two.example"], "My Tracker")

        database.unlink_domains(["two.example"])

        trackers = {tracker["display_name"]: tracker for tracker in database.list_trackers()}
        self.assertEqual(trackers["My Tracker"]["domains"], ["one.example"])
        self.assertEqual(trackers["two.example"]["domains"], ["two.example"])

    def test_unlinking_a_whole_buffered_group_preserves_its_total_buffer(self):
        database._safe_load_yaml = lambda path: {}
        database.load_tracker_configuration(["one.example", "two.example"])
        database.group_domains(["one.example", "two.example"], "My Tracker")
        tracker = database.list_trackers()[0]
        database.update_trackers(
            {
                tracker["key"]: {
                    "display_name": "My Tracker",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "5 TiB",
                    "downloaded_add": "1 TiB",
                }
            }
        )

        database.unlink_domains(["one.example", "two.example"])

        trackers = database.list_trackers()
        self.assertEqual(sum(row["buffer_uploaded"] for row in trackers), 5 * 1024**4)
        self.assertEqual(sum(row["buffer_downloaded"] for row in trackers), 1024**4)

    def test_example_placeholder_is_not_imported_or_listed(self):
        database._safe_load_yaml = lambda path: {
            "trackers": {
                "tracker_name": {
                    "display": "Name_Displayed",
                    "domains": ["tracker.address.1"],
                }
            },
            "buffers": {
                "tracker_name": {
                    "uploaded_add": "100 TiB",
                    "downloaded_add": "50 TiB",
                }
            },
        }

        database.init_database()

        self.assertEqual(database.list_trackers(), [])

    def test_old_renamed_example_placeholder_is_removed_when_it_has_no_domains(self):
        database._safe_load_yaml = lambda path: {}
        database.init_database()
        with database._database() as connection:
            connection.execute(
                """
                INSERT INTO trackers (
                    key, display_name, buffer_uploaded, buffer_downloaded,
                    buffer_uploaded_text, buffer_downloaded_text
                ) VALUES ('tracker_name', 'Public', ?, ?, '100 TiB', '50 TiB')
                """,
                (100 * 1024**4, 50 * 1024**4),
            )

        database.init_database()

        _, trackers = database.load_tracker_configuration()
        self.assertNotIn("tracker_name", trackers)

    def test_timed_events_expire_and_return_to_normal(self):
        database._safe_load_yaml = lambda path: {}
        current_time = [datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)]
        database._now_utc = lambda: current_time[0]
        database.load_tracker_configuration(["one.example"])
        tracker = database.list_trackers()[0]

        database.update_trackers(
            {
                tracker["key"]: {
                    "display_name": "Timed",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "0",
                    "downloaded_add": "0",
                    "event_uploaded_multiplier": "2",
                    "event_downloaded_multiplier": "0",
                    "event_uploaded_hours_remaining": "4",
                    "event_downloaded_hours_remaining": "2",
                }
            }
        )

        active = database.list_trackers()[0]
        self.assertEqual(active["event_uploaded_hours_remaining"], "4")
        self.assertEqual(active["event_downloaded_hours_remaining"], "2")

        current_time[0] += timedelta(hours=1)
        database.update_trackers(
            {
                tracker["key"]: {
                    "display_name": "Renamed while active",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "0",
                    "downloaded_add": "0",
                    "event_uploaded_multiplier": "2",
                    "event_downloaded_multiplier": "0",
                    "event_uploaded_hours_remaining": active["event_uploaded_hours_remaining"],
                    "event_downloaded_hours_remaining": active["event_downloaded_hours_remaining"],
                    "original_event_uploaded_multiplier": "2",
                    "original_event_downloaded_multiplier": "0",
                    "original_event_uploaded_hours_remaining": active["event_uploaded_hours_remaining"],
                    "original_event_downloaded_hours_remaining": active["event_downloaded_hours_remaining"],
                    "original_event_uploaded_expires_at": active["event_uploaded_expires_at"],
                    "original_event_downloaded_expires_at": active["event_downloaded_expires_at"],
                }
            }
        )
        preserved = database.list_trackers()[0]
        self.assertEqual(
            preserved["event_uploaded_expires_at"], active["event_uploaded_expires_at"]
        )
        self.assertEqual(
            preserved["event_downloaded_expires_at"], active["event_downloaded_expires_at"]
        )

        current_time[0] += timedelta(hours=2)
        expired_download = database.list_trackers()[0]
        self.assertEqual(expired_download["event_uploaded_multiplier"], 2)
        self.assertEqual(expired_download["event_uploaded_hours_remaining"], "1")
        self.assertEqual(expired_download["event_downloaded_multiplier"], 1)
        self.assertEqual(expired_download["event_downloaded_hours_remaining"], "")

        current_time[0] += timedelta(hours=1)
        expired_upload = database.list_trackers()[0]
        self.assertEqual(expired_upload["event_uploaded_multiplier"], 1)
        self.assertEqual(expired_upload["event_uploaded_hours_remaining"], "")

    def test_qui_clients_can_be_added_and_deleted_with_selected_instance(self):
        database.init_database()

        client_id = database.add_torrent_client(
            "Seedbox", "https://qui.example", "7476", "abcdef1234", "3"
        )

        clients = database.list_torrent_clients()
        self.assertEqual(clients[0]["id"], client_id)
        self.assertEqual(clients[0]["base_url"], "https://qui.example:7476")
        self.assertEqual(clients[0]["instance_id"], "3")
        self.assertEqual(clients[0]["api_key_hint"], "******1234")

        database.delete_torrent_client(client_id)
        self.assertEqual(database.list_torrent_clients(), [])

    def test_qui_client_can_be_edited_without_retyping_api_key(self):
        database.init_database()
        client_id = database.add_torrent_client(
            "Seedbox", "http://old.example", "7476", "secret-key", "1"
        )

        updated = database.update_torrent_client(
            client_id, "Maison", "https://new.example", "8080", "", "9"
        )

        self.assertTrue(updated)
        client = database.list_torrent_clients()[0]
        self.assertEqual(client["name"], "Maison")
        self.assertEqual(client["base_url"], "https://new.example:8080")
        self.assertEqual(client["instance_id"], "9")
        self.assertEqual(client["api_key"], "secret-key")

        database.update_torrent_client(
            client_id, "Maison", "https://new.example", "8080", "new-key", "9"
        )
        self.assertEqual(database.list_torrent_clients()[0]["api_key"], "new-key")

    def test_iframe_option_is_enabled_by_default_and_can_be_disabled(self):
        database.init_database()

        defaults = database.get_app_options()
        self.assertTrue(defaults["iframe_enabled"])
        self.assertTrue(defaults["background_refresh_enabled"])
        self.assertEqual(defaults["refresh_interval_seconds"], 3600)
        self.assertEqual(defaults["http_timeout_seconds"], 10)

        database.update_app_options(False)
        self.assertFalse(database.get_app_options()["iframe_enabled"])

        database.update_app_options(True)
        self.assertTrue(database.get_app_options()["iframe_enabled"])

    def test_homarr_auth_options_are_stored_in_the_interface_settings(self):
        database.init_database()

        database.update_app_options(
            True, True, "https://homarr.example/", "/api/auth/session"
        )

        options = database.get_app_options()
        self.assertTrue(options["homarr_auth_enabled"])
        self.assertEqual(options["homarr_base_url"], "https://homarr.example")
        self.assertEqual(options["homarr_session_endpoint"], "/api/auth/session")

    def test_collection_options_are_stored_in_the_interface_settings(self):
        database.init_database()

        database.update_app_options(
            True,
            False,
            "",
            "/api/auth/session",
            False,
            "0.5",
            "25",
        )

        options = database.get_app_options()
        self.assertFalse(options["background_refresh_enabled"])
        self.assertEqual(options["refresh_interval_seconds"], 1800)
        self.assertEqual(options["refresh_interval_hours"], 0.5)
        self.assertEqual(options["http_timeout_seconds"], 25)

    def test_invalid_stored_collection_options_fall_back_to_defaults(self):
        database.init_database()
        with database._database() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                ("refresh_interval_seconds", "invalid"),
            )
            connection.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                ("http_timeout_seconds", "invalid"),
            )

        options = database.get_app_options()
        self.assertEqual(options["refresh_interval_seconds"], 3600)
        self.assertEqual(options["http_timeout_seconds"], 10)


if __name__ == "__main__":
    unittest.main()
