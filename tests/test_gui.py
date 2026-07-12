import logging
import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from studyflow.app import create_app
from studyflow.domain import ActivityState, Snapshot


class TkinterGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.window = create_app(self.root, Path(self.temp.name))
        self.root.update()
        self.root.withdraw()

    def tearDown(self):
        self.window.stop_scheduler()
        if self.window.service.segment_id:
            self.window.service.stop()
        self.window.database.connection.close()
        self.root.destroy()
        logging.shutdown()
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
            handler.close()
        self.temp.cleanup()

    def test_compact_bottom_right_window(self):
        geometry = self.root.geometry()
        self.assertTrue(geometry.startswith("430x150+"), geometry)
        self.assertEqual(self.root.cget("bg"), "white")
        self.assertFalse(bool(self.root.resizable()[0]))

    def test_toolbar_is_one_row_with_six_actions(self):
        buttons = [child for child in self.window.toolbar.winfo_children()
                   if isinstance(child, tk.Button)]
        self.assertEqual([button.cget("text") for button in buttons],
                         ["暂停", "历史", "设置", "清空", "隐藏", "退出"])
        self.assertEqual(len({button.pack_info()["side"] for button in buttons}), 1)

    def test_only_work_and_idle_values_are_prominent(self):
        self.window.set_snapshot(Snapshot(3661, 122, goal_seconds=14400))
        self.assertEqual(self.window.work_value.cget("text"), "01:01:01")
        self.assertEqual(self.window.idle_value.cget("text"), "00:02:02")
        self.assertEqual(self.window.work_value.cget("fg"), "black")
        self.assertEqual(self.window.idle_value.cget("fg"), "black")

    def test_pause_interaction(self):
        self.window.service.toggle_pause()
        self.assertIs(self.window.service.state, ActivityState.PAUSED)
        self.assertEqual(self.window.pause_button.cget("text"), "恢复")

    def test_clear_restarts_with_a_valid_segment(self):
        old_id = self.window.service.segment_id
        self.window.service.clear_and_restart()
        new_id = self.window.service.segment_id
        row = self.window.database.connection.execute(
            "SELECT id, end_utc FROM activity_segments"
        ).fetchone()
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(row["id"], new_id)
        self.assertIsNone(row["end_utc"])
        with patch("studyflow.timer_service.get_idle_seconds", return_value=0.0):
            self.window.service.tick()
        self.assertEqual(
            self.window.database.connection.execute(
                "SELECT COUNT(*) FROM activity_segments"
            ).fetchone()[0],
            1,
        )

    def test_work_stops_after_idle_threshold(self):
        self.window.database.set_setting("idle_threshold_seconds", 30)
        self.window.service.clear_and_restart()
        self.assertIs(self.window.service.state, ActivityState.WORKING)
        with patch("studyflow.timer_service.get_idle_seconds", return_value=30.0):
            self.window.service.tick()
        self.assertIs(self.window.service.state, ActivityState.WORKING)
        start = datetime.now(timezone.utc) - timedelta(seconds=31)
        self.window.database.connection.execute(
            "UPDATE activity_segments SET start_utc=? WHERE id=?",
            (start.isoformat(), self.window.service.segment_id),
        )
        self.window.database.connection.commit()
        with patch("studyflow.timer_service.get_idle_seconds", return_value=31.0):
            self.window.service.tick()
        self.assertIs(self.window.service.state, ActivityState.IDLE)
        working = self.window.database.connection.execute(
            "SELECT end_utc FROM activity_segments WHERE state='working' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        idle = self.window.database.connection.execute(
            "SELECT end_utc FROM activity_segments WHERE state='idle' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(working["end_utc"])
        self.assertIsNone(idle["end_utc"])
        self.assertEqual(self.window.work_value.cget("text"), "00:00:30")
        self.assertEqual(self.window.idle_value.cget("text"), "00:00:01")

    def test_clear_forces_working_even_if_system_was_already_idle(self):
        with patch("studyflow.timer_service.get_idle_seconds", return_value=999.0):
            self.window.service.clear_and_restart()
        self.assertIs(self.window.service.state, ActivityState.WORKING)
        row = self.window.database.connection.execute(
            "SELECT state, end_utc FROM activity_segments"
        ).fetchone()
        self.assertEqual(row["state"], "working")
        self.assertIsNone(row["end_utc"])


if __name__ == "__main__":
    unittest.main()
