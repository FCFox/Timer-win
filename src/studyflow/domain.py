from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class ActivityState(StrEnum):
    WORKING = "working"
    IDLE = "idle"
    PAUSED = "paused"
    UNTRACKED = "untracked"


@dataclass(frozen=True, slots=True)
class ActivitySegment:
    id: int
    state: ActivityState
    start_utc: datetime
    end_utc: datetime | None
    duration_seconds: int
    source: str = "automatic"
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    working_seconds: int = 0
    idle_seconds: int = 0
    paused_seconds: int = 0
    untracked_seconds: int = 0
    longest_working_seconds: int = 0
    goal_seconds: int = 4 * 3600

    @property
    def tracked_seconds(self) -> int:
        return self.working_seconds + self.idle_seconds

    @property
    def work_ratio(self) -> float:
        return self.working_seconds / self.tracked_seconds if self.tracked_seconds else 0.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def desired_state(*, paused: bool, session_available: bool, idle_seconds: float,
                  threshold_seconds: int) -> ActivityState:
    if paused:
        return ActivityState.PAUSED
    if not session_available:
        return ActivityState.UNTRACKED
    # The threshold itself belongs to working time: a 30-second threshold means
    # 0..30 seconds are working and idle begins only after 30 seconds.
    return ActivityState.WORKING if idle_seconds <= threshold_seconds else ActivityState.IDLE
