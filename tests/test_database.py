import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from studyflow.domain import ActivityState
from studyflow.infrastructure import Database
from studyflow.timer_service import TimerService


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.db.connection.close()
        self.temp.cleanup()

    def test_settings_round_trip(self):
        self.assertEqual(self.db.get_setting("idle_threshold_seconds"), 30)
        self.db.set_setting("idle_threshold_seconds", 600)
        self.assertEqual(self.db.get_setting("idle_threshold_seconds"), 600)

    def test_segment_transition_and_snapshot(self):
        start = datetime.now(timezone.utc).replace(microsecond=0)
        segment_id = self.db.start_segment(ActivityState.WORKING, start, "automatic")
        segment_id = self.db.transition(segment_id, ActivityState.IDLE,
                                        start + timedelta(minutes=5))
        self.db.close_segment(segment_id, start + timedelta(minutes=7))
        snapshot = self.db.snapshot(date.today(), 14400)
        self.assertEqual(snapshot.working_seconds, 300)
        self.assertEqual(snapshot.idle_seconds, 120)
        self.assertEqual(snapshot.longest_working_seconds, 300)

    def test_recover_open_segment(self):
        self.db.start_segment(ActivityState.WORKING, datetime.now(timezone.utc), "automatic")
        self.db.recover_open_segment()
        row = self.db.connection.execute("SELECT end_utc FROM activity_segments").fetchone()
        self.assertIsNotNone(row[0])

    def test_new_launch_starts_display_at_zero_but_keeps_daily_statistics(self):
        now = datetime.now(timezone.utc)
        segment_id = self.db.start_segment(
            ActivityState.WORKING, now - timedelta(hours=1), "automatic"
        )
        self.db.close_segment(segment_id, now - timedelta(minutes=30))
        with patch("studyflow.timer_service.get_idle_seconds", return_value=0.0):
            service = TimerService(self.db)
            snapshots = []
            service.on_snapshot(snapshots.append)
            service.refresh()
        self.assertEqual(snapshots[-1].working_seconds, 0)
        self.assertEqual(self.db.snapshot(date.today(), 14400).working_seconds, 1800)
        service.stop()


if __name__ == "__main__":
    unittest.main()
