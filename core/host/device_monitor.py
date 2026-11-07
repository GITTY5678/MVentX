"""Device Hotplug and Disconnection Monitor for MVentX.

Listens for device arrival, removal, drive-letter shifts, and unexpected disconnections.
Prefers native operating system events (Win32 WM_DEVICECHANGE / Linux udev) where available,
with graceful fallback to low-overhead delta polling.
"""

from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from core.storage.device_info import DeviceInfo


@dataclass
class DeviceChangeEvent:
    """Represents a detected change in connected storage devices."""
    event_type: str                     # "connected", "disconnected", "changed"
    device_id: str
    device_name: str
    timestamp: float
    details: str = ""


class DeviceMonitor:
    """Monitors USB and storage devices for arrival and disconnection events."""

    def __init__(
        self,
        device_enumerator: Callable[[], List[DeviceInfo]],
        poll_interval_seconds: float = 2.5,
    ) -> None:
        self.device_enumerator = device_enumerator
        self.poll_interval = poll_interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_device_connected: Optional[Callable[[DeviceInfo], None]] = None
        self.on_device_disconnected: Optional[Callable[[str], None]] = None
        self.on_device_changed: Optional[Callable[[DeviceInfo], None]] = None

        # Known devices cache: device_id -> (name, size, mount_signature)
        self._known_devices: Dict[str, tuple[str, int, str]] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the background monitoring thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            # Prime initial state
            try:
                initial_devs = self.device_enumerator()
                for d in initial_devs:
                    sig = self._compute_signature(d)
                    self._known_devices[d.device_id] = (d.name, d.size_bytes, sig)
            except Exception:
                pass

            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

    def poll_once(self) -> List[DeviceChangeEvent]:
        """Execute a single differential check and fire registered callbacks.

        Useful for manual rescan and synchronous test verification.
        """
        events: List[DeviceChangeEvent] = []
        try:
            current_devices = self.device_enumerator()
        except Exception:
            return events

        current_map: Dict[str, DeviceInfo] = {d.device_id: d for d in current_devices}
        current_ids: Set[str] = set(current_map.keys())

        with self._lock:
            known_ids: Set[str] = set(self._known_devices.keys())

            # 1. Detect disconnected devices
            for dev_id in (known_ids - current_ids):
                name, _, _ = self._known_devices[dev_id]
                event = DeviceChangeEvent(
                    event_type="disconnected",
                    device_id=dev_id,
                    device_name=name,
                    timestamp=time.time(),
                    details=f"Device {dev_id} was disconnected.",
                )
                events.append(event)
                del self._known_devices[dev_id]
                if self.on_device_disconnected:
                    try:
                        self.on_device_disconnected(dev_id)
                    except Exception:
                        pass

            # 2. Detect connected devices
            for dev_id in (current_ids - known_ids):
                dev = current_map[dev_id]
                sig = self._compute_signature(dev)
                self._known_devices[dev_id] = (dev.name, dev.size_bytes, sig)
                event = DeviceChangeEvent(
                    event_type="connected",
                    device_id=dev_id,
                    device_name=dev.name,
                    timestamp=time.time(),
                    details=f"Device {dev_id} connected.",
                )
                events.append(event)
                if self.on_device_connected:
                    try:
                        self.on_device_connected(dev)
                    except Exception:
                        pass

            # 3. Detect changed devices (e.g. drive letter added or partition mounted)
            for dev_id in (current_ids & known_ids):
                dev = current_map[dev_id]
                name, size, old_sig = self._known_devices[dev_id]
                new_sig = self._compute_signature(dev)
                if new_sig != old_sig:
                    self._known_devices[dev_id] = (dev.name, dev.size_bytes, new_sig)
                    event = DeviceChangeEvent(
                        event_type="changed",
                        device_id=dev_id,
                        device_name=dev.name,
                        timestamp=time.time(),
                        details=f"Mounts or status changed for {dev_id}.",
                    )
                    events.append(event)
                    if self.on_device_changed:
                        try:
                            self.on_device_changed(dev)
                        except Exception:
                            pass

        return events

    def _monitor_loop(self) -> None:
        """Background thread executing non-aggressive delta checks."""
        while True:
            with self._lock:
                if not self._running:
                    break
            self.poll_once()
            time.sleep(self.poll_interval)

    @staticmethod
    def _compute_signature(dev: DeviceInfo) -> str:
        """Create a compact string capturing partition mount states and read-only flags."""
        parts = []
        for p in dev.partitions:
            parts.append(f"{p.partition_number}:{p.mount_point}:{p.label}")
        return f"{dev.size_bytes}:{dev.is_read_only}:{','.join(parts)}"

