import os
import tempfile
import unittest

from qui_ratio_dashboard import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_log_directory = diagnostics.LOG_DIRECTORY
        self.previous_log_path = diagnostics.LOG_PATH
        self.previous_max_log_bytes = diagnostics.MAX_LOG_BYTES
        diagnostics.LOG_DIRECTORY = self.temp_dir.name
        diagnostics.LOG_PATH = os.path.join(self.temp_dir.name, "log.txt")
        diagnostics.MAX_LOG_BYTES = 20

    def tearDown(self):
        diagnostics.LOG_DIRECTORY = self.previous_log_directory
        diagnostics.LOG_PATH = self.previous_log_path
        diagnostics.MAX_LOG_BYTES = self.previous_max_log_bytes
        self.temp_dir.cleanup()

    def test_log_event_writes_and_rotates_until_log_9(self):
        for index in range(15):
            diagnostics.log_event("event", index=index, value="x" * 20)

        self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, "log.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, "log.1.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, "log.9.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "log.10.txt")))


if __name__ == "__main__":
    unittest.main()
