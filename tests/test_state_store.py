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
        return state_store.apply_domain_ledger(rows, mapping or {})

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
        rows, _ = state_store.apply_domain_ledger(
            [self.domain_row("tracker.ygg.example", 100, 50)],
            {"tracker.ygg.example": "ygg"},
            settings,
        )
        self.assertEqual(rows[0]["uploaded"], 100)
        self.assertEqual(rows[0]["downloaded"], 50)

        rows, _ = state_store.apply_domain_ledger(
            [self.domain_row("tracker.ygg.example", 110, 60)],
            {"tracker.ygg.example": "ygg"},
            settings,
        )
        self.assertEqual(rows[0]["uploaded"], 120)
        self.assertEqual(rows[0]["downloaded"], 55)

        rows, _ = state_store.apply_domain_ledger(
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


if __name__ == "__main__":
    unittest.main()
