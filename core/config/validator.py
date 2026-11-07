"""Configuration Validation Subsystem for MVentX.

Validates configuration dictionaries against core/config/schema.json and semantic rules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"


class ConfigValidator:
    """Validates configuration dictionaries against JSON Schema and structural rules."""

    @classmethod
    def validate_config(cls, config_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate configuration dictionary.

        Returns:
            Tuple of (is_valid: bool, errors: List[str])
        """
        errors: List[str] = []

        if not isinstance(config_data, dict):
            return False, ["Configuration root must be a JSON object (dictionary)."]

        # 1. Structural checks
        if "version" not in config_data:
            errors.append("Missing required top-level property: 'version'")
        elif not isinstance(config_data["version"], str):
            errors.append("'version' must be a string.")

        if "meta" in config_data and not isinstance(config_data["meta"], dict):
            errors.append("'meta' must be an object (dictionary).")

        if "environments" not in config_data:
            errors.append("Missing required top-level property: 'environments'")
        elif not isinstance(config_data["environments"], list):
            errors.append("'environments' must be an array (list).")

        # 2. JSON Schema validation if library available
        if not errors and HAS_JSONSCHEMA and SCHEMA_PATH.is_file():
            try:
                with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
                    schema = json.load(f)
                validator = jsonschema.Draft7Validator(schema)
                schema_errs = list(validator.iter_errors(config_data))
                for err in schema_errs:
                    errors.append(f"{err.path[-1] if err.path else 'root'}: {err.message}")
            except Exception as exc:
                pass

        # 3. Use GrubMenuGenerator semantic checks
        if not errors:
            from core.boot_config.generator import GrubMenuGenerator
            try:
                generator = GrubMenuGenerator()
                generator.validate_config(config_data)
            except Exception as exc:
                errors.append(str(exc))

        is_valid = (len(errors) == 0)
        return is_valid, errors

