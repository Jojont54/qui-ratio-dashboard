import json
import os
import tempfile
import unittest

from qui_ratio_dashboard import state_store


def row(uploaded, downloaded, manual_uploaded=0, manual_downloaded=0):
    return {
        "_key": "tracker",
        "tracker": "Tracker",
        "uploaded": uploaded,
        "downloaded": downloaded,
        "manual_buffer_uploaded": manual_uploaded,
        "manual_buffer_downloaded": manual_downloaded,
    }


class StateLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_path = state_store.STATE_PATH
        state_store.STATE_PATH = os.path.join(self.temp_dir.name, "state.json")

    def tearDown(self):
        state_store.STATE_PATH = self.previous_path
        self.temp_dir.cleanup()

    def apply(self, *args, **kwargs):
        return state_store.apply_state_ledger([row(*args, **kwargs)])[0]

    def test_new_activity_is_visible_after_a_torrent_removal(self):
        self.assertEqual(self.apply(100, 50)["uploaded"], 100)

        after_removal = self.apply(20, 10)
        self.assertEqual(after_removal["uploaded"], 100)
        self.assertEqual(after_removal["downloaded"], 50)
        self.assertEqual(after_removal["carried_uploaded"], 80)

        after_new_transfer = self.apply(30, 15)
        self.assertEqual(after_new_transfer["uploaded"], 110)
        self.assertEqual(after_new_transfer["downloaded"], 55)
        self.assertEqual(after_new_transfer["ratio"], 2.0)

    def test_manual_buffer_is_fixed_and_not_recorded_as_history(self):
        initial = self.apply(100, 50, manual_uploaded=1000, manual_downloaded=500)
        self.assertEqual(initial["uploaded"], 1100)

        after_removal = self.apply(20, 10, manual_uploaded=1000, manual_downloaded=500)
        self.assertEqual(after_removal["tracked_uploaded"], 100)
        self.assertEqual(after_removal["uploaded"], 1100)

    def test_floor_state_is_migrated_without_changing_current_display(self):
        with open(state_store.STATE_PATH, "w", encoding="utf-8") as state_file:
            json.dump({"trackers": {"tracker": {"prev_raw_u": 100, "prev_raw_d": 50}}}, state_file)

        migrated = self.apply(20, 10)
        self.assertEqual(migrated["uploaded"], 100)
        self.assertEqual(migrated["downloaded"], 50)

        advanced = self.apply(30, 15)
        self.assertEqual(advanced["uploaded"], 110)
        self.assertEqual(advanced["downloaded"], 55)


if __name__ == "__main__":
    unittest.main()
