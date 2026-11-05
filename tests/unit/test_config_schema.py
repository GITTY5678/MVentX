"""Tests for MVentX configuration schema and default configuration."""

import json
from pathlib import Path
import unittest

try:
    import jsonschema

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

try:
    import fastjsonschema

    HAS_FASTJSONSCHEMA = True
except ImportError:
    HAS_FASTJSONSCHEMA = False


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = REPO_ROOT / "core" / "config"
SCHEMA_PATH = CONFIG_DIR / "schema.json"
DEFAULTS_PATH = CONFIG_DIR / "defaults.json"


class TestConfigSchema(unittest.TestCase):
    """Test suite verifying schema.json and defaults.json."""

    def setUp(self):
        self.assertTrue(SCHEMA_PATH.exists(), f"schema.json must exist at {SCHEMA_PATH}")
        self.assertTrue(DEFAULTS_PATH.exists(), f"defaults.json must exist at {DEFAULTS_PATH}")

        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.schema = json.load(f)
        with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
            self.defaults = json.load(f)

    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA_PATH.exists())

    def test_defaults_file_exists(self):
        self.assertTrue(DEFAULTS_PATH.exists())

    def test_schema_is_valid_json_schema(self):
        if HAS_JSONSCHEMA:
            jsonschema.Draft7Validator.check_schema(self.schema)
        elif HAS_FASTJSONSCHEMA:
            fastjsonschema.compile(self.schema)
        else:
            self.assertIn("properties", self.schema)
            self.assertIn("version", self.schema["properties"])

    def test_defaults_validates_against_schema(self):
        if HAS_JSONSCHEMA:
            jsonschema.validate(instance=self.defaults, schema=self.schema)
        elif HAS_FASTJSONSCHEMA:
            validate = fastjsonschema.compile(self.schema)
            validate(self.defaults)

        self.assertEqual(self.defaults["version"], "1.0.0")
        self.assertEqual(self.defaults["environments"], [])

    def test_valid_environment_registration(self):
        sample_config = {
            "version": "1.0.0",
            "meta": {
                "title": "Custom Multiboot",
                "theme": "mventx",
                "timeout": 5,
                "default_entry_id": "test-env-1",
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
                    "custom_args": "quiet",
                }
            ],
        }

        if HAS_JSONSCHEMA:
            jsonschema.validate(instance=sample_config, schema=self.schema)
        elif HAS_FASTJSONSCHEMA:
            validate = fastjsonschema.compile(self.schema)
            validate(sample_config)


if __name__ == "__main__":
    unittest.main()
