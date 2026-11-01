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


def github_output(text):
    """Parse GITHUB_OUTPUT the way the runner does: name=value and name<<DELIMITER blocks."""
    values, lines = {}, iter(text.splitlines())
    for line in lines:
        if '<<' in line.split('=', 1)[0]:
            name, delimiter = line.split('<<', 1)
            block = []
            for item in lines:
                if item == delimiter:
                    break
                block.append(item)
            else:
                raise AssertionError('Unterminated multiline output')
            values[name] = '\n'.join(block)
        else:
            name, value = line.split('=', 1)
            values[name] = value
    return values


PLAN = embedded_python('.github/workflows/image.yml', 'Validate inputs and publication boundary')
MERGE = embedded_python('.github/workflows/image.yml', 'Merge and inspect OCI index')
DEPLOYED = embedded_python('.github/workflows/deployed-tag.yml', 'Validate inputs and event boundary')
RELEASE = embedded_python('.github/workflows/release.yml', 'Validate inputs and event boundary')
NOTIFY = embedded_python('.github/workflows/release-notify.yml', 'Validate tags and event boundary')
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
            values = github_output(output.read_text()) if output.exists() else {}
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

    def test_build_args_are_validated_and_passed_as_a_plan_output(self):
        accepted = (
            '', 'WIKI_BUILD_NUMBER=42', 'WIKI_BUILD_NUMBER=42\n', 'EMPTY=',
            'WIKI_BUILD_NUMBER=42\nWIKI_BUILD_DATE=2026-10-27T10:00:00+00:00\nWIKI_BUILD_SHA=' + 'a' * 40,
            'A' + '_' * 63 + '=' + 'v' * 128, '\n'.join(f'K{i}=v' for i in range(10)),
            'PATH_LIKE=registry.example/app:1.0+build-2_x',
        )
        for value in accepted:
            with self.subTest(value=value):
                result, values = self.plan(BUILD_ARGS=value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values['build-args'], value.removesuffix('\n'))
                self.assertEqual(values['tag'], 'sha-' + 'a' * 40)
        rejected = (
            'lower=value', '1KEY=value', '_KEY=value', 'KEY', 'KEY value', '=value', 'KEY==value',
            'KEY=has space', 'KEY=quote"', "KEY=it's", 'KEY=$(id)', 'KEY=`id`', 'KEY=a;b', 'KEY=a=b',
            'KEY=${{ secrets.TOKEN }}', 'KEY=%0A', 'KEY=\\n', 'KEY=value\r', 'KEY=a\x0bB=c', 'KEY=a\x85B=c',
            'KEY=a\u2028B=c', 'KEY=\u00e9', 'A' * 65 + '=v', 'KEY=' + 'v' * 129,
            'KEY=value\n\nOTHER=value', '\n', 'KEY=value\n\n', ' KEY=value', 'KEY=value ',
            'KEY=value\nBUILD_ARGS_EOF',
            '\n'.join(f'K{i}=v' for i in range(11)), 'KEY=one\nKEY=two',
        )
        for value in rejected:
            with self.subTest(value=value):
                result, values = self.plan(BUILD_ARGS=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

    def test_build_args_cannot_forge_other_outputs(self):
        result, values = self.plan(BUILD_ARGS='KEY=value')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(values), {'matrix', 'image', 'tag', 'publish', 'build-args'})

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

    def deployed(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            env = dict(IMAGE='ghcr.io/verbanium-lab/iblonda-web', DIGEST='sha256:' + 'a' * 64, TARGET_ENV='prod',
                       RELEASE='', EVENT='workflow_dispatch', REF='refs/heads/main', DEFAULT_REF='refs/heads/main',
                       OWNER='verbanium-lab', GITHUB_OUTPUT=str(output))
            env.update(changes)
            result = subprocess.run([sys.executable, '-c', DEPLOYED], env=env, capture_output=True, text=True)
            values = github_output(output.read_text()) if output.exists() else {}
            return result, values

    def test_deployed_tag_accepts_each_environment(self):
        for target in ('dev', 'rc', 'prod'):
            with self.subTest(target=target):
                result, values = self.deployed(TARGET_ENV=target)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values, {'image': 'ghcr.io/verbanium-lab/iblonda-web',
                                          'digest': 'sha256:' + 'a' * 64, 'tag': 'deployed-' + target,
                                          'release': '', 'deployment-environment':
                                          {'dev': 'dev', 'rc': 'rc', 'prod': 'production'}[target]})

    def test_deployed_tag_rejects_malformed_inputs_and_events(self):
        for changes in (
            dict(TARGET_ENV='production'), dict(TARGET_ENV=''), dict(TARGET_ENV='prod\ninjected=value'),
            dict(TARGET_ENV='Prod'), dict(TARGET_ENV='dev '),
            dict(DIGEST='sha256:' + 'a' * 63), dict(DIGEST='sha256:' + 'a' * 65), dict(DIGEST='sha256:' + 'A' * 64),
            dict(DIGEST='sha512:' + 'a' * 64), dict(DIGEST='a' * 64), dict(DIGEST='sha256:' + 'a' * 64 + '\n'),
            dict(DIGEST=''), dict(DIGEST='sha256:' + 'a' * 64 + '\ninjected=value'),
            dict(IMAGE='ghcr.io/other/iblonda-web'), dict(IMAGE='docker.io/verbanium-lab/iblonda-web'),
            dict(IMAGE='ghcr.io/verbanium-lab/iblonda-web:latest'),
            dict(IMAGE='ghcr.io/verbanium-lab/iblonda-web@sha256:' + 'a' * 64),
            dict(IMAGE='ghcr.io/verbanium-lab/Iblonda-web'), dict(IMAGE='ghcr.io/verbanium-lab/'),
            dict(IMAGE='ghcr.io/verbanium-lab/../other/x'), dict(IMAGE='ghcr.io/verbanium-lab/web\ninjected=value'),
            dict(IMAGE=''),
            dict(EVENT='push'), dict(EVENT='pull_request'), dict(EVENT='pull_request_target'),
            dict(REF='refs/heads/feature'), dict(REF='refs/pull/1/merge'),
            dict(DEFAULT_REF='refs/heads/', REF='refs/heads/'),
            dict(OWNER='other'),
        ):
            with self.subTest(changes=changes):
                result, values = self.deployed(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

    def release(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            env = dict(SLUG='landing', IMAGE='ghcr.io/verbanium-lab/landing',
                       DIGEST='sha256:' + 'a' * 64, NOTES='',
                       EVENT='push', REF='refs/heads/main', SHA='b' * 40,
                       OWNER='verbanium-lab', SERVER='https://github.com',
                       REPOSITORY='verbanium-lab/verbanium-apps', GITHUB_OUTPUT=str(output))
            env.update(changes)
            result = subprocess.run([sys.executable, '-c', RELEASE], env=env, capture_output=True, text=True)
            values = github_output(output.read_text()) if output.exists() else {}
            return result, values

    def test_release_plan_builds_tag_and_telegram_html(self):
        result, values = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values['slug'], 'landing')
        self.assertEqual(values['digest'], 'sha256:' + 'a' * 64)
        self.assertTrue(values['tag'].startswith('landing-'))
        self.assertTrue(values['tag'].endswith('-' + 'b' * 7))
        self.assertIn('<b>landing</b>', values['telegram-text'])
        self.assertIn('ghcr.io/verbanium-lab/landing', values['telegram-text'])
        self.assertNotIn('<script', values['telegram-text'])

    def test_release_plan_rejects_invalid_boundary(self):
        for changes in (
            dict(SLUG='Landing'), dict(SLUG=''), dict(SLUG='x' * 64),
            dict(IMAGE='ghcr.io/other/landing'), dict(DIGEST='sha256:' + 'A' * 64),
            dict(EVENT='pull_request'), dict(REF='refs/heads/feature'), dict(OWNER='other'),
            dict(NOTES='a' * 20000), dict(SHA='short'),
        ):
            with self.subTest(changes=changes):
                result, values = self.release(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

    def notify(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            env = dict(TAGS='studio-v0.2.0', EVENT='push', REF='refs/heads/main', OWNER='verbanium-lab',
                       SERVER='https://github.com', REPOSITORY='verbanium-lab/verbanium-templates',
                       GITHUB_OUTPUT=str(output))
            env.update(changes)
            result = subprocess.run([sys.executable, '-c', NOTIFY], env=env, capture_output=True, text=True)
            values = github_output(output.read_text()) if output.exists() else {}
            return result, values

    def test_notify_plan_builds_one_message_per_tag(self):
        result, values = self.notify(TAGS='studio-v0.2.0 tokens-v1.0.0-rc.1')
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = json.loads(values['messages'])
        self.assertEqual(values['count'], '2')
        self.assertIn('<b>studio</b> v0.2.0', messages[0])
        self.assertIn('releases/tag/studio-v0.2.0', messages[0])
        self.assertIn('<b>tokens</b> v1.0.0-rc.1', messages[1])

    def test_notify_plan_rejects_invalid_boundary(self):
        for changes in (
            dict(TAGS=''), dict(TAGS='studio-2026.11.01-abcdef0'), dict(TAGS='Studio-v1.0.0'),
            dict(TAGS='studio-v1.0.0 studio-v1.0.0'), dict(TAGS='<b>-v1.0.0'),
            dict(EVENT='pull_request'), dict(REF='refs/heads/feature'), dict(OWNER='other'),
            dict(TAGS=' '.join(f'a{i}-v1.0.0' for i in range(21))),
        ):
            with self.subTest(changes=changes):
                result, values = self.notify(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

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
        for names, expected in ((['@verbanium-lab/tower'], 1), ([], 0), (['@other/unknown'], 0)):
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
        for data in ({'packages': []}, {'packages': {'items': ['@verbanium-lab/tower']}}):
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / 'affected.json').write_text(json.dumps(data))
                result = subprocess.run([sys.executable, '-c', AFFECTED],
                                        env=dict(RUNNER_TEMP=directory, GITHUB_OUTPUT=str(Path(directory) / 'output')),
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)


class StatusWorkflowTests(unittest.TestCase):
    """Option C (2026-11-01): the README commit is opt-in."""

    def test_readme_commit_is_off_by_default(self):
        text = (ROOT / '.github/workflows/status.yml').read_text()
        block = text.split('commit-readme:', 1)[1].split('type: boolean', 1)[0]
        self.assertIn('default: false', block)
        step = text.split('- name: Commit status update', 1)[1].split('run:', 1)[0]
        self.assertIn('inputs.commit-readme &&', step)


if __name__ == '__main__':
    unittest.main()
