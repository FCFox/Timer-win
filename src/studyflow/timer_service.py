from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from studyflow.domain import ActivityState, Snapshot, desired_state, utc_now
from studyflow.infrastructure import Database, get_idle_seconds


class TimerService:
    """UI-independent timer; a Tk `after` callback drives `tick` once a second."""

    def __init__(self, database: Database):
        self.database = database
        self.paused = False
        self.session_available = True
        self.state = ActivityState.UNTRACKED
        self.segment_id = 0
        self._checkpoint_ticks = 0
        self._state_callbacks: list[Callable[[ActivityState], None]] = []
        self._snapshot_callbacks: list[Callable[[Snapshot], None]] = []
        self._segment_callbacks: list[Callable[[list], None]] = []
        self.database.recover_open_segment()
        self._begin(self._calculate_state(), "automatic")

    @property
    def threshold(self) -> int:
        return int(self.database.get_setting("idle_threshold_seconds"))

    def on_state(self, callback: Callable[[ActivityState], None]) -> None:
        self._state_callbacks.append(callback)

    def on_snapshot(self, callback: Callable[[Snapshot], None]) -> None:
        self._snapshot_callbacks.append(callback)

    def on_segments(self, callback: Callable[[list], None]) -> None:
        self._segment_callbacks.append(callback)

    def _read_idle_seconds(self) -> float:
        try:
            return get_idle_seconds()
        except OSError:
            self.session_available = False
            return 0.0

    def _calculate_state(self, idle_seconds: float | None = None) -> ActivityState:
        idle = self._read_idle_seconds() if idle_seconds is None else idle_seconds
        return desired_state(paused=self.paused, session_available=self.session_available,
                             idle_seconds=idle, threshold_seconds=self.threshold)

    def _begin(self, state: ActivityState, source: str) -> None:
        self.state = state
        self.segment_id = self.database.start_segment(state, utc_now(), source)

    def tick(self) -> None:
        now = utc_now()
        idle_seconds = self._read_idle_seconds()
        next_state = self._calculate_state(idle_seconds)
        if next_state != self.state:
            boundary = now
            if self.state is ActivityState.WORKING and next_state is ActivityState.IDLE:
                boundary = now - timedelta(
                    seconds=max(0, idle_seconds - self.threshold)
                )
            self.segment_id = self.database.transition(self.segment_id, next_state, boundary)
            self.state = next_state
            for callback in self._state_callbacks:
                callback(self.state)
        self._checkpoint_ticks += 1
        if self._checkpoint_ticks >= 30:
            self.database.checkpoint(self.segment_id, now)
            self._checkpoint_ticks = 0
        self.refresh()

    def refresh(self) -> None:
        today = date.today()
        snapshot = self.database.snapshot(
            today, int(self.database.get_setting("daily_goal_seconds"))
        )
        segments = self.database.segments_for_date(today)
        for callback in self._snapshot_callbacks:
            callback(snapshot)
        for callback in self._segment_callbacks:
            callback(segments)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.tick()

    def set_session_available(self, available: bool) -> None:
        self.session_available = available
        self.tick()

    def clear_and_restart(self) -> None:
        """Delete history and immediately start a fresh segment for the current state."""
        self.database.clear_all()
        self._checkpoint_ticks = 0
        # Clearing is an explicit user action, so the new session always starts
        # as working regardless of the state of the segment that was deleted.
        self.paused = False
        self.session_available = True
        self._begin(ActivityState.WORKING, "manual")
        for callback in self._state_callbacks:
            callback(self.state)
        self.refresh()

    def stop(self) -> None:
        if self.segment_id:
            self.database.close_segment(self.segment_id, utc_now())
            self.segment_id = 0
