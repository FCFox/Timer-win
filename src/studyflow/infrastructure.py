from __future__ import annotations

import ctypes
import json
import os
import sqlite3
from ctypes import wintypes
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from studyflow.domain import ActivitySegment, ActivityState, Snapshot, utc_now


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_seconds() -> float:
    if os.name != "nt":
        return 0.0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    kernel32.GetTickCount64.argtypes = []
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo failed")
    tick = kernel32.GetTickCount64()
    milliseconds = ((tick & 0xFFFFFFFF) - info.dwTime) & 0xFFFFFFFF
    return milliseconds / 1000.0


DEFAULT_SETTINGS: dict[str, Any] = {
    "idle_threshold_seconds": 30,
    "daily_goal_seconds": 14400,
    "theme": "dark",
    "launch_at_login": False,
    "minimize_to_tray": True,
}


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS activity_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'automatic',
                note TEXT,
                created_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_segments_start ON activity_segments(start_utc);
            CREATE INDEX IF NOT EXISTS idx_segments_state ON activity_segments(state);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            PRAGMA user_version=1;
        """)
        self.connection.commit()

    def recover_open_segment(self) -> None:
        row = self.connection.execute(
            "SELECT * FROM activity_segments WHERE end_utc IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            start = datetime.fromisoformat(row["start_utc"])
            # Recover only the duration that was actually persisted by the
            # periodic checkpoint. Never infer time from the next launch.
            duration = max(0, int(row["duration_seconds"]))
            self._close(row["id"], start + timedelta(seconds=duration), duration)

    def start_segment(self, state: ActivityState, at: datetime, source: str) -> int:
        cur = self.connection.execute(
            "INSERT INTO activity_segments(state,start_utc,source,created_at_utc) VALUES(?,?,?,?)",
            (state.value, at.isoformat(), source, utc_now().isoformat()),
        )
        self.connection.commit()
        return int(cur.lastrowid)

    def _close(self, segment_id: int, at: datetime, duration: int) -> None:
        self.connection.execute(
            "UPDATE activity_segments SET end_utc=?, duration_seconds=? WHERE id=?",
            (at.isoformat(), max(0, duration), segment_id),
        )
        self.connection.commit()

    def transition(self, segment_id: int, state: ActivityState, at: datetime,
                   source: str = "automatic") -> int:
        row = self.connection.execute(
            "SELECT start_utc FROM activity_segments WHERE id=?", (segment_id,)
        ).fetchone()
        if row:
            start = datetime.fromisoformat(row["start_utc"])
            with self.connection:
                self.connection.execute(
                    "UPDATE activity_segments SET end_utc=?,duration_seconds=? WHERE id=?",
                    (at.isoformat(), max(0, int((at - start).total_seconds())), segment_id),
                )
                cur = self.connection.execute(
                    "INSERT INTO activity_segments(state,start_utc,source,created_at_utc) VALUES(?,?,?,?)",
                    (state.value, at.isoformat(), source, utc_now().isoformat()),
                )
            return int(cur.lastrowid)
        return self.start_segment(state, at, source)

    def checkpoint(self, segment_id: int, at: datetime) -> None:
        row = self.connection.execute(
            "SELECT start_utc FROM activity_segments WHERE id=?", (segment_id,)
        ).fetchone()
        if row:
            start = datetime.fromisoformat(row["start_utc"])
            self.connection.execute(
                "UPDATE activity_segments SET duration_seconds=? WHERE id=?",
                (max(0, int((at - start).total_seconds())), segment_id),
            )
            self.connection.commit()

    def close_segment(self, segment_id: int, at: datetime) -> None:
        self.checkpoint(segment_id, at)
        self.connection.execute("UPDATE activity_segments SET end_utc=? WHERE id=?", (at.isoformat(), segment_id))
        self.connection.commit()

    def segments_for_date(self, local_date: date) -> list[ActivitySegment]:
        local_tz = datetime.now().astimezone().tzinfo
        start = datetime.combine(local_date, time.min, local_tz).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        rows = self.connection.execute(
            "SELECT * FROM activity_segments WHERE start_utc < ? AND COALESCE(end_utc, ?) > ? ORDER BY start_utc",
            (end.isoformat(), utc_now().isoformat(), start.isoformat()),
        ).fetchall()
        result = []
        now = utc_now()
        for row in rows:
            seg_start = max(datetime.fromisoformat(row["start_utc"]), start)
            raw_end = datetime.fromisoformat(row["end_utc"]) if row["end_utc"] else now
            seg_end = min(raw_end, end)
            result.append(ActivitySegment(row["id"], ActivityState(row["state"]), seg_start,
                                          seg_end, max(0, int((seg_end-seg_start).total_seconds())),
                                          row["source"], row["note"]))
        return result

    def snapshot(self, local_date: date, goal_seconds: int) -> Snapshot:
        values = {state: 0 for state in ActivityState}
        longest = 0
        for segment in self.segments_for_date(local_date):
            values[segment.state] += segment.duration_seconds
            if segment.state is ActivityState.WORKING:
                longest = max(longest, segment.duration_seconds)
        return Snapshot(values[ActivityState.WORKING], values[ActivityState.IDLE],
                        values[ActivityState.PAUSED], values[ActivityState.UNTRACKED], longest,
                        goal_seconds)

    def get_setting(self, key: str) -> Any:
        row = self.connection.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else DEFAULT_SETTINGS[key]

    def set_setting(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_utc=excluded.updated_at_utc",
            (key, json.dumps(value), utc_now().isoformat()),
        )
        self.connection.commit()

    def clear_all(self) -> None:
        self.connection.execute("DELETE FROM activity_segments")
        self.connection.commit()


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        return
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            executable = Path(os.sys.executable)
            command = f'"{executable}" -m studyflow'
            winreg.SetValueEx(key, "StudyFlow", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "StudyFlow")
            except FileNotFoundError:
                pass
