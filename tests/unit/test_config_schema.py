"""Tests for MVentX configuration schema and default configuration."""

import json
from pathlib import Path
import pytest
import jsonschema


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = REPO_ROOT / "core" / "config"
SCHEMA_PATH = CONFIG_DIR / "schema.json"
DEFAULTS_PATH = CONFIG_DIR / "defaults.json"


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"schema.json must exist at {SCHEMA_PATH}"


def test_defaults_file_exists():
    assert DEFAULTS_PATH.exists(), f"defaults.json must exist at {DEFAULTS_PATH}"


def test_schema_is_valid_json_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    # Validate against JSON Schema Draft-07 metaschema
    jsonschema.Draft7Validator.check_schema(schema)


def test_defaults_validates_against_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
        defaults = json.load(f)

    # Validating defaults.json against schema.json
    jsonschema.validate(instance=defaults, schema=schema)
    assert defaults["version"] == "1.0.0"
    assert defaults["environments"] == []


def test_valid_environment_registration():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)

    sample_config = {
        "version": "1.0.0",
        "meta": {
            "title": "Custom Multiboot",
            "theme": "mventx",
            "timeout": 5,
            "default_entry_id": "test-env-1"
        },
        "environments": [
            {
                "id": "test-env-1",
                "display_name": "Test Environment 1",
                "type": "iso",
                "iso_path": "isos/test.iso",
                "boot_method": "linux_generic_loopback",
                "enabled": True,
                "order": 1,
                "description": "A sample registered ISO",
                "custom_args": "quiet"
            }
        ]
    }

    jsonschema.validate(instance=sample_config, schema=schema)

