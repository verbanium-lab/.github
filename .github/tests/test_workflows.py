"""Offline behavior tests of the actual embedded scripts; no registry or real tokens."""

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def embedded_python(path, step):
    """Extract a literal Python heredoc without requiring a YAML package for these tests."""
    text = (ROOT / path).read_text().split(step, 1)[1]
    match = re.search(r"(?m)^( +)python3 - <<'PY'\n(.*?)^\1PY$", text, re.DOTALL)
    if not match:
        raise AssertionError("Python heredoc not found")
    return textwrap.dedent(match[2])


PLAN = embedded_python('.github/workflows/image.yml', 'Validate inputs and publication boundary')
MERGE = embedded_python('.github/workflows/image.yml', 'Merge and inspect OCI index')
RETENTION = embedded_python('.github/workflows/ghcr-cleanup.yml', 'Protect the latest ten sha tags')
AFFECTED = embedded_python('docs/reusable-workflows.md', 'Select affected images')


class WorkflowBehaviorTests(unittest.TestCase):
    def plan(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            env = dict(APP='wiki', IMAGE='ghcr.io/verbanium-lab/wiki', DOCKERFILE='portal/Dockerfile',
                       CONTEXT='.', PLATFORMS='linux/amd64,linux/arm64', MODE='main', BUILD_ARGS='',
                       EVENT='push', REF='refs/heads/main', SHA='a' * 40,
                       REPOSITORY='verbanium-lab/wiki', HEAD_REPOSITORY='', PR_NUMBER='',
                       GITHUB_OUTPUT=str(output))
            env.update(changes)
            result = subprocess.run([sys.executable, '-c', PLAN], env=env, capture_output=True, text=True)
            values = dict(line.split('=', 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return result, values

    def test_native_platforms_and_full_sha(self):
        result, values = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values['tag'], 'sha-' + 'a' * 40)
        self.assertEqual(values['publish'], 'true')
        self.assertEqual(json.loads(values['matrix'])['include'], [
            {'platform': 'linux/amd64', 'arch': 'amd64', 'runner': 'ubuntu-24.04'},
            {'platform': 'linux/arm64', 'arch': 'arm64', 'runner': 'ubuntu-24.04-arm'},
        ])
        result, values = self.plan(PLATFORMS='linux/arm64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(values['matrix'])['include']), 1)

    def test_same_repository_preview_uses_tested_sha(self):
        result, values = self.plan(MODE='preview', EVENT='pull_request', REF='refs/pull/8/merge',
                                   HEAD_REPOSITORY='verbanium-lab/wiki', PR_NUMBER='8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values['tag'], 'pr-8-' + 'a' * 12)

    def test_fork_build_has_no_publication(self):
        result, values = self.plan(MODE='none', EVENT='pull_request', HEAD_REPOSITORY='outside/wiki', PR_NUMBER='8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values['publish'], 'false')
        self.assertEqual(values['tag'], '')

    def test_reject_publication_outside_event_boundary(self):
        for changes in (
            dict(MODE='preview', EVENT='pull_request', HEAD_REPOSITORY='outside/wiki', PR_NUMBER='8'),
            dict(MODE='preview', EVENT='push', HEAD_REPOSITORY='verbanium-lab/wiki', PR_NUMBER='8'),
            dict(MODE='main', EVENT='pull_request', HEAD_REPOSITORY='verbanium-lab/wiki', PR_NUMBER='8'),
            dict(MODE='main', REF='refs/heads/feature'),
            dict(MODE='none', EVENT='pull_request_target'),
            dict(MODE='preview', EVENT='workflow_run'),
            dict(MODE='preview', EVENT='pull_request', HEAD_REPOSITORY='verbanium-lab/wiki', PR_NUMBER=''),
        ):
            with self.subTest(changes=changes):
                result, output = self.plan(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, {})

    def test_reject_malformed_or_injected_inputs(self):
        for changes in (
            dict(MODE='publish'), dict(PLATFORMS='linux/s390x'), dict(PLATFORMS='linux/amd64,linux/amd64'),
            dict(PLATFORMS=''), dict(APP='wiki\ninjected=value'), dict(IMAGE='ghcr.io/other/wiki'),
            dict(IMAGE='ghcr.io/verbanium-lab/wiki:tag'), dict(DOCKERFILE='../Dockerfile'),
            dict(CONTEXT='/tmp'), dict(CONTEXT='https://example.test/context'),
            dict(BUILD_ARGS='KEY=value\ninjected'), dict(BUILD_ARGS='KEY=value\x1b'), dict(SHA='not-a-sha'),
        ):
            with self.subTest(changes=changes):
                result, output = self.plan(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, {})
        result, _ = self.plan(BUILD_ARGS='PUBLIC_VERSION=1.0\nPUBLIC_TITLE=Example app')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_merge_rejects_missing_extra_or_invalid_artifacts(self):
        for files, success in (
            ({'amd64': 'sha256:' + 'a' * 64, 'arm64': 'sha256:' + 'b' * 64}, True),
            ({'amd64': 'sha256:' + 'a' * 64}, False),
            ({'amd64': 'sha256:' + 'a' * 64, 'arm64': 'invalid'}, False),
            ({'amd64': 'sha256:' + 'a' * 64, 'arm64': 'sha256:' + 'b' * 64, 'extra': 'x'}, False),
        ):
            with self.subTest(files=files), tempfile.TemporaryDirectory() as directory:
                digests = Path(directory) / 'digests'
                digests.mkdir()
                for name, value in files.items():
                    (digests / name).write_text(value)
                env = dict(RUNNER_TEMP=directory, IMAGE='ghcr.io/verbanium-lab/wiki',
                           MATRIX=json.dumps({'include': [{'arch': 'amd64'}, {'arch': 'arm64'}]}))
                result = subprocess.run([sys.executable, '-c', MERGE], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                if success:
                    sources = (Path(directory) / 'sources.txt').read_text().splitlines()
                    self.assertEqual(sources, ['ghcr.io/verbanium-lab/wiki@sha256:' + c * 64 for c in ('a', 'b')])

    def retention(self, pages, package='wiki'):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            calls = []

            @contextmanager
            def response(request, timeout):
                calls.append(request.full_url)
                self.assertEqual(timeout, 30)
                yield io.StringIO(json.dumps(pages[len(calls) - 1]))

            with patch.dict(os.environ, dict(GH_TOKEN='synthetic-test-value', PACKAGE=package,
                                            GITHUB_OUTPUT=str(output)), clear=True), \
                    patch('urllib.request.urlopen', response):
                exec(compile(RETENTION, '<retention>', 'exec'), {})
            return output.read_text().split('=', 1)[1].strip(), calls

    def test_retention_paginates_and_protects_ten_sha_versions(self):
        versions = []
        for number in range(110):
            tags = ['sha-' + format(number, '040x')] if number < 15 else ['v' + str(number)]
            versions.append(dict(id=number, created_at=f'2026-10-{number % 28 + 1:02}T00:00:00Z',
                                 metadata={'container': {'tags': tags}}))
        exclusion, calls = self.retention([versions[:100], versions[100:]], package='apps/wiki')
        self.assertEqual(len(calls), 2)
        self.assertIn('/apps%2Fwiki/versions?per_page=100&page=2', calls[1])
        for number in range(15):
            self.assertEqual(bool(re.fullmatch(exclusion, 'sha-' + format(number, '040x'))), number >= 5)
        for tag in ('v1.0.0', 'deployed-production', 'deployed-preview'):
            self.assertRegex(tag, exclusion)
        self.assertIsNone(re.fullmatch(exclusion, 'pr-8-abcdef123456'))

    def test_retention_empty_and_aliases(self):
        exclusion, _ = self.retention([[]])
        self.assertRegex('v1.0.0', exclusion)
        versions = [dict(id=1, created_at='2026-10-27T00:00:00Z', metadata={'container': {
            'tags': ['sha-' + 'a' * 40, 'sha-' + 'b' * 12, 'sha-legacy.1', 'bad\noutput=value']}})]
        exclusion, _ = self.retention([versions])
        self.assertRegex('sha-' + 'a' * 40, exclusion)
        self.assertRegex('sha-' + 'b' * 12, exclusion)
        self.assertRegex('sha-legacy.1', exclusion)
        self.assertIsNone(re.fullmatch(exclusion, 'sha-legacyX1'))
        self.assertNotIn('bad', exclusion)
        with self.assertRaises(SystemExit):
            self.retention([[]], package='../invalid')

    def test_retention_api_failure_is_fatal(self):
        with patch.dict(os.environ, dict(GH_TOKEN='synthetic-test-value', PACKAGE='wiki'), clear=True), \
                patch('urllib.request.urlopen', side_effect=OSError('API unavailable')):
            with self.assertRaises(OSError):
                exec(compile(RETENTION, '<retention>', 'exec'), {})

    def test_affected_recipe_selects_known_apps_and_handles_empty(self):
        for names, expected in ((['@verbanium/tower'], 1), ([], 0), (['@other/unknown'], 0)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / 'affected.json').write_text(json.dumps({
                    'packages': {'items': [{'name': name} for name in names]}}))
                output = Path(directory) / 'output'
                result = subprocess.run([sys.executable, '-c', AFFECTED],
                                        env=dict(RUNNER_TEMP=directory, GITHUB_OUTPUT=str(output)),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(len(json.loads(values['matrix'])['include']), expected)
                self.assertEqual(values['has-apps'], str(bool(expected)).lower())

    def test_affected_recipe_rejects_unknown_schema(self):
        for data in ({'packages': []}, {'packages': {'items': ['@verbanium/tower']}}):
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / 'affected.json').write_text(json.dumps(data))
                result = subprocess.run([sys.executable, '-c', AFFECTED],
                                        env=dict(RUNNER_TEMP=directory, GITHUB_OUTPUT=str(Path(directory) / 'output')),
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
