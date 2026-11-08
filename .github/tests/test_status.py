"""Unit tests for README status collection and rendering."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/render_status.py"
COLLECTOR_SPEC = importlib.util.spec_from_file_location(
    "collect_status", ROOT / ".github/scripts/collect_status.py"
)
collector = importlib.util.module_from_spec(COLLECTOR_SPEC)
COLLECTOR_SPEC.loader.exec_module(collector)


class CollectStatusTests(unittest.TestCase):
    server = "https://github.com"
    repository = "verbanium-lab/iblonda-apps"
    image = "ghcr.io/verbanium-lab/iblonda-web"
    digest = "sha256:" + "a" * 64

    def latest(self, releases, slug="iblonda-web"):
        return collector.latest_release_for_slug(releases, slug, self.server, self.repository)

    def test_semver_tag_accepted(self):
        for slug in ("iblonda-web", "iblonda-admin"):
            tag = slug + "-v1.2.3"
            with self.subTest(tag=tag):
                self.assertEqual(self.latest([{"tag_name": tag}], slug), {
                    "tag": tag,
                    "url": f"{self.server}/{self.repository}/releases/tag/{tag}",
                })

    def test_legacy_tag_still_accepted(self):
        tag = "iblonda-web-2026.10.31-abc1234"
        self.assertEqual(self.latest([{"tag_name": tag}])["tag"], tag)

    def test_prefix_collision_rejected(self):
        for tag in ("iblonda-web-admin-v1.0.0", "iblonda-web-admin-2026.10.31-abc1234"):
            with self.subTest(tag=tag):
                self.assertIsNone(self.latest([{"tag_name": tag}]))

    def test_prerelease_and_build_metadata_accepted(self):
        for version in ("0.0.0", "1.2.3-rc.1", "1.2.3-0", "1.2.3-01a",
                        "1.2.3-alpha-beta.2", "1.2.3+build.001", "1.2.3-rc.1+build.001"):
            tag = "iblonda-web-v" + version
            with self.subTest(tag=tag):
                release = self.latest([{"tag_name": tag, "prerelease": "-" in version}])
                self.assertEqual(release["tag"], tag)
                self.assertEqual(release["url"],
                                 f"{self.server}/{self.repository}/releases/tag/{tag.replace('+', '%2B')}")

    def test_latest_uses_published_at_across_tag_shapes(self):
        releases = [
            {"tag_name": "iblonda-web-v9.0.0", "published_at": "2026-10-30T10:00:00Z"},
            {"tag_name": "iblonda-web-v1.2.3-rc.1", "published_at": "2026-11-02T10:00:00Z"},
            {"tag_name": "iblonda-web-2026.10.31-abc1234", "published_at": "2026-10-31T10:00:00Z"},
        ]
        self.assertEqual(self.latest(releases)["tag"], "iblonda-web-v1.2.3-rc.1")
        releases[2]["published_at"] = "2026-11-03T10:00:00Z"
        self.assertEqual(self.latest(releases)["tag"], releases[2]["tag_name"])

    def test_malformed_tags_rejected(self):
        for suffix in ("v1.2", "v1.2.3.4", "v01.2.3", "v1.02.3", "v1.2.03",
                       "v1.2.3-01", "v1.2.3-rc.01", "v1.2.3-", "v1.2.3+",
                       "v1.2.3-rc..1", "v1.2.3+build..1", "v1.2.3+build_1",
                       "v1.2.3-rc/1", "v1.2.3|injected", "v1.2.3[link](url)",
                       "v1.2.3\n| injected |", "v1.2.3\n", "v1.2.3 ", "v١.2.3",
                       "2026.10.31-abcdef12", "2026.10.31-ABC1234"):
            tag = "iblonda-web-" + suffix
            with self.subTest(tag=tag):
                releases = [{"tag_name": tag, "body": f"Deploy by digest: `{self.image}@{self.digest}`"}]
                self.assertIsNone(self.latest(releases))
                self.assertIsNone(collector.release_for_digest(
                    releases, self.image, self.digest, self.server, self.repository))

    def test_digest_maps_only_with_explicit_image_digest_line(self):
        for tag in ("iblonda-web-2026.10.31-abc1234", "iblonda-web-v1.2.3-rc.1"):
            with self.subTest(tag=tag):
                releases = [{"tag_name": tag, "body": f"Deploy by digest: `{self.image}@{self.digest}`"}]
                self.assertEqual(collector.digest_cell(
                    releases, self.image, self.digest, self.server, self.repository), {
                        "label": tag,
                        "url": f"{self.server}/{self.repository}/releases/tag/{tag}",
                    })

    def test_semver_without_digest_line_keeps_short_digest(self):
        for body in (None, "", "## 1.2.3\n\n### Bug Fixes\n\n* Fix a bug"):
            with self.subTest(body=body):
                releases = [{"tag_name": "iblonda-web-v1.2.3", "body": body}]
                self.assertEqual(collector.digest_cell(
                    releases, self.image, self.digest, self.server, self.repository),
                    {"label": "a" * 12, "url": None})

    def test_digest_mapping_requires_exact_image_and_digest(self):
        for image, digest in ((self.image + "-admin", self.digest),
                              (self.image, "sha256:" + "b" * 64)):
            with self.subTest(image=image, digest=digest):
                releases = [{"tag_name": "iblonda-web-v1.2.3",
                             "body": f"Deploy by digest: `{image}@{digest}`"}]
                self.assertIsNone(collector.release_for_digest(
                    releases, self.image, self.digest, self.server, self.repository))

    def test_missing_digest_has_no_cell(self):
        self.assertIsNone(collector.digest_cell([], self.image, None, self.server, self.repository))


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
