"""Unit tests for README status rendering."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/render_status.py"


class RenderStatusTests(unittest.TestCase):
    def run_render(self, readme: str, payload: dict) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            readme_path = Path(directory) / "README.md"
            data_path = Path(directory) / "data.json"
            readme_path.write_text(readme, encoding="utf-8")
            data_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--readme", str(readme_path), "--data", str(data_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return readme_path.read_text(encoding="utf-8"), result.stdout.strip()

    def sample_payload(self, rows):
        return {
            "repository": "verbanium-lab/iblonda-apps",
            "server": "https://github.com",
            "rows": rows,
        }

    def test_renders_table_with_links(self):
        readme = "# App\n\n<!-- status:start -->\nold\n<!-- status:end -->\n"
        rows = [{
            "slug": "iblonda-web",
            "release": {"tag": "iblonda-web-2026.10.31-abc1234",
                        "url": "https://github.com/verbanium-lab/iblonda-apps/releases/tag/iblonda-web-2026.10.31-abc1234"},
            "production": {"label": "iblonda-web-2026.10.30-deadbeef",
                           "url": "https://github.com/verbanium-lab/iblonda-apps/releases/tag/iblonda-web-2026.10.30-deadbeef"},
            "rc": {"label": "abc123deadbeef", "url": None},
            "coverage": {"unit": {"url": "https://example.test/unit"}, "e2e": {"url": "https://example.test/e2e"}},
        }]
        updated, status = self.run_render(readme, self.sample_payload(rows))
        self.assertEqual(status, "changed=true")
        self.assertIn("![ci](https://github.com/verbanium-lab/iblonda-apps/actions/workflows/ci.yml/badge.svg?branch=main)", updated)
        self.assertIn("| iblonda-web |", updated)
        self.assertIn("[iblonda-web-2026.10.31-abc1234]", updated)
        self.assertIn("[unit](https://example.test/unit) / [e2e](https://example.test/e2e)", updated)
        self.assertIn("abc123deadbeef", updated)
        self.assertNotIn("old", updated)

    def test_idempotent_when_block_unchanged(self):
        rows = [{
            "slug": "iblonda-web",
            "release": None,
            "production": None,
            "rc": None,
        }]
        payload = self.sample_payload(rows)
        readme = "# Title\n\n"
        import importlib.util

        spec = importlib.util.spec_from_file_location("render_status", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        block = module.render_block(rows, payload["repository"], payload["server"])
        readme += block + "\nFooter\n"
        updated, changed = module.apply_status(readme, rows, payload["repository"], payload["server"])
        self.assertFalse(changed)
        second, status = self.run_render(updated, payload)
        self.assertEqual(status, "changed=false")
        self.assertEqual(second, updated)

    def test_marker_replacement_preserves_surrounding_content(self):
        readme = "Before\n<!-- status:start -->\nlegacy\n<!-- status:end -->\nAfter\n"
        rows = [{"slug": "tower", "release": None, "production": None, "rc": None}]
        updated, _ = self.run_render(readme, self.sample_payload(rows))
        self.assertTrue(updated.startswith("Before\n"))
        self.assertIn("\nAfter\n", updated)
        self.assertNotIn("legacy", updated)

    def test_missing_data_renders_em_dash_cells(self):
        readme = "<!-- status:start -->\n<!-- status:end -->\n"
        rows = [{"slug": "iblonda-admin", "release": None, "production": None, "rc": None}]
        updated, _ = self.run_render(readme, self.sample_payload(rows))
        self.assertIn("| iblonda-admin | — | — | — | — |", updated)

    def test_missing_markers_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            readme_path = Path(directory) / "README.md"
            data_path = Path(directory) / "data.json"
            readme_path.write_text("# no markers\n", encoding="utf-8")
            data_path.write_text(json.dumps(self.sample_payload([])), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--readme", str(readme_path), "--data", str(data_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_ci_badge_not_duplicated(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("render_status", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [{"slug": "x", "release": None, "production": None, "rc": None}]
        block = module.render_block(rows, "verbanium-lab/x", "https://github.com")
        self.assertEqual(block.count("![ci]("), 1)
        readme = f"<!-- status:start -->\n{block.splitlines()[1]}\n<!-- status:end -->\n"
        payload = self.sample_payload(rows)
        updated, _ = self.run_render(readme, payload)
        self.assertEqual(updated.count("![ci]("), 1)


if __name__ == "__main__":
    unittest.main()
