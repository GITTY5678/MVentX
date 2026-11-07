"""Unit tests for MVentX Device Lifecycle State Machine."""

import unittest

from core.host.device_state import (
    DeviceLifecycleState,
    DeviceStateMachine,
    InvalidStateTransitionError,
)


class TestDeviceStateMachine(unittest.TestCase):
    """Test explicit device lifecycle transitions."""

    def test_initial_state(self):
        sm = DeviceStateMachine(DeviceLifecycleState.DETECTED)
        self.assertEqual(sm.current_state, DeviceLifecycleState.DETECTED)
        self.assertFalse(sm.is_operational)
        self.assertFalse(sm.is_busy)
        self.assertFalse(sm.is_disconnected)
        self.assertEqual(len(sm.history), 1)

    def test_valid_lifecycle_progression(self):
        sm = DeviceStateMachine(DeviceLifecycleState.DETECTED)
        sm.transition_to(DeviceLifecycleState.IDENTIFYING, "Probing hardware")
        self.assertEqual(sm.current_state, DeviceLifecycleState.IDENTIFYING)

        sm.transition_to(DeviceLifecycleState.IDENTIFIED, "Identity confirmed")
        self.assertEqual(sm.current_state, DeviceLifecycleState.IDENTIFIED)

        sm.transition_to(DeviceLifecycleState.VALIDATING, "Validating schema")
        self.assertEqual(sm.current_state, DeviceLifecycleState.VALIDATING)

        sm.transition_to(DeviceLifecycleState.READY, "Valid MVentX device ready")
        self.assertEqual(sm.current_state, DeviceLifecycleState.READY)
        self.assertTrue(sm.is_operational)

        sm.transition_to(DeviceLifecycleState.BUSY, "Writing ISO")
        self.assertEqual(sm.current_state, DeviceLifecycleState.BUSY)
        self.assertTrue(sm.is_busy)

        sm.transition_to(DeviceLifecycleState.READY, "Write complete")
        self.assertEqual(sm.current_state, DeviceLifecycleState.READY)

        sm.transition_to(DeviceLifecycleState.EJECTING, "Ejection started")
        self.assertEqual(sm.current_state, DeviceLifecycleState.EJECTING)

        sm.transition_to(DeviceLifecycleState.EJECTED, "Ejected by OS")
        self.assertEqual(sm.current_state, DeviceLifecycleState.EJECTED)
        self.assertTrue(sm.is_disconnected)

    def test_illegal_transition_raises_error(self):
        sm = DeviceStateMachine(DeviceLifecycleState.DETECTED)
        # Cannot jump directly from DETECTED to READY or BUSY
        with self.assertRaises(InvalidStateTransitionError):
            sm.transition_to(DeviceLifecycleState.READY, "Illegal jump")

        with self.assertRaises(InvalidStateTransitionError):
            sm.transition_to(DeviceLifecycleState.BUSY, "Illegal jump")

    def test_unexpected_disconnection_from_any_active_state(self):
        states_allowing_disconnect = [
            DeviceLifecycleState.DETECTED,
            DeviceLifecycleState.IDENTIFYING,
            DeviceLifecycleState.IDENTIFIED,
            DeviceLifecycleState.VALIDATING,
            DeviceLifecycleState.READY,
            DeviceLifecycleState.BUSY,
            DeviceLifecycleState.INCOMPLETE,
            DeviceLifecycleState.CORRUPTED,
            DeviceLifecycleState.ORDINARY,
            DeviceLifecycleState.INACCESSIBLE,
        ]
        for st in states_allowing_disconnect:
            sm = DeviceStateMachine(st)
            sm.transition_to(DeviceLifecycleState.DISCONNECTED, "Unplugged")
            self.assertEqual(sm.current_state, DeviceLifecycleState.DISCONNECTED)
            self.assertTrue(sm.is_disconnected)


if __name__ == "__main__":
    unittest.main()

