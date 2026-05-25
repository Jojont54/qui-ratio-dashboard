import os
import sys
import tempfile
import types
import unittest

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda _: {}
    sys.modules["yaml"] = yaml_stub

from qui_ratio_dashboard import database, formatters, state_store


class TrackerVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database_path = database.DATABASE_PATH
        self.previous_legacy_dirs = database.LEGACY_CONFIG_DIRECTORIES
        self.previous_state_path = state_store.STATE_PATH
        database.DATABASE_PATH = os.path.join(self.temp_dir.name, "dashboard.db")
        database.LEGACY_CONFIG_DIRECTORIES = (self.temp_dir.name,)
        state_store.STATE_PATH = os.path.join(self.temp_dir.name, "state.json")
        database.init_database()
        shown = database.create_tracker("Shown")
        hidden = database.create_tracker("Hidden")
        self.shown = shown
        database.load_tracker_configuration(["shown.example", "hidden.example"])
        database.group_domains(["shown.example"], "Shown")
        database.group_domains(["hidden.example"], "Hidden")
        database.update_trackers(
            {
                hidden: {
                    "display_name": "Hidden",
                    "visible_dashboard": True,
                    "visible_widget": False,
                    "uploaded_add": "0",
                    "downloaded_add": "0",
                }
            }
        )

    def tearDown(self):
        database.DATABASE_PATH = self.previous_database_path
        database.LEGACY_CONFIG_DIRECTORIES = self.previous_legacy_dirs
        state_store.STATE_PATH = self.previous_state_path
        self.temp_dir.cleanup()

    def compute(self):
        payload = {
            "counts": {
                "trackerTransfers": {
                    "shown.example": {"uploaded": 1, "downloaded": 1},
                    "hidden.example": {"uploaded": 1, "downloaded": 1},
                    "unknown.example": {"uploaded": 1, "downloaded": 1},
                }
            }
        }
        return {row["_key"]: row for row in formatters.compute_tracker_rows(payload)}

    def test_trackers_are_visible_by_default_and_can_be_hidden_in_widget(self):
        rows = self.compute()

        self.assertTrue(rows["shown"]["widget_visible"])
        self.assertFalse(rows["hidden"]["widget_visible"])
        self.assertTrue(rows["unknown.example"]["widget_visible"])

    def test_legacy_adjustment_is_included_after_aggregation(self):
        rows = formatters.aggregate_tracker_rows(
            [
                {
                    "_key": "shown.example",
                    "domain": "shown.example",
                    "uploaded": 20,
                    "downloaded": 10,
                    "count": 1,
                    "total_size": 1,
                }
            ],
            {"shown": {"uploaded": 80, "downloaded": 40}},
        )

        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)

    def test_buffer_is_applied_after_domain_aggregation(self):
        database.update_trackers(
            {
                self.shown: {
                    "display_name": "Shown",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "1000",
                    "downloaded_add": "500",
                }
            }
        )
        rows = formatters.aggregate_tracker_rows(
            [
                {
                    "_key": "shown.example",
                    "domain": "shown.example",
                    "uploaded": 100,
                    "downloaded": 50,
                    "count": 1,
                    "total_size": 1,
                }
            ]
        )

        self.assertEqual(rows[0]["uploaded"], 1100)
        self.assertEqual(rows[0]["downloaded"], 550)

    def test_inherited_history_remains_visible_without_an_active_domain(self):
        rows = formatters.aggregate_tracker_rows(
            [], {"shown": {"uploaded": 80, "downloaded": 40}}
        )

        self.assertEqual(rows[0]["tracker"], "Shown")
        self.assertEqual(rows[0]["uploaded"], 80)
        self.assertEqual(rows[0]["downloaded"], 40)

    def test_domains_assigned_to_same_group_are_aggregated(self):
        database.group_domains(["hidden.example"], "Shown")
        rows = formatters.compute_tracker_rows(
            {
                "counts": {
                    "trackerTransfers": {
                        "shown.example": {"uploaded": 100, "downloaded": 10},
                        "hidden.example": {"uploaded": 50, "downloaded": 5},
                    }
                }
            }
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tracker"], "Shown")
        self.assertEqual(rows[0]["uploaded"], 150)
        self.assertEqual(rows[0]["downloaded"], 15)

    def test_placeholder_legacy_history_is_not_rendered(self):
        rows = formatters.aggregate_tracker_rows(
            [], {"tracker_name": {"uploaded": 100, "downloaded": 50}}
        )

        self.assertEqual(rows, [])

    def test_unlinked_domain_is_displayed_as_its_own_tracker(self):
        database.group_domains(["hidden.example"], "Shown")
        database.unlink_domains(["hidden.example"])
        rows = formatters.compute_tracker_rows(
            {
                "counts": {
                    "trackerTransfers": {
                        "shown.example": {"uploaded": 100, "downloaded": 10},
                        "hidden.example": {"uploaded": 50, "downloaded": 5},
                    }
                }
            }
        )

        self.assertEqual({row["tracker"] for row in rows}, {"Shown", "hidden.example"})

    def test_same_domain_from_multiple_clients_is_combined_before_grouping(self):
        rows = formatters.combine_domain_rows(
            [
                {
                    "domain": "shown.example",
                    "uploaded": 100,
                    "downloaded": 20,
                    "count": 1,
                    "total_size": 300,
                },
                {
                    "domain": "shown.example",
                    "uploaded": 50,
                    "downloaded": 10,
                    "count": 2,
                    "total_size": 200,
                },
            ]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uploaded"], 150)
        self.assertEqual(rows[0]["downloaded"], 30)
        self.assertEqual(rows[0]["count"], 3)
        self.assertEqual(rows[0]["total_size"], 500)

    def test_dashboard_totals_survive_a_torrent_removed_from_qui_summary(self):
        gib = 1024**3
        database.update_trackers(
            {
                self.shown: {
                    "display_name": "Shown",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "10 GiB",
                    "downloaded_add": "5 GiB",
                }
            }
        )

        def refresh(uploaded, downloaded, count, total_size):
            rows = formatters.compute_domain_rows(
                {
                    "counts": {
                        "trackerTransfers": {
                            "shown.example": {
                                "uploaded": uploaded,
                                "downloaded": downloaded,
                                "count": count,
                                "totalSize": total_size,
                            }
                        }
                    }
                }
            )
            mapping, settings = database.load_tracker_configuration(["shown.example"])
            rows, legacy = state_store.apply_domain_ledger(rows, mapping, settings)
            return formatters.aggregate_tracker_rows(rows, legacy)[0]

        before_delete = refresh(300 * gib, 100 * gib, 2, 80 * gib)
        after_delete = refresh(120 * gib, 40 * gib, 1, 30 * gib)

        self.assertEqual(before_delete["uploaded"], 310 * gib)
        self.assertEqual(before_delete["downloaded"], 105 * gib)
        self.assertEqual(after_delete["uploaded"], before_delete["uploaded"])
        self.assertEqual(after_delete["downloaded"], before_delete["downloaded"])
        self.assertEqual(after_delete["ratio"], before_delete["ratio"])
        self.assertEqual(after_delete["count"], 1)
        self.assertEqual(after_delete["total_size"], 30 * gib)

    def event_removal_scenario(self, event_uploaded="1", event_downloaded="1"):
        gib = 1024**3
        database.update_trackers(
            {
                self.shown: {
                    "display_name": "Shown",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "10 GiB",
                    "downloaded_add": "5 GiB",
                }
            }
        )

        def refresh(uploaded, downloaded, count, total_size):
            rows = formatters.compute_domain_rows(
                {
                    "counts": {
                        "trackerTransfers": {
                            "shown.example": {
                                "uploaded": uploaded,
                                "downloaded": downloaded,
                                "count": count,
                                "totalSize": total_size,
                            }
                        }
                    }
                }
            )
            mapping, settings = database.load_tracker_configuration(["shown.example"])
            rows, legacy = state_store.apply_domain_ledger(rows, mapping, settings)
            return formatters.aggregate_tracker_rows(rows, legacy)[0]

        baseline = refresh(300 * gib, 100 * gib, 2, 80 * gib)
        database.update_trackers(
            {
                self.shown: {
                    "display_name": "Shown",
                    "visible_dashboard": True,
                    "visible_widget": True,
                    "uploaded_add": "10 GiB",
                    "downloaded_add": "5 GiB",
                    "event_uploaded_multiplier": event_uploaded,
                    "event_downloaded_multiplier": event_downloaded,
                }
            }
        )
        during_event = refresh(320 * gib, 110 * gib, 2, 80 * gib)
        after_delete = refresh(125 * gib, 42 * gib, 1, 30 * gib)
        continued = refresh(130 * gib, 45 * gib, 1, 30 * gib)
        return baseline, during_event, after_delete, continued

    def test_double_upload_credit_survives_a_torrent_removed_from_qui_summary(self):
        gib = 1024**3

        baseline, during_event, after_delete, continued = self.event_removal_scenario(
            event_uploaded="2"
        )

        self.assertEqual(baseline["uploaded"], 310 * gib)
        self.assertEqual(during_event["uploaded"], 350 * gib)
        self.assertEqual(during_event["downloaded"], 115 * gib)
        self.assertEqual(after_delete["uploaded"], during_event["uploaded"])
        self.assertEqual(after_delete["downloaded"], during_event["downloaded"])
        self.assertEqual(after_delete["ratio"], during_event["ratio"])
        self.assertEqual(after_delete["count"], 1)
        self.assertEqual(continued["uploaded"], 360 * gib)
        self.assertEqual(continued["downloaded"], 118 * gib)

    def test_freeleech_credit_survives_a_torrent_removed_from_qui_summary(self):
        gib = 1024**3

        baseline, during_event, after_delete, continued = self.event_removal_scenario(
            event_downloaded="0"
        )

        self.assertEqual(baseline["downloaded"], 105 * gib)
        self.assertEqual(during_event["uploaded"], 330 * gib)
        self.assertEqual(during_event["downloaded"], 105 * gib)
        self.assertEqual(after_delete["uploaded"], during_event["uploaded"])
        self.assertEqual(after_delete["downloaded"], during_event["downloaded"])
        self.assertEqual(after_delete["ratio"], during_event["ratio"])
        self.assertEqual(after_delete["count"], 1)
        self.assertEqual(continued["uploaded"], 335 * gib)
        self.assertEqual(continued["downloaded"], 105 * gib)


if __name__ == "__main__":
    unittest.main()
