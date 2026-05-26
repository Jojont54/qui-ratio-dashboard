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
        self.previous_legacy_dirs = database.LEGACY_CONFIG_DIRECTORIES
        self.previous_legacy_loader = database._load_legacy_yaml
        self.previous_legacy_remover = database._remove_migrated_yaml_files
        self.previous_now = database._now_utc
        database.DATABASE_PATH = os.path.join(self.temp_dir.name, "dashboard.db")
        database.LEGACY_CONFIG_DIRECTORIES = (self.temp_dir.name,)

    def tearDown(self):
        database.DATABASE_PATH = self.previous_database_path
        database.LEGACY_CONFIG_DIRECTORIES = self.previous_legacy_dirs
        database._load_legacy_yaml = self.previous_legacy_loader
        database._remove_migrated_yaml_files = self.previous_legacy_remover
        database._now_utc = self.previous_now
        self.temp_dir.cleanup()

    def test_legacy_yaml_is_automatically_imported_once_into_an_empty_database(self):
        trackers_path = os.path.join(self.temp_dir.name, "trackers.yml")
        buffers_path = os.path.join(self.temp_dir.name, "buffers.yml")
        with open(trackers_path, "w", encoding="utf-8") as source_file:
            source_file.write("legacy tracker configuration")
        with open(buffers_path, "w", encoding="utf-8") as source_file:
            source_file.write("legacy buffer configuration")
        contents = {
            trackers_path: {
                "trackers": {
                    "sample": {
                        "display": "Sample Tracker",
                        "visible": False,
                        "domains": ["tracker.sample.example"],
                    }
                }
            },
            buffers_path: {
                "buffers": {
                    "sample": {"uploaded_add": "10 TiB", "downloaded_add": "2 TiB"}
                }
            },
        }
        database._load_legacy_yaml = lambda path: contents.get(path, {})

        database.init_database()

        _, config = database.load_tracker_configuration()
        self.assertEqual(config["sample"]["display"], "Sample Tracker")
        self.assertFalse(config["sample"]["visible_widget"])
        self.assertEqual(config["sample"]["uploaded_add"], 10 * 1024**4)
        self.assertEqual(database.list_trackers()[0]["buffer_uploaded_text"], "10 TiB")
        self.assertFalse(os.path.exists(trackers_path))
        self.assertFalse(os.path.exists(buffers_path))

        contents[buffers_path]["buffers"]["sample"]["uploaded_add"] = "99 TiB"
        database.init_database()
        _, config = database.load_tracker_configuration()
        self.assertEqual(config["sample"]["uploaded_add"], 10 * 1024**4)
        with database._database() as connection:
            status = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'legacy_yaml_migration'"
            ).fetchone()["value"]
        self.assertEqual(status, "imported")

    def test_read_only_legacy_yaml_does_not_block_migration_and_cleanup_is_retried(self):
        trackers_path = os.path.join(self.temp_dir.name, "trackers.yml")
        with open(trackers_path, "w", encoding="utf-8") as source_file:
            source_file.write("legacy tracker configuration")
        database._load_legacy_yaml = lambda path: {
            "trackers": {"old": {"display": "Old", "domains": ["old.example"]}}
        }
        cleanup_allowed = [False]

        def remove_when_allowed(paths):
            if not cleanup_allowed[0]:
                return False
            for path in paths:
                if path and os.path.exists(path):
                    os.remove(path)
            return True

        database._remove_migrated_yaml_files = remove_when_allowed

        database.init_database()
        self.assertEqual(database.list_trackers()[0]["display_name"], "Old")
        self.assertTrue(os.path.exists(trackers_path))
        with database._database() as connection:
            status = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'legacy_yaml_migration'"
            ).fetchone()["value"]
        self.assertEqual(status, "imported_cleanup_pending")

        cleanup_allowed[0] = True
        database.init_database()
        self.assertFalse(os.path.exists(trackers_path))
        with database._database() as connection:
            status = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'legacy_yaml_migration'"
            ).fetchone()["value"]
        self.assertEqual(status, "imported")

    def test_legacy_yaml_does_not_overwrite_an_already_configured_database(self):
        database.init_database()
        database.load_tracker_configuration(["current.example"])
        database.group_domains(["current.example"], "Current")
        trackers_path = os.path.join(self.temp_dir.name, "trackers.yml")
        with open(trackers_path, "w", encoding="utf-8") as source_file:
            source_file.write("legacy tracker configuration")
        database._load_legacy_yaml = lambda path: {
            "trackers": {
                "legacy": {
                    "display": "Legacy",
                    "domains": ["legacy.example"],
                }
            }
        }

        database.init_database()

        trackers = database.list_trackers()
        self.assertEqual([tracker["display_name"] for tracker in trackers], ["Current"])
        self.assertTrue(os.path.exists(trackers_path))
        with database._database() as connection:
            status = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'legacy_yaml_migration'"
            ).fetchone()["value"]
        self.assertEqual(status, "skipped_existing_database")

    def test_discovered_domain_becomes_manageable_tracker(self):
        _, config = database.load_tracker_configuration(["new.tracker.example"])

        self.assertIn("new.tracker.example", config)
        self.assertEqual(database.list_trackers()[0]["domains"], ["new.tracker.example"])

    def test_detected_parent_domain_and_its_subdomain_are_automatically_grouped(self):
        database.load_tracker_configuration(["tracker.example.test", "example.test"])

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["display_name"], "example.test")
        self.assertEqual(trackers[0]["domains"], ["example.test", "tracker.example.test"])

    def test_detected_subdomain_joins_an_existing_named_parent_tracker(self):
        database.load_tracker_configuration(["example.test"])
        database.group_domains(["example.test"], "Sample Tracker")

        database.load_tracker_configuration(["tracker.example.test"])

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["display_name"], "Sample Tracker")
        self.assertEqual(trackers[0]["domains"], ["example.test", "tracker.example.test"])

    def test_manually_unlinked_related_domains_are_not_linked_again(self):
        database.load_tracker_configuration(["tracker.example.test", "example.test"])
        database.unlink_domains(["tracker.example.test"])

        database.load_tracker_configuration(["tracker.example.test", "example.test"])

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 2)

    def test_existing_related_domains_are_grouped_once_during_upgrade(self):
        database.init_database()
        with database._database() as connection:
            connection.execute(
                "DELETE FROM app_metadata WHERE key = ?",
                (database._AUTO_PARENT_DOMAIN_GROUPING_KEY,),
            )
            for domain in ("example.test", "tracker.example.test"):
                connection.execute(
                    "INSERT INTO trackers (key, display_name) VALUES (?, ?)",
                    (domain, domain),
                )
                connection.execute(
                    "INSERT INTO tracker_domains (domain, tracker_key) VALUES (?, ?)",
                    (domain, domain),
                )

        database.init_database()
        self.assertEqual(len(database.list_trackers()), 1)

        database.unlink_domains(["tracker.example.test"])
        database.init_database()
        self.assertEqual(len(database.list_trackers()), 2)

    def test_selected_domains_can_be_grouped_and_empty_discovered_entries_removed(self):
        database.load_tracker_configuration(["one.example", "two.example"])

        database.group_domains(["one.example", "two.example"], "My Tracker")

        trackers = database.list_trackers()
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["display_name"], "My Tracker")
        self.assertEqual(trackers[0]["domains"], ["one.example", "two.example"])

    def test_settings_update_keeps_existing_domain_links(self):
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
        database.load_tracker_configuration(["one.example", "two.example"])
        database.group_domains(["one.example", "two.example"], "My Tracker")

        database.unlink_domains(["two.example"])

        trackers = {tracker["display_name"]: tracker for tracker in database.list_trackers()}
        self.assertEqual(trackers["My Tracker"]["domains"], ["one.example"])
        self.assertEqual(trackers["two.example"]["domains"], ["two.example"])

    def test_unlinking_a_whole_buffered_group_preserves_its_total_buffer(self):
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

    def test_old_renamed_example_placeholder_is_removed_when_it_has_no_domains(self):
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
        self.assertEqual(clients[0]["initial_sync_mode"], "preserve")
        self.assertTrue(clients[0]["sync_pending"])

        database.complete_client_initializations([client_id])
        self.assertFalse(database.list_torrent_clients()[0]["sync_pending"])

        database.delete_torrent_client(client_id)
        self.assertEqual(database.list_torrent_clients(), [])

    def test_client_connection_status_records_last_result(self):
        client_id = database.add_torrent_client(
            "Seedbox", "https://qui.example", "7476", "abcdef1234", "3"
        )
        self.assertIsNone(database.list_torrent_clients()[0]["last_connection_success"])

        database.update_torrent_client_connection_status(client_id, False, "Timeout")
        failed = database.list_torrent_clients()[0]
        self.assertFalse(failed["last_connection_success"])
        self.assertEqual(failed["last_connection_error"], "Timeout")
        self.assertTrue(failed["last_connection_at"])

        database.update_torrent_client_connection_status(client_id, True)
        recovered = database.list_torrent_clients()[0]
        self.assertTrue(recovered["last_connection_success"])
        self.assertEqual(recovered["last_connection_error"], "")

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

        database.complete_client_initializations([client_id])
        database.update_torrent_client(
            client_id, "Maison", "https://new.example", "8080", "new-key", "9", "add"
        )
        client = database.list_torrent_clients()[0]
        self.assertEqual(client["api_key"], "new-key")
        self.assertEqual(client["initial_sync_mode"], "add")
        self.assertTrue(client["sync_pending"])

    def test_direct_torrent_client_credentials_and_rpc_path_are_stored(self):
        client_id = database.add_torrent_client(
            "Transmission",
            "http://transmission",
            "9091",
            "",
            "",
            "replace",
            "TRANSMISSION",
            "user",
            "secret",
            "/transmission/rpc",
        )

        client = database.list_torrent_clients()[0]
        self.assertEqual(client["id"], client_id)
        self.assertEqual(client["client_type"], "TRANSMISSION")
        self.assertEqual(client["username"], "user")
        self.assertEqual(client["password"], "secret")
        self.assertEqual(client["rpc_path"], "/transmission/rpc")
        self.assertEqual(client["instance_id"], "")

    def test_replacing_client_values_can_clear_only_selected_tracker_buffers(self):
        first = database.create_tracker("Premier")
        second = database.create_tracker("Second")
        database.update_trackers(
            {
                first: {
                    "display_name": "Premier",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "10 GiB",
                    "downloaded_add": "2 GiB",
                },
                second: {
                    "display_name": "Second",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "20 GiB",
                    "downloaded_add": "4 GiB",
                },
            }
        )

        database.clear_tracker_buffers([first])
        _, settings = database.load_tracker_configuration()
        self.assertEqual(settings[first]["uploaded_add"], 0)
        self.assertEqual(settings[first]["downloaded_add"], 0)
        self.assertGreater(settings[second]["uploaded_add"], 0)

    def test_iframe_option_is_enabled_by_default_and_can_be_disabled(self):
        database.init_database()

        defaults = database.get_app_options()
        self.assertTrue(defaults["iframe_enabled"])
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

    def test_prowlarr_options_and_torrent_rules_are_stored(self):
        database.init_database()
        client_id = database.add_torrent_client(
            "Transmission", "http://transmission", "", "", "", "replace", "TRANSMISSION"
        )
        database.update_app_options(
            True,
            prowlarr_enabled=True,
            prowlarr_base_url="http://prowlarr:9696/",
            prowlarr_api_key="secret",
        )
        database.save_torrent_rule(
            client_id,
            "ABCDEF",
            "tracker.example",
            {
                "upload_multiplier": 2,
                "download_multiplier": 0,
                "label": "Upload x2 + Freeleech",
                "source": "prowlarr",
            },
        )

        options = database.get_app_options()
        rules = database.get_torrent_rules(client_id, ["abcdef"])
        self.assertTrue(options["prowlarr_enabled"])
        self.assertEqual(options["prowlarr_base_url"], "http://prowlarr:9696")
        self.assertEqual(options["prowlarr_api_key"], "secret")
        self.assertEqual(rules["abcdef"]["download_multiplier"], 0)
        self.assertEqual(rules["abcdef"]["upload_multiplier"], 2)

    def test_prowlarr_miss_can_be_replaced_when_history_arrives_later(self):
        client_id = database.add_torrent_client(
            "Deluge", "http://deluge", "", "", "", "replace", "DELUGE"
        )
        database.save_torrent_rule(
            client_id,
            "abc",
            "tracker.example",
            {"label": "Non trouve dans Prowlarr", "source": "prowlarr-miss"},
        )
        database.save_torrent_rule(
            client_id,
            "abc",
            "tracker.example",
            {
                "download_multiplier": 0,
                "label": "Freeleech",
                "source": "prowlarr",
            },
        )

        rule = database.get_torrent_rules(client_id, ["abc"])["abc"]
        self.assertEqual(rule["download_multiplier"], 0)
        self.assertEqual(rule["source"], "prowlarr")
        self.assertEqual(rule["lookup_attempts"], 2)

    def test_many_detected_torrent_rules_can_be_saved_in_one_collection(self):
        client_id = database.add_torrent_client(
            "qBittorrent", "http://qbit", "", "", "", "replace", "QBITTORRENT"
        )
        rules = [
            (
                f"hash-{index}",
                "tracker.example",
                {
                    "upload_multiplier": 1,
                    "download_multiplier": 1,
                    "label": "Normal",
                    "source": "detected",
                },
            )
            for index in range(2000)
        ]

        database.save_torrent_rules(client_id, rules)

        stored = database.get_torrent_rules(client_id, ["hash-0", "hash-1999"])
        self.assertEqual(set(stored), {"hash-0", "hash-1999"})
        self.assertEqual(stored["hash-1999"]["source"], "detected")

    def test_collection_options_are_stored_in_the_interface_settings(self):
        database.init_database()

        database.update_app_options(
            True,
            False,
            "",
            "/api/auth/session",
            "30",
            "25",
        )

        options = database.get_app_options()
        self.assertEqual(options["refresh_interval_seconds"], 1800)
        self.assertEqual(options["refresh_interval_minutes"], 30)
        self.assertEqual(options["http_timeout_seconds"], 25)

    def test_obsolete_background_refresh_toggle_is_removed(self):
        database.init_database()
        with database._database() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                ("background_refresh_enabled", "0"),
            )

        database.init_database()

        with database._database() as connection:
            stored = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'background_refresh_enabled'"
            ).fetchone()
        self.assertIsNone(stored)

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
        self.assertEqual(options["refresh_interval_minutes"], 60)
        self.assertEqual(options["http_timeout_seconds"], 10)


if __name__ == "__main__":
    unittest.main()
