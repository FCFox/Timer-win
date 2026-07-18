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
        self.session_started_at = utc_now()
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
        segments = self.database.segments_for_date(today)
        snapshot = self._session_snapshot(
            segments, int(self.database.get_setting("daily_goal_seconds"))
        )
        for callback in self._snapshot_callbacks:
            callback(snapshot)
        for callback in self._segment_callbacks:
            callback(segments)

    def _session_snapshot(self, segments: list, goal_seconds: int) -> Snapshot:
        totals = {state: 0 for state in ActivityState}
        longest = 0
        now = utc_now()
        for segment in segments:
            start = max(segment.start_utc, self.session_started_at)
            end = segment.end_utc or now
            duration = max(0, int((end - start).total_seconds()))
            totals[segment.state] += duration
            if segment.state is ActivityState.WORKING:
                longest = max(longest, duration)
        return Snapshot(
            totals[ActivityState.WORKING], totals[ActivityState.IDLE],
            totals[ActivityState.PAUSED], totals[ActivityState.UNTRACKED],
            longest, goal_seconds,
        )

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
        self.session_started_at = utc_now()
        # Clearing is an explicit user action, so the new session always starts
        # as working regardless of the state of the segment that was deleted.
        self.paused = False
        self.session_available = True
        self._begin(ActivityState.WORKING, "manual")
        for callback in self._state_callbacks:
            callback(self.state)
        self.refresh()

    def reset_session(self) -> None:
        """Reset the visible session clock without deleting daily history."""
        now = utc_now()
        self.paused = False
        self.session_available = True
        if self.segment_id:
            self.segment_id = self.database.transition(
                self.segment_id, ActivityState.WORKING, now, "manual"
            )
        else:
            self.segment_id = self.database.start_segment(
                ActivityState.WORKING, now, "manual"
            )
        self.state = ActivityState.WORKING
        self.session_started_at = now
        self._checkpoint_ticks = 0
        for callback in self._state_callbacks:
            callback(self.state)
        self.refresh()

    def stop(self) -> None:
        if self.segment_id:
            self.database.close_segment(self.segment_id, utc_now())
            self.segment_id = 0

    def save_checkpoint(self) -> None:
        """Persist current elapsed time without stopping the service."""
        if self.segment_id:
            self.database.checkpoint(self.segment_id, utc_now())
