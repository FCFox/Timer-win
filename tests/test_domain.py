import unittest

from studyflow.domain import ActivityState, desired_state


class StateMachineTests(unittest.TestCase):
    def test_state_priority_paused_over_session_and_idle(self):
        self.assertIs(desired_state(paused=True, session_available=False, idle_seconds=999,
                                    threshold_seconds=300), ActivityState.PAUSED)

    def test_state_priority_session_unavailable(self):
        self.assertIs(desired_state(paused=False, session_available=False, idle_seconds=0,
                                    threshold_seconds=300), ActivityState.UNTRACKED)

    def test_working_and_idle_boundary(self):
        self.assertIs(desired_state(paused=False, session_available=True, idle_seconds=299.9,
                                    threshold_seconds=300), ActivityState.WORKING)
        self.assertIs(desired_state(paused=False, session_available=True, idle_seconds=300,
                                    threshold_seconds=300), ActivityState.WORKING)
        self.assertIs(desired_state(paused=False, session_available=True, idle_seconds=300.001,
                                    threshold_seconds=300), ActivityState.IDLE)

    def test_zero_threshold_is_supported(self):
        self.assertIs(desired_state(paused=False, session_available=True, idle_seconds=0,
                                    threshold_seconds=0), ActivityState.WORKING)
        self.assertIs(desired_state(paused=False, session_available=True, idle_seconds=0.001,
                                    threshold_seconds=0), ActivityState.IDLE)


if __name__ == "__main__":
    unittest.main()
