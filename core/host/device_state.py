"""Device Lifecycle State Machine for MVentX Host Integration.

Defines an explicit state machine tracking storage devices through discovery,
identification, validation, operational readiness, active management, safe ejection,
and unexpected disconnection.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Set


class DeviceLifecycleState(str, Enum):
    """Explicit device lifecycle states for MVentX storage devices."""
    DETECTED = "detected"              # Raw device visible on host bus
    IDENTIFYING = "identifying"        # Extracting GPT GUIDs, hardware serials, disk metrics
    IDENTIFIED = "identified"          # Strong device identity established
    VALIDATING = "validating"          # Checking partition types, labels, directory skeleton, schema
    READY = "ready"                    # Valid MVentX device, mounted & verified for management
    BUSY = "busy"                      # Active I/O, file copying, or menu synchronization in progress
    INCOMPLETE = "incomplete"          # Incomplete installation (missing partition, bootloader, skeleton)
    CORRUPTED = "corrupted"            # Corrupted partition table, unparseable JSON, or schema error
    ORDINARY = "ordinary"              # Generic storage drive without MVentX partitions
    INACCESSIBLE = "inaccessible"      # Device locked, unmounted, or permission denied
    EJECTING = "ejecting"              # Safe ejection sequence in progress (flushing, unmounting)
    EJECTED = "ejected"                # Successfully unmounted and safely detached by OS
    DISCONNECTED = "disconnected"      # Physical or virtual disconnection detected
    ERROR = "error"                    # Operation failed or safety check violation


# Valid state transitions
VALID_TRANSITIONS: dict[DeviceLifecycleState, Set[DeviceLifecycleState]] = {
    DeviceLifecycleState.DETECTED: {
        DeviceLifecycleState.IDENTIFYING,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.IDENTIFYING: {
        DeviceLifecycleState.IDENTIFIED,
        DeviceLifecycleState.INACCESSIBLE,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.IDENTIFIED: {
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.ORDINARY,
        DeviceLifecycleState.INACCESSIBLE,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.VALIDATING: {
        DeviceLifecycleState.READY,
        DeviceLifecycleState.INCOMPLETE,
        DeviceLifecycleState.CORRUPTED,
        DeviceLifecycleState.ORDINARY,
        DeviceLifecycleState.INACCESSIBLE,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.READY: {
        DeviceLifecycleState.BUSY,
        DeviceLifecycleState.EJECTING,
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.INACCESSIBLE,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.BUSY: {
        DeviceLifecycleState.READY,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.INCOMPLETE: {
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.EJECTING,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.CORRUPTED: {
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.EJECTING,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.ORDINARY: {
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.EJECTING,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.INACCESSIBLE: {
        DeviceLifecycleState.IDENTIFYING,
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.EJECTING,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.EJECTING: {
        DeviceLifecycleState.EJECTED,
        DeviceLifecycleState.READY,
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.ERROR,
    },
    DeviceLifecycleState.EJECTED: {
        DeviceLifecycleState.DISCONNECTED,
        DeviceLifecycleState.DETECTED,
    },
    DeviceLifecycleState.DISCONNECTED: {
        DeviceLifecycleState.DETECTED,
    },
    DeviceLifecycleState.ERROR: {
        DeviceLifecycleState.DETECTED,
        DeviceLifecycleState.VALIDATING,
        DeviceLifecycleState.READY,
        DeviceLifecycleState.DISCONNECTED,
    },
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal device state machine transition is attempted."""


class DeviceStateMachine:
    """Manages explicit state transitions for a connected storage device."""

    def __init__(self, initial_state: DeviceLifecycleState = DeviceLifecycleState.DETECTED) -> None:
        self._current_state = initial_state
        self._history: List[tuple[DeviceLifecycleState, Optional[str]]] = [(initial_state, "Initialization")]

    @property
    def current_state(self) -> DeviceLifecycleState:
        return self._current_state

    @property
    def history(self) -> List[tuple[DeviceLifecycleState, Optional[str]]]:
        return list(self._history)

    def transition_to(self, new_state: DeviceLifecycleState, reason: Optional[str] = None) -> None:
        """Transition device to a new state if valid, or raise InvalidStateTransitionError."""
        if new_state == self._current_state:
            return

        valid_targets = VALID_TRANSITIONS.get(self._current_state, set())
        if new_state not in valid_targets:
            raise InvalidStateTransitionError(
                f"Illegal state transition from {self._current_state.value} to {new_state.value}. "
                f"Reason: {reason or 'Not specified'}"
            )

        self._current_state = new_state
        self._history.append((new_state, reason))

    def can_transition_to(self, new_state: DeviceLifecycleState) -> bool:
        """Check if transition to new_state is allowed from current state."""
        if new_state == self._current_state:
            return True
        return new_state in VALID_TRANSITIONS.get(self._current_state, set())

    @property
    def is_operational(self) -> bool:
        """True if device is in a state where safe management operations may proceed."""
        return self._current_state == DeviceLifecycleState.READY

    @property
    def is_busy(self) -> bool:
        """True if an operation is currently in progress."""
        return self._current_state == DeviceLifecycleState.BUSY

    @property
    def is_disconnected(self) -> bool:
        """True if device has been unplugged or lost."""
        return self._current_state in (DeviceLifecycleState.DISCONNECTED, DeviceLifecycleState.EJECTED)

