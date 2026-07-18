import logging
import tempfile
import tkinter as tk
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

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
        self.assertFalse(bool(self.root.attributes("-topmost")))

    def test_window_is_kept_inside_work_area(self):
        self.root.deiconify()
        with patch.object(self.window, "_work_area", return_value=(0, 0, 800, 600)):
            self.root.geometry("430x150+700+550")
            self.root.update_idletasks()
            self.window._constrain_to_work_area()
        self.assertLessEqual(self.root.winfo_x(), 370)
        self.assertLessEqual(self.root.winfo_y(), 450)
        self.assertGreaterEqual(self.root.winfo_x(), 0)
        self.assertGreaterEqual(self.root.winfo_y(), 0)
        self.root.withdraw()

    def test_window_is_placed_above_taskbar_work_area(self):
        self.root.deiconify()
        self.root.update_idletasks()
        with patch.object(self.window, "_work_area", return_value=(0, 0, 1920, 1040)):
            outer_width, outer_height = self.window._outer_window_size()
            self.window._place_bottom_right()
            self.root.update_idletasks()
        self.assertEqual(self.root.winfo_x(), 1920 - outer_width - 8)
        self.assertEqual(self.root.winfo_y(), 1040 - outer_height - 8)
        self.assertLessEqual(self.root.winfo_y() + outer_height, 1040)
        self.root.withdraw()

    def test_pause_is_on_the_same_top_menu_row(self):
        self.assertEqual(self.window.menu_bar.entrycget(0, "label"), "文件")
        self.assertEqual(self.window.menu_bar.entrycget(1, "label"), "重置时间")
        self.assertEqual(self.window.menu_bar.entrycget(2, "label"), "暂停")
        self.assertFalse(hasattr(self.window, "toolbar"))

    def test_file_menu_contains_statistics_settings_and_exit(self):
        labels = [self.window.file_menu.entrycget(index, "label")
                  for index in (0, 1, 3)]
        self.assertEqual(labels, ["统计", "设置", "退出"])
        self.assertEqual(self.window.menu_bar.entrycget(1, "label"), "重置时间")

    def test_only_work_and_idle_values_are_prominent(self):
        self.window.set_snapshot(Snapshot(3661, 122, goal_seconds=14400))
        self.assertEqual(self.window.work_value.cget("text"), "01:01:01")
        self.assertEqual(self.window.idle_value.cget("text"), "00:02:02")
        self.assertEqual(self.window.work_value.cget("fg"), "black")
        self.assertEqual(self.window.idle_value.cget("fg"), "black")
        self.assertEqual(self.window.work_value.pack_info()["anchor"], "center")
        self.assertEqual(self.window.idle_value.pack_info()["anchor"], "center")
        self.assertEqual(self.window.work_value.master.pack_info()["expand"], 1)
        self.assertEqual(self.window.idle_value.master.pack_info()["expand"], 1)
        separator_frames = [
            child for child in self.root.winfo_children()
            if isinstance(child, tk.Frame) and child.cget("height") == 1
        ]
        self.assertEqual(separator_frames, [])

    def test_pause_interaction(self):
        self.window.service.toggle_pause()
        self.assertIs(self.window.service.state, ActivityState.PAUSED)
        self.assertEqual(self.window.menu_bar.entrycget(2, "label"), "恢复")

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
        self.window.service.session_started_at = start
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

    def test_reset_time_zeros_session_but_preserves_daily_statistics(self):
        self.window.service.session_started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        self.window.service.refresh()
        daily_before = self.window.database.snapshot(date.today(), 14400).working_seconds
        self.window.service.reset_session()
        self.assertEqual(self.window.work_value.cget("text"), "00:00:00")
        self.assertEqual(self.window.idle_value.cget("text"), "00:00:00")
        daily_after = self.window.database.snapshot(date.today(), 14400).working_seconds
        self.assertGreaterEqual(daily_after, daily_before)

    @patch("studyflow.main_window.messagebox.askyesno", return_value=True)
    def test_statistics_clear_deletes_records_and_restarts(self, _dialog):
        table = MagicMock()
        table.winfo_toplevel.return_value = self.root
        with patch.object(self.window, "_populate_statistics") as populate:
            self.window.clear_statistics(table)
        count = self.window.database.connection.execute(
            "SELECT COUNT(*) FROM activity_segments"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertIs(self.window.service.state, ActivityState.WORKING)
        populate.assert_called_once_with(table)

    @patch("studyflow.main_window.messagebox.askyesno", return_value=False)
    def test_statistics_clear_cancel_keeps_records(self, _dialog):
        before = self.window.database.connection.execute(
            "SELECT COUNT(*) FROM activity_segments"
        ).fetchone()[0]
        table = MagicMock()
        table.winfo_toplevel.return_value = self.root
        self.window.clear_statistics(table)
        after = self.window.database.connection.execute(
            "SELECT COUNT(*) FROM activity_segments"
        ).fetchone()[0]
        self.assertEqual(after, before)

    @patch("studyflow.main_window.messagebox.askyesnocancel", return_value=False)
    def test_close_no_hides_to_system_tray(self, _dialog):
        with patch.object(self.root, "withdraw") as withdraw:
            self.window.handle_close()
        withdraw.assert_called_once_with()
        self.assertTrue(self.window.service.segment_id)

    @patch("studyflow.main_window.messagebox.askyesnocancel", return_value=None)
    def test_close_cancel_keeps_window_and_timer(self, _dialog):
        with patch.object(self.root, "withdraw") as withdraw:
            self.window.handle_close()
        withdraw.assert_not_called()
        self.assertTrue(self.window.service.segment_id)

    @patch("studyflow.main_window.messagebox.askyesnocancel", return_value=True)
    def test_close_yes_exits_and_stops_timer(self, _dialog):
        with patch.object(self.root, "destroy") as destroy:
            self.window.handle_close()
        destroy.assert_called_once_with()
        self.assertEqual(self.window.service.segment_id, 0)

    def test_windows_shutdown_saves_then_stops(self):
        with patch.object(self.window.service, "save_checkpoint") as save:
            result = self.window._handle_shutdown_message(0x0011, 0)
        self.assertEqual(result, 1)
        save.assert_called_once_with()
        with patch.object(self.window.service, "stop") as stop:
            result = self.window._handle_shutdown_message(0x0016, 1)
        self.assertEqual(result, 0)
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
