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

from qui_ratio_dashboard import formatters


class TrackerVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_trackers_path = formatters.TRACKERS_PATH
        self.previous_buffers_path = formatters.BUFFERS_PATH
        self.previous_safe_load = formatters.yaml.safe_load
        formatters.TRACKERS_PATH = os.path.join(self.temp_dir.name, "trackers.yml")
        formatters.BUFFERS_PATH = os.path.join(self.temp_dir.name, "buffers.yml")

    def tearDown(self):
        formatters.TRACKERS_PATH = self.previous_trackers_path
        formatters.BUFFERS_PATH = self.previous_buffers_path
        formatters.yaml.safe_load = self.previous_safe_load
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
        return {r["_key"]: r for r in formatters.compute_tracker_rows(payload)}

    def test_trackers_are_visible_by_default_and_can_be_hidden_on_web(self):
        with open(formatters.TRACKERS_PATH, "w", encoding="utf-8") as trackers_file:
            trackers_file.write("trackers configured externally\n")
        formatters.yaml.safe_load = lambda _: {
            "trackers": {
                "shown": {"domains": ["shown.example"]},
                "hidden": {"visible": False, "domains": ["hidden.example"]},
            }
        }

        rows = self.compute()

        self.assertTrue(rows["shown"]["web_visible"])
        self.assertFalse(rows["hidden"]["web_visible"])
        self.assertTrue(rows["unknown.example"]["web_visible"])


if __name__ == "__main__":
    unittest.main()
