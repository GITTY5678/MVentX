"""MVentX ISO Management Subsystem.

Provides manual registration, metadata extraction, and validation routines
for ISO images and boot environments.
"""

from typing import List

from core.iso_manager.metadata import ISOMetadata, ISOMetadataExtractor
from core.iso_manager.registration import (
    DuplicateRegistrationError,
    EnvironmentNotFoundError,
    EnvironmentRecord,
    ISORegistrationManager,
    MenuSyncRollbackError,
    PathTraversalError,
    RegistrationError,
    StorageMetrics,
)
from core.iso_manager.validation import (
    ISOValidationError,
    ISOValidationResult,
    ISOValidator,
)
from core.iso_manager.windows_iso_inspector import (
    WindowsArchitecture,
    WindowsInspectionResult,
    WindowsISOInspector,
    WindowsPayloadType,
)
from core.iso_manager.windows_media_manager import (
    ExtractionIntegrityError,
    ExtractionInterruptedError,
    InsufficientStorageError,
    StorageRequirement,
    WindowsMediaManager,
    WindowsStorageError,
)

__all__: List[str] = [
    "DuplicateRegistrationError",
    "EnvironmentNotFoundError",
    "EnvironmentRecord",
    "ISOMetadata",
    "ISOMetadataExtractor",
    "ISORegistrationManager",
    "ISOValidationError",
    "ISOValidationResult",
    "ISOValidator",
    "MenuSyncRollbackError",
    "PathTraversalError",
    "RegistrationError",
    "StorageMetrics",
    "WindowsArchitecture",
    "WindowsInspectionResult",
    "WindowsISOInspector",
    "WindowsPayloadType",
    "WindowsMediaManager",
    "StorageRequirement",
    "WindowsStorageError",
    "InsufficientStorageError",
    "ExtractionInterruptedError",
    "ExtractionIntegrityError",
]
