"""Integration tests for MVentX Boot Configuration Generation."""

import json
from pathlib import Path
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULTS_PATH = REPO_ROOT / "core" / "config" / "defaults.json"
SCHEMA_PATH = REPO_ROOT / "core" / "config" / "schema.json"


class TestMenuIntegration(unittest.TestCase):
    """Verifies integration between configuration defaults, schema, and menu generator."""

    def setUp(self):
        self.generator = GrubMenuGenerator()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_integ_"))

    def tearDown(self):
        import shutil

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_defaults_json_generates_valid_fallback_menu(self):
        """defaults.json must produce a clean fallback menu."""
        with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
            defaults_data = json.load(f)

        script = self.generator.generate_menu_script(defaults_data)
        self.assertIn('menuentry "No environments registered or enabled"', script)
        self.assertIn("mventx-empty", script)

    def test_end_to_end_file_write_and_reload(self):
        """Test full file generation to disk."""
        target_menu = self.temp_dir / "mventx_menu.cfg"
        with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
            defaults_data = json.load(f)

        self.generator.write_menu_file(defaults_data, target_menu)
        self.assertTrue(target_menu.exists())
        content = target_menu.read_text(encoding="utf-8")
        self.assertIn('set mventx_menu_title="MVentX Multiboot System"', content)


if __name__ == "__main__":
    unittest.main()

