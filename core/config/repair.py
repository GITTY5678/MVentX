"""Configuration Repair and Fault Recovery Engine for MVentX (Milestone 15).

Provides resilient mechanisms for detecting, salvaging, and recovering from:
- Truncated or malformed mventx.json files (e.g. from power failure during write)
- Corrupted environment records (salvages valid entries, repairs invalid ones)
- Schema deviations and missing required fields
- Automatic rollback to latest intact backup from MVX_DATA/backups/
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple

from core.config.validator import ConfigValidator
from core.iso_manager import EnvironmentRecord

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0.0",
    "meta": {
        "title": "MVentX Multiboot System",
        "theme": "mventx",
        "timeout": 10,
        "default_entry_id": None,
    },
    "environments": [],
}


class ConfigRepairResult:
    """Outcome of a configuration repair or recovery attempt."""

    def __init__(
        self,
        repaired: bool,
        recovered_from_backup: bool = False,
        backup_used: Optional[Path] = None,
        salvaged_count: int = 0,
        corrupted_count: int = 0,
        actions_taken: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.repaired = repaired
        self.recovered_from_backup = recovered_from_backup
        self.backup_used = backup_used
        self.salvaged_count = salvaged_count
        self.corrupted_count = corrupted_count
        self.actions_taken = actions_taken or []
        self.config = config or {}

    def summary(self) -> str:
        status = "SUCCESS" if self.repaired else "FAILED"
        actions = "\n".join(f"  - {a}" for a in self.actions_taken)
        return (
            f"ConfigRepair [{status}]:\n"
            f"  Recovered from backup: {self.recovered_from_backup} ({self.backup_used})\n"
            f"  Salvaged environments: {self.salvaged_count}, Discarded corrupt: {self.corrupted_count}\n"
            f"  Actions:\n{actions}"
        )


class ConfigRepairer:
    """Repairs damaged mventx.json files, salvages intact environments, and restores from backups."""

    @classmethod
    def load_or_recover(
        cls,
        config_path: Path | str,
        backups_dir: Optional[Path | str] = None,
        iso_dir: Optional[Path | str] = None,
    ) -> Tuple[Dict[str, Any], ConfigRepairResult]:
        """Attempt to load mventx.json; if missing or corrupt, automatically repair or recover.

        Guarantees that files in iso_dir are never touched or deleted.
        """
        c_path = Path(config_path)
        b_dir = Path(backups_dir) if backups_dir else c_path.parent.parent / "backups"
        actions: List[str] = []

        # 1. File does not exist -> check backups or create default
        if not c_path.is_file():
            actions.append(f"Configuration file {c_path.name} was missing.")
            backup_cfg, used_backup = cls._find_latest_valid_backup(b_dir)
            if backup_cfg:
                actions.append(f"Restored configuration from intact backup: {used_backup.name}")
                cls._write_json(c_path, backup_cfg)
                return backup_cfg, ConfigRepairResult(
                    repaired=True,
                    recovered_from_backup=True,
                    backup_used=used_backup,
                    salvaged_count=len(backup_cfg.get("environments", [])),
                    actions_taken=actions,
                    config=backup_cfg,
                )
            else:
                actions.append("No valid backups found. Reinitialized default configuration (ISO files preserved).")
                default_cfg = dict(DEFAULT_CONFIG)
                cls._write_json(c_path, default_cfg)
                return default_cfg, ConfigRepairResult(
                    repaired=True,
                    recovered_from_backup=False,
                    actions_taken=actions,
                    config=default_cfg,
                )

        # 2. File exists -> try parsing
        content = ""
        try:
            with open(c_path, "r", encoding="utf-8") as f:
                content = f.read()
            raw_data = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as parse_err:
            actions.append(f"JSON syntax error / truncation: {parse_err}")
            # Try recovering from latest valid backup first
            backup_cfg, used_backup = cls._find_latest_valid_backup(b_dir)
            if backup_cfg:
                actions.append(f"Restored from backup: {used_backup.name}")
                cls._write_json(c_path, backup_cfg)
                return backup_cfg, ConfigRepairResult(
                    repaired=True,
                    recovered_from_backup=True,
                    backup_used=used_backup,
                    salvaged_count=len(backup_cfg.get("environments", [])),
                    actions_taken=actions,
                    config=backup_cfg,
                )
            else:
                # Try partial salvage of JSON text or fallback to clean default
                actions.append("Could not parse JSON and no valid backups found. Rebuilding default configuration.")
                clean_cfg = dict(DEFAULT_CONFIG)
                cls._write_json(c_path, clean_cfg)
                return clean_cfg, ConfigRepairResult(
                    repaired=True,
                    recovered_from_backup=False,
                    actions_taken=actions,
                    config=clean_cfg,
                )

        # 3. Data parsed as JSON -> Validate structure
        is_valid, validation_errors = ConfigValidator.validate_config(raw_data)
        if is_valid:
            return raw_data, ConfigRepairResult(repaired=False, config=raw_data, actions_taken=["Config is valid."])

        actions.append(f"Validation errors detected: {'; '.join(validation_errors)}")

        # 4. Repair schema and salvage environments
        repaired_cfg, salvaged, corrupt = cls.repair_structure(raw_data)
        actions.append(f"Salvaged {salvaged} environment(s); discarded {corrupt} unrecoverable entries.")

        # Save repaired configuration
        cls._write_json(c_path, repaired_cfg)
        return repaired_cfg, ConfigRepairResult(
            repaired=True,
            recovered_from_backup=False,
            salvaged_count=salvaged,
            corrupted_count=corrupt,
            actions_taken=actions,
            config=repaired_cfg,
        )

    @classmethod
    def repair_structure(cls, raw: Any) -> Tuple[Dict[str, Any], int, int]:
        """Repair a dictionary that violates mventx.json schema, salvaging all valid entries."""
        repaired = dict(DEFAULT_CONFIG)

        if not isinstance(raw, dict):
            return repaired, 0, 0

        # Version
        if "version" in raw and isinstance(raw["version"], str):
            repaired["version"] = raw["version"]

        # Meta
        if "meta" in raw and isinstance(raw["meta"], dict):
            m = raw["meta"]
            repaired["meta"]["title"] = str(m.get("title", DEFAULT_CONFIG["meta"]["title"]))
            repaired["meta"]["timeout"] = int(m.get("timeout", 10))
            repaired["meta"]["theme"] = str(m.get("theme", "mventx"))
            repaired["meta"]["default_entry_id"] = m.get("default_entry_id")

        # Environments array
        raw_envs = raw.get("environments", [])
        salvaged_envs: List[Dict[str, Any]] = []
        corrupted_count = 0

        if isinstance(raw_envs, list):
            for idx, item in enumerate(raw_envs):
                if not isinstance(item, dict):
                    corrupted_count += 1
                    continue

                # Must have at least an id or name or iso_path to be salvageable
                eid = item.get("id") or f"salvaged_env_{idx + 1}"
                dname = item.get("display_name") or f"Environment {idx + 1}"
                bmethod = item.get("boot_method") or "linux_generic_loopback"

                # Check if it has an iso_path or target_path
                iso_p = item.get("iso_path")
                target_p = item.get("target_path")
                if not iso_p and not target_p:
                    corrupted_count += 1
                    continue

                salvaged_entry: Dict[str, Any] = {
                    "id": str(eid),
                    "display_name": str(dname),
                    "boot_method": str(bmethod),
                    "enabled": bool(item.get("enabled", True)),
                    "order": int(item.get("order", len(salvaged_envs) + 1)),
                    "created_at": item.get("created_at", "2026-01-01T00:00:00Z"),
                }
                if iso_p:
                    salvaged_entry["iso_path"] = str(iso_p)
                if target_p:
                    salvaged_entry["target_path"] = str(target_p)
                if "kernel_path" in item:
                    salvaged_entry["kernel_path"] = item["kernel_path"]
                if "initrd_path" in item:
                    salvaged_entry["initrd_path"] = item["initrd_path"]
                if "custom_args" in item:
                    salvaged_entry["custom_args"] = item["custom_args"]
                if "description" in item:
                    salvaged_entry["description"] = item["description"]
                if "sha256" in item:
                    salvaged_entry["sha256"] = item["sha256"]

                salvaged_envs.append(salvaged_entry)

        # Re-number order sequentially
        for i, env in enumerate(salvaged_envs, start=1):
            env["order"] = i

        repaired["environments"] = salvaged_envs
        return repaired, len(salvaged_envs), corrupted_count

    @classmethod
    def _find_latest_valid_backup(cls, backups_dir: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
        """Scan backups/ directory and return the most recent valid configuration."""
        if not backups_dir.is_dir():
            return None, None

        backup_files = sorted(
            backups_dir.glob("mventx_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for bp in backup_files:
            try:
                with open(bp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                is_valid, _ = ConfigValidator.validate_config(data)
                if is_valid:
                    return data, bp
            except Exception:
                continue

        return None, None

    @classmethod
    def _write_json(cls, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        path.write_text(content, encoding="utf-8")

