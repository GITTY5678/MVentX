"""Partition Planning Subsystem for MVentX.

Calculates and validates exact GPT sector boundaries, 1 MiB alignment,
and sizing for MVX_BOOT (FAT32 ESP) and MVX_DATA (exFAT Basic Data).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Optional, Tuple

# Standard Partition Type GUIDs
GUID_EFI_SYSTEM = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
GUID_BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"

# Standard Sector Sizes and Alignment
SECTOR_SIZE_DEFAULT = 512
ALIGNMENT_SECTORS_1MB = 2048  # 1 MiB = 2048 sectors of 512 bytes

# Practical Capacity Thresholds
MIN_ESP_SIZE_MB = 32          # Absolute minimum to hold 2.5MB EFI + 15MB modules + FAT32 structures
DEFAULT_ESP_SIZE_MB = 64      # Standard recommended EFI partition size
MIN_DISK_CAPACITY_MB = 128    # Minimum virtual disk capacity to allow dual partition creation


class PartitionPlanError(Exception):
    """Raised when partition sizing or boundary validation fails."""


@dataclass
class PlannedPartition:
    """Calculated partition geometry and metadata."""
    partition_number: int
    name: str
    label: str
    type_guid: str
    start_lba: int
    end_lba: int
    filesystem: str

    @property
    def sector_count(self) -> int:
        return self.end_lba - self.start_lba + 1

    @property
    def size_bytes(self) -> int:
        return self.sector_count * SECTOR_SIZE_DEFAULT

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 ** 2), 2)


@dataclass
class PartitionPlan:
    """Complete GPT layout plan for a target disk."""
    total_disk_bytes: int
    sector_size: int
    total_sectors: int
    first_usable_lba: int
    last_usable_lba: int
    esp_partition: PlannedPartition
    data_partition: PlannedPartition

    @property
    def partitions(self) -> List[PlannedPartition]:
        return [self.esp_partition, self.data_partition]


class PartitionPlanner:
    """Generates and validates partition plans for dual-partition GPT disks."""

    @classmethod
    def calculate_plan(
        cls,
        disk_size_bytes: int,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
        sector_size: int = SECTOR_SIZE_DEFAULT,
    ) -> PartitionPlan:
        """Calculate sector bounds for MVX_BOOT and MVX_DATA.

        Raises:
            PartitionPlanError: If disk capacity is insufficient or parameters invalid.
        """
        if sector_size != 512:
            raise PartitionPlanError(f"Unsupported sector size: {sector_size}. Only 512-byte sectors supported.")

        disk_size_mb = disk_size_bytes / (1024 ** 2)
        if disk_size_mb < MIN_DISK_CAPACITY_MB:
            raise PartitionPlanError(
                f"Disk capacity ({disk_size_mb:.1f} MiB) is smaller than the minimum required "
                f"capacity ({MIN_DISK_CAPACITY_MB} MiB) for MVentX dual-partition storage."
            )

        if esp_size_mb < MIN_ESP_SIZE_MB:
            raise PartitionPlanError(
                f"Requested EFI partition size ({esp_size_mb} MiB) is smaller than the minimum practical "
                f"size ({MIN_ESP_SIZE_MB} MiB) required for GRUB 2.12 EFI binary and modules."
            )

        total_sectors = disk_size_bytes // sector_size

        # GPT ReservedLBAs:
        # LBA 0: Protective MBR
        # LBA 1: Primary GPT Header
        # LBA 2..33: Primary Partition Table Array (32 sectors for 128 entries of 128 bytes)
        # First usable sector aligned to 1 MiB = LBA 2048
        first_usable_lba = ALIGNMENT_SECTORS_1MB

        # Backup GPT:
        # LBA total_sectors - 33..total_sectors - 2: Backup Partition Table Array
        # LBA total_sectors - 1: Backup GPT Header
        last_usable_lba = total_sectors - 34

        if first_usable_lba >= last_usable_lba:
            raise PartitionPlanError("Disk too small to fit GPT partition table structures.")

        # 1. Calculate ESP (Partition 1: MVX_BOOT)
        esp_sectors_requested = (esp_size_mb * 1024 * 1024) // sector_size
        # Align to 1 MiB boundary
        esp_sectors = math.ceil(esp_sectors_requested / ALIGNMENT_SECTORS_1MB) * ALIGNMENT_SECTORS_1MB

        esp_start = first_usable_lba
        esp_end = esp_start + esp_sectors - 1

        # 2. Calculate Data (Partition 2: MVX_DATA)
        # Next partition starts at 1 MiB aligned boundary after ESP
        data_start = esp_end + 1
        if data_start % ALIGNMENT_SECTORS_1MB != 0:
            data_start = math.ceil(data_start / ALIGNMENT_SECTORS_1MB) * ALIGNMENT_SECTORS_1MB

        # Data partition extends to last usable LBA (aligned down to 1 MiB)
        data_end = last_usable_lba
        data_sectors = data_end - data_start + 1

        min_data_sectors = (32 * 1024 * 1024) // sector_size  # At least 32 MiB for data
        if data_sectors < min_data_sectors:
            raise PartitionPlanError(
                f"Insufficient space for data partition ({data_sectors * sector_size / (1024**2):.1f} MiB). "
                f"Reduce EFI partition size or use a larger storage device."
            )

        esp_part = PlannedPartition(
            partition_number=1,
            name="MVX_BOOT",
            label="MVX_BOOT",
            type_guid=GUID_EFI_SYSTEM,
            start_lba=esp_start,
            end_lba=esp_end,
            filesystem="FAT32",
        )

        data_part = PlannedPartition(
            partition_number=2,
            name="MVX_DATA",
            label="MVX_DATA",
            type_guid=GUID_BASIC_DATA,
            start_lba=data_start,
            end_lba=data_end,
            filesystem="exFAT",
        )

        plan = PartitionPlan(
            total_disk_bytes=disk_size_bytes,
            sector_size=sector_size,
            total_sectors=total_sectors,
            first_usable_lba=first_usable_lba,
            last_usable_lba=last_usable_lba,
            esp_partition=esp_part,
            data_partition=data_part,
        )

        cls.validate_plan(plan)
        return plan

    @classmethod
    def validate_plan(cls, plan: PartitionPlan) -> None:
        """Verify boundary alignment, non-overlapping sectors, and LBA limits."""
        # 1. Usable bounds
        if plan.first_usable_lba < 34:
            raise PartitionPlanError(f"first_usable_lba ({plan.first_usable_lba}) overlaps primary GPT header")
        if plan.last_usable_lba > plan.total_sectors - 34:
            raise PartitionPlanError(f"last_usable_lba ({plan.last_usable_lba}) overlaps backup GPT header")

        # 2. 1 MiB Alignment check (start_lba % 2048 == 0)
        for part in plan.partitions:
            if part.start_lba % ALIGNMENT_SECTORS_1MB != 0:
                raise PartitionPlanError(
                    f"Partition {part.partition_number} ({part.name}) start LBA {part.start_lba} "
                    f"is not aligned to 1 MiB ({ALIGNMENT_SECTORS_1MB} sectors)."
                )

        # 3. Overlap check
        if plan.esp_partition.end_lba >= plan.data_partition.start_lba:
            raise PartitionPlanError(
                f"Partition overlap detected: ESP ends at {plan.esp_partition.end_lba}, "
                f"Data begins at {plan.data_partition.start_lba}."
            )

        # 4. End bounds check
        if plan.data_partition.end_lba > plan.last_usable_lba:
            raise PartitionPlanError(
                f"Data partition end LBA ({plan.data_partition.end_lba}) exceeds last usable LBA ({plan.last_usable_lba})."
            )

