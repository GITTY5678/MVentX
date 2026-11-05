"""ISO Registration Service.

Handles manual registration, renaming, reordering, enabling, disabling,
and deregistration of ISO images within the MVentX configuration store.

Architectural Rule: No automatic ISO discovery is performed. All environments
must be explicitly registered by the user.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class EnvironmentRecord:
    """Represents a single registered boot environment."""
    id: str
    display_name: str
    type: str  # 'iso' or 'chainload'
    boot_method: str
    enabled: bool
    order: int
    iso_path: Optional[str] = None
    description: Optional[str] = None
    custom_args: Optional[str] = None
    target_path: Optional[str] = None
    target_hint: Optional[str] = None


class ISORegistrationManager:
    """Manages the lifecycle of registered boot environments on the MVentX USB drive."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def list_environments(self) -> List[EnvironmentRecord]:
        """Return all registered environments ordered by their display sequence."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

    def register_iso(
        self,
        iso_path: Path,
        display_name: str,
        boot_method: str,
        custom_args: Optional[str] = None,
        enabled: bool = True,
    ) -> EnvironmentRecord:
        """Manually register a new ISO image into the environment store."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

    def update_environment(
        self,
        env_id: str,
        display_name: Optional[str] = None,
        enabled: Optional[bool] = None,
        order: Optional[int] = None,
        custom_args: Optional[str] = None,
    ) -> EnvironmentRecord:
        """Update existing environment attributes such as display name or order."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

    def remove_environment(self, env_id: str, delete_file: bool = False) -> bool:
        """Remove an environment from the registry, optionally deleting the ISO file."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

    def reorder_environments(self, ordered_ids: List[str]) -> None:
        """Update display order for all registered environments."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

