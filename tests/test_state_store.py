import json
import os
import tempfile
import unittest

from qui_ratio_dashboard import state_store


class DomainLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_path = state_store.STATE_PATH
        state_store.STATE_PATH = os.path.join(self.temp_dir.name, "state.json")

    def tearDown(self):
        state_store.STATE_PATH = self.previous_path
        self.temp_dir.cleanup()

    def apply(self, rows, mapping=None):
        return state_store.apply_domain_ledger(rows, mapping or {})[:2]

    def domain_row(self, domain, uploaded, downloaded):
        return {
            "_key": domain,
            "domain": domain,
            "tracker": domain,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "count": 1,
            "total_size": 1,
        }

    def test_history_follows_domain_when_it_is_regrouped(self):
        rows, _ = self.apply([self.domain_row("tracker.example", 100, 50)], {"tracker.example": "old"})
        self.assertEqual(rows[0]["uploaded"], 100)

        rows, _ = self.apply([self.domain_row("tracker.example", 110, 55)], {"tracker.example": "new"})
        self.assertEqual(rows[0]["uploaded"], 110)
        self.assertEqual(rows[0]["downloaded"], 55)

    def test_removed_domain_is_kept_in_history(self):
        self.apply([self.domain_row("tracker.example", 100, 50)])
        rows, _ = self.apply([])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)

        rows, _ = self.apply([self.domain_row("tracker.example", 20, 10)])
        self.assertEqual(rows[0]["uploaded"], 120)
        self.assertEqual(rows[0]["downloaded"], 60)

    def test_stored_rows_can_render_dashboard_without_a_new_collection(self):
        active = self.domain_row("tracker.example", 100, 50)
        active["count"] = 3
        active["total_size"] = 900
        self.apply([active])

        rows, adjustments = state_store.stored_domain_rows()

        self.assertEqual(adjustments, {})
        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)
        self.assertEqual(rows[0]["count"], 3)
        self.assertEqual(rows[0]["total_size"], 900)

        self.apply([])
        rows, _ = state_store.stored_domain_rows()
        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)
        self.assertEqual(rows[0]["count"], 0)
        self.assertEqual(rows[0]["total_size"], 0)

    def test_display_snapshot_is_compact_when_ledger_contains_many_torrents(self):
        rows = []
        for index in range(100):
            row = self.domain_row("tracker.example", 10, 5)
            row["ledger_key"] = f"client:1:torrent:{index}"
            rows.append(row)
        self.apply(rows)

        cached, _ = state_store.stored_domain_rows()

        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["uploaded"], 1000)
        self.assertEqual(cached[0]["downloaded"], 500)
        self.assertTrue(os.path.exists(state_store.STATE_PATH + ".snapshot"))

    def test_previous_tracker_state_becomes_a_legacy_adjustment(self):
        with open(state_store.STATE_PATH, "w", encoding="utf-8") as state_file:
            json.dump(
                {"version": 2, "trackers": {"ygg": {"prev_raw_u": 100, "prev_raw_d": 50}}},
                state_file,
            )

        rows, adjustments = self.apply(
            [self.domain_row("tracker.ygg.example", 20, 10)],
            {"tracker.ygg.example": "ygg"},
        )

        self.assertEqual(rows[0]["uploaded"], 20)
        self.assertEqual(adjustments["ygg"], {"uploaded": 80, "downloaded": 40})

    def test_events_only_apply_to_new_transfer_deltas(self):
        settings = {
            "ygg": {
                "event_uploaded_multiplier": 2,
                "event_downloaded_multiplier": 0.5,
            }
        }
        rows, _, _, _ = state_store.apply_domain_ledger(
            [self.domain_row("tracker.ygg.example", 100, 50)],
            {"tracker.ygg.example": "ygg"},
            settings,
        )
        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)

        rows, _, _, _ = state_store.apply_domain_ledger(
            [self.domain_row("tracker.ygg.example", 110, 60)],
            {"tracker.ygg.example": "ygg"},
            settings,
        )
        self.assertEqual(rows[0]["uploaded"], 120)
        self.assertEqual(rows[0]["downloaded"], 55)

        rows, _, _, _ = state_store.apply_domain_ledger(
            [self.domain_row("tracker.ygg.example", 120, 70)],
            {"tracker.ygg.example": "ygg"},
            {"ygg": {"event_uploaded_multiplier": 1, "event_downloaded_multiplier": 0}},
        )
        self.assertEqual(rows[0]["uploaded"], 130)
        self.assertEqual(rows[0]["downloaded"], 55)

    def test_each_client_keeps_its_own_history_for_the_same_domain(self):
        first = self.domain_row("tracker.example", 100, 30)
        first["ledger_key"] = "client:1:tracker.example"
        second = self.domain_row("tracker.example", 80, 20)
        second["ledger_key"] = "client:2:tracker.example"
        rows, _ = self.apply([first, second])
        self.assertEqual(sum(row["uploaded"] for row in rows), 180)

        first = self.domain_row("tracker.example", 10, 2)
        first["ledger_key"] = "client:1:tracker.example"
        second = self.domain_row("tracker.example", 100, 25)
        second["ledger_key"] = "client:2:tracker.example"
        rows, _ = self.apply([first, second])

        self.assertEqual(sum(row["uploaded"] for row in rows), 200)
        self.assertEqual(sum(row["downloaded"] for row in rows), 55)

    def client_row(self, client_id, uploaded, downloaded):
        row = self.domain_row("tracker.example", uploaded, downloaded)
        row["ledger_key"] = f"client:{client_id}:tracker.example"
        row["client_id"] = client_id
        return row

    def test_preserve_initialization_uses_incoming_totals_only_as_a_new_reference(self):
        rows, _, _, initialized = state_store.apply_domain_ledger(
            [self.client_row(1, 100, 50)],
            {"tracker.example": "tracker"},
            client_initializations={1: "preserve"},
        )
        self.assertEqual(rows[0]["uploaded"], 0)
        self.assertEqual(rows[0]["downloaded"], 0)
        self.assertEqual(initialized, {1})

        rows, _, _, _ = state_store.apply_domain_ledger(
            [self.client_row(1, 110, 54)], {"tracker.example": "tracker"}
        )
        self.assertEqual(rows[0]["uploaded"], 10)
        self.assertEqual(rows[0]["downloaded"], 4)

    def test_preserve_initialization_does_not_add_repaired_connection_totals(self):
        state_store.apply_domain_ledger(
            [self.client_row(1, 100, 50)], {"tracker.example": "tracker"}
        )
        rows, _, _, _ = state_store.apply_domain_ledger(
            [self.client_row(1, 300, 200)],
            {"tracker.example": "tracker"},
            client_initializations={1: "preserve"},
        )
        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)

    def test_add_initialization_credits_client_totals_on_top_of_stored_values(self):
        state_store.apply_domain_ledger(
            [self.client_row(1, 100, 50)], {"tracker.example": "tracker"}
        )
        rows, _, _, _ = state_store.apply_domain_ledger(
            [self.client_row(1, 30, 20)],
            {"tracker.example": "tracker"},
            client_initializations={1: "add"},
        )
        self.assertEqual(rows[0]["uploaded"], 130)
        self.assertEqual(rows[0]["downloaded"], 70)

    def test_replace_initialization_removes_stored_tracker_history(self):
        state_store.apply_domain_ledger(
            [self.client_row(1, 100, 50)], {"tracker.example": "tracker"}
        )
        rows, adjustments, replace_keys, initialized = state_store.apply_domain_ledger(
            [self.client_row(2, 30, 20)],
            {"tracker.example": "tracker"},
            client_initializations={2: "replace"},
        )
        self.assertEqual(rows[0]["uploaded"], 30)
        self.assertEqual(rows[0]["downloaded"], 20)
        self.assertEqual(adjustments, {})
        self.assertEqual(replace_keys, {"tracker"})
        self.assertEqual(initialized, {2})

    def test_replace_initialization_can_clear_history_even_when_client_is_empty(self):
        state_store.apply_domain_ledger(
            [self.client_row(1, 100, 50)], {"tracker.example": "tracker"}
        )
        rows, adjustments, replace_keys, initialized = state_store.apply_domain_ledger(
            [],
            {},
            {"tracker": {}},
            {2: "replace"},
            {2},
        )
        self.assertEqual(rows, [])
        self.assertEqual(adjustments, {})
        self.assertEqual(replace_keys, {"tracker"})
        self.assertEqual(initialized, {2})


if __name__ == "__main__":
    unittest.main()
