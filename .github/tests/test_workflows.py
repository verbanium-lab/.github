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
RELEASE_PR = embedded_python('.github/workflows/release-pr-merge.yml', 'Check the Release PR and merge it')
DECISION_END = '# --- end of the decision logic ---'

FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
args = sys.argv[1:]
if args[:3] == ["api", "--method", "PUT"]:
    open(os.path.join(d, "merge.log"), "a").write(os.environ["GH_TOKEN"] + " " + " ".join(args) + "\\n")
    sys.exit(int(open(os.path.join(d, "merge_exit")).read()) if os.path.exists(os.path.join(d, "merge_exit")) else 0)
elif args[:1] == ["api"]:
    path = args[-1]
    if "/check-runs" in path:
        open(os.path.join(d, "checks.log"), "a").write(os.environ["GH_TOKEN"] + " " + path + "\\n")
        print(json.dumps([{"check_runs": json.load(open(os.path.join(d, "checks.json")))}]))
    elif "state=closed" in path:
        open(os.path.join(d, "closed.log"), "a").write(os.environ["GH_TOKEN"] + " " + " ".join(args) + "\\n")
        print(json.dumps(json.load(open(os.path.join(d, "closed.json")))))
    elif "/compare/" in path:
        open(os.path.join(d, "compare.log"), "a").write(" ".join(args) + "\\n")
        status = open(os.path.join(d, "compare.txt")).read()
        if status == "error":
            sys.exit(1)
        print(status)
    else:
        name = "files" if "/files" in path else "pulls"
        print(json.dumps([json.load(open(os.path.join(d, name + ".json")))]))
else:
    sys.exit(2)
'''

GOOD_FILES = [
    dict(filename='.release-please-manifest.json', status='modified',
         patch='@@ -1 +1 @@\n-  "portal": "1.7.1"\n+  "portal": "1.7.2"'),
    dict(filename='portal/CHANGELOG.md', status='modified', patch='@@\n+## 1.7.2\n+* anything'),
    dict(filename='portal/package.json', status='modified',
         patch='@@\n {\n-  "version": "1.7.1",\n+  "version": "1.7.2",\n   "private": true,'),
    dict(filename='portal/package-lock.json', status='modified',
         patch='@@\n-  "version": "1.7.1",\n+  "version": "1.7.2",\n-      "version": "1.7.1",\n+      "version": "1.7.2",'),
]


def ci_run(**changes):
    run = dict(id=1, name='ci', status='completed', conclusion='success', completed_at='2026-11-02T04:35:00Z',
               app=dict(id=15368))
    run.update(changes)
    return run


GREEN = [ci_run()]


def release_pr(**changes):
    pr = dict(number=167, draft=False, user=dict(login='verbanium-ci[bot]', type='Bot'),
              head=dict(sha='b' * 40, repo=dict(full_name='verbanium-lab/wiki')))
    pr.update(changes)
    return pr


TICKED_BODY = '- [x] Release and deploy to release-candidate after merge'


def merged_pr(number, merged_at='2026-11-27T09:00:00Z', body='', merged=True, base='main', sha='c' * 40,
              head_ref='agent/org/change', head_repo='verbanium-lab/wiki'):
    return dict(number=number, merged_at=merged_at if merged else None, body=body, merge_commit_sha=sha,
                base=dict(ref=base), head=dict(ref=head_ref, repo=dict(full_name=head_repo)))


class WorkflowBehaviorTests(unittest.TestCase):
    def plan(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            env = dict(APP='wiki', IMAGE='ghcr.io/verbanium-lab/wiki', DOCKERFILE='portal/Dockerfile',
                       CONTEXT='.', PLATFORMS='linux/amd64,linux/arm64', MODE='main', BUILD_ARGS='',
                       RUNS_ON='ubuntu-24.04', RUNS_ON_ARM64='ubuntu-24.04-arm', EVENT='push', REF='refs/heads/main', SHA='a' * 40,
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

    def test_runner_inputs_choose_the_matrix_runners(self):
        result, values = self.plan(RUNS_ON='vl-heavy', RUNS_ON_ARM64='vl-heavy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p['runner'] for p in json.loads(values['matrix'])['include']], ['vl-heavy', 'vl-heavy'])
        self.assertEqual(values['fork'], 'false')
        result, values = self.plan(MODE='preview', EVENT='pull_request', REF='refs/pull/8/merge',
                                   HEAD_REPOSITORY='verbanium-lab/wiki', PR_NUMBER='8', RUNS_ON='vl-light')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(values['matrix'])['include'][0]['runner'], 'vl-light')

    def test_fork_pull_requests_never_use_self_hosted_runners(self):
        result, values = self.plan(MODE='none', EVENT='pull_request', HEAD_REPOSITORY='outside/wiki', PR_NUMBER='8',
                                   RUNS_ON='vl-heavy', RUNS_ON_ARM64='vl-heavy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values['fork'], 'true')
        self.assertEqual([p['runner'] for p in json.loads(values['matrix'])['include']],
                         ['ubuntu-24.04', 'ubuntu-24.04-arm'])

    def test_reject_malformed_runner_labels(self):
        for changes in (dict(RUNS_ON=''), dict(RUNS_ON_ARM64=''), dict(RUNS_ON='["self-hosted"]'),
                        dict(RUNS_ON='"vl-light"'), dict(RUNS_ON='vl light'), dict(RUNS_ON='vl\ninjected=value'),
                        dict(RUNS_ON='x' * 65)):
            with self.subTest(changes=changes):
                result, output = self.plan(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, {})

    def test_dispatch_from_main_publishes_the_main_tag(self):
        result, values = self.plan(EVENT='workflow_dispatch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((values['tag'], values['publish']), ('sha-' + 'a' * 40, 'true'))
        for changes in (dict(EVENT='workflow_dispatch', REF='refs/heads/feature'),
                        dict(EVENT='workflow_dispatch', MODE='preview'),
                        dict(EVENT='workflow_dispatch', REF='refs/tags/v1.0.0')):
            with self.subTest(changes=changes):
                result, output = self.plan(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, {})

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
        self.assertEqual(set(values), {'matrix', 'image', 'tag', 'publish', 'build-args', 'fork'})

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

    def release_merge(self, pulls, files, checks=GREEN, merge_exit=0, with_checks_log=False, mode='always',
                      closed=None, compare='ahead', logs=None):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / 'gh').write_text(FAKE_GH)
            (d / 'gh').chmod(0o755)
            (d / 'pulls.json').write_text(json.dumps(pulls))
            (d / 'files.json').write_text(json.dumps(files))
            (d / 'checks.json').write_text(json.dumps(checks))
            (d / 'closed.json').write_text(json.dumps(closed or []))
            (d / 'compare.txt').write_text(compare)
            if merge_exit:
                (d / 'merge_exit').write_text(str(merge_exit))
            env = dict(PATH=f"{d}:{os.environ['PATH']}", FAKE_DIR=str(d), GITHUB_REPOSITORY='verbanium-lab/wiki',
                       GITHUB_REPOSITORY_OWNER='verbanium-lab', GITHUB_STEP_SUMMARY=str(d / 'summary'),
                       GH_TOKEN='app-token', CHECKS_TOKEN='job-token', RELEASE_MODE=mode)
            result = subprocess.run([sys.executable, '-c', RELEASE_PR], env=env, capture_output=True, text=True)
            log = (d / 'merge.log').read_text() if (d / 'merge.log').exists() else ''
            checks_log = (d / 'checks.log').read_text() if (d / 'checks.log').exists() else ''
            if logs is not None:
                for name, file in (('closed', 'closed.log'), ('compare', 'compare.log'), ('summary', 'summary')):
                    logs[name] = (d / file).read_text() if (d / file).exists() else ''
            return (result, log, checks_log) if with_checks_log else (result, log)

    def test_release_pr_metadata_only_is_merged_directly_at_the_checked_head(self):
        result, log, checks_log = self.release_merge([release_pr()], GOOD_FILES, with_checks_log=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.strip(), 'app-token api --method PUT repos/verbanium-lab/wiki/pulls/167/merge '
                         '-f merge_method=squash -f sha=' + 'b' * 40)
        # Checks are read with the job token at exactly the PR head; the App token only merges.
        self.assertEqual(checks_log.strip(),
                         'job-token repos/verbanium-lab/wiki/commits/' + 'b' * 40 + '/check-runs?check_name=ci&per_page=100')

    def test_release_pr_waits_until_ci_passes_on_the_head(self):
        for checks in ([], [ci_run(status='in_progress', conclusion=None)],
                       [ci_run(), ci_run(id=2, status='queued', conclusion=None)],
                       [ci_run(conclusion='failure')], [ci_run(conclusion='cancelled')],
                       [ci_run(app=dict(id=99))], [ci_run(name='checks')],
                       [ci_run(), ci_run(id=2, conclusion='failure', completed_at='2026-11-02T05:00:00Z')]):
            with self.subTest(checks=checks):
                result, log = self.release_merge([release_pr()], GOOD_FILES, checks=checks)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log, '')

    def test_release_pr_merge_refusal_fails_the_job(self):
        result, log = self.release_merge([release_pr()], GOOD_FILES, merge_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pulls/167/merge', log)

    def test_release_mode_always_is_todays_behaviour_and_reads_no_descriptions(self):
        logs = {}
        result, log = self.release_merge([release_pr()], GOOD_FILES, mode='always', logs=logs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('pulls/167/merge', log)
        self.assertEqual((logs['closed'], logs['compare']), ('', ''))

    def test_release_mode_never_and_unknown_modes_do_not_merge(self):
        for mode in ('never', '', 'ON-REQUEST', 'on request', 'on-request; true'):
            with self.subTest(mode=mode):
                result, log = self.release_merge([release_pr()], GOOD_FILES, mode=mode,
                                                 closed=[merged_pr(1, body=TICKED_BODY)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(log, '')

    def test_release_mode_on_request_merges_when_a_pull_request_ticked_the_box(self):
        logs = {}
        closed = [merged_pr(9, '2026-11-27T10:00:00Z', TICKED_BODY), merged_pr(8, '2026-11-27T09:00:00Z', 'No box')]
        result, log = self.release_merge([release_pr()], GOOD_FILES, mode='on-request', closed=closed, logs=logs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.strip(), 'app-token api --method PUT repos/verbanium-lab/wiki/pulls/167/merge '
                         '-f merge_method=squash -f sha=' + 'b' * 40)
        self.assertIn('state=closed&base=main&sort=updated&direction=desc&per_page=50', logs['closed'])
        self.assertIn('compare/' + 'c' * 40 + '...' + 'b' * 40, logs['compare'])
        self.assertIn('Release requested: pull request #9 asked for the release.', logs['summary'])

    def test_release_mode_on_request_a_later_unticked_merge_does_not_cancel_the_request(self):
        logs = {}
        closed = [merged_pr(9, '2026-11-27T10:00:00Z', 'Fixes a typo'), merged_pr(8, '2026-11-27T09:00:00Z', TICKED_BODY)]
        result, log = self.release_merge([release_pr()], GOOD_FILES, mode='on-request', closed=closed, logs=logs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('pulls/167/merge', log)
        self.assertIn('Release requested: pull request #8 asked for the release.', logs['summary'])

    def test_release_mode_on_request_leaves_the_release_pr_to_the_owner(self):
        for name, closed, compare in (
            ('unticked', [merged_pr(9, body='- [ ] Release and deploy to release-candidate after merge')], 'ahead'),
            ('no merged pull request', [], 'ahead'),
            ('not contained', [merged_pr(9, body=TICKED_BODY)], 'diverged'),
            ('compare failed', [merged_pr(9, body=TICKED_BODY)], 'error'),
            ('only unticked', [merged_pr(8, '2026-11-27T09:00:00Z', 'Fixes a typo'),
                               merged_pr(9, '2026-11-27T10:00:00Z', 'Fixes another typo')], 'ahead'),
        ):
            with self.subTest(name):
                logs = {}
                result, log = self.release_merge([release_pr()], GOOD_FILES, mode='on-request', closed=closed,
                                                 compare=compare, logs=logs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log, '')
                self.assertIn('left for the owner (release-mode on-request)', logs['summary'])

    def test_release_mode_on_request_keeps_every_other_safety_check(self):
        closed = [merged_pr(9, body=TICKED_BODY)]
        code = dict(filename='portal/app/page.tsx', status='modified', patch='@@\n+x')
        for pulls, files, checks in (
            ([release_pr(user=dict(login='someone', type='User'))], GOOD_FILES, GREEN),
            ([release_pr(draft=True)], GOOD_FILES, GREEN),
            ([release_pr()], GOOD_FILES + [code], GREEN),
            ([release_pr()], GOOD_FILES, [ci_run(conclusion='failure')]),
            ([release_pr()], GOOD_FILES, []),
        ):
            with self.subTest(pr=pulls[0]['user']['login'], draft=pulls[0]['draft'], checks=checks, files=len(files)):
                logs = {}
                result, log = self.release_merge(pulls, files, checks=checks, mode='on-request', closed=closed, logs=logs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log, '')
                self.assertEqual(logs['closed'], '')  # the description is read only once the rest passed

    def decision(self):
        namespace = {}
        exec(compile(RELEASE_PR.split(DECISION_END)[0], '<decision>', 'exec'), namespace)
        return namespace

    def test_release_checkbox_counts_only_as_a_ticked_line_of_its_own(self):
        decision = self.decision()
        ticked = decision['ticked']
        self.assertEqual(decision['CHECKBOX'], TICKED_BODY)
        for name, body in (
            ('ticked', TICKED_BODY),
            ('upper case x', '- [X] Release and deploy to release-candidate after merge'),
            ('star bullet', '* [x] Release and deploy to release-candidate after merge'),
            ('crlf', 'Closes #1\r\n\r\n' + TICKED_BODY + '\r\n\r\nNone.'),
            ('trailing space', TICKED_BODY + '  '),
            ('after a closed comment', '<!-- hint -->\n' + TICKED_BODY),
            ('after a closed fence', '```\ncode\n```\n' + TICKED_BODY),
            ('after a one-line code span', '```not a fence```\n' + TICKED_BODY),
            ('after a fence closed by a longer one', '```\n```` \n' + TICKED_BODY),
        ):
            with self.subTest(name):
                self.assertTrue(ticked(body))
        for name, body in (
            ('unticked', '- [ ] Release and deploy to release-candidate after merge'),
            ('other label', '- [x] Deploy to RC after merge'),
            ('extra text', TICKED_BODY + ' please'),
            ('prefix text', 'Note: ' + TICKED_BODY),
            ('inline code', '`' + TICKED_BODY + '`'),
            ('quoted', '> ' + TICKED_BODY),
            ('indented code', '    ' + TICKED_BODY),
            ('html comment', '<!-- ' + TICKED_BODY + ' -->'),
            ('html comment over lines', 'Closes #1\n<!--\nTicked:\n' + TICKED_BODY + '\n-->\n- [ ] x'),
            ('unterminated comment', '<!--\n' + TICKED_BODY),
            ('backtick fence', '```\n' + TICKED_BODY + '\n```'),
            ('backtick fence with language', '```md\n' + TICKED_BODY + '\n```'),
            ('tilde fence', '~~~\n' + TICKED_BODY + '\n~~~'),
            ('unterminated fence', 'text\n```\n' + TICKED_BODY),
            ('closing fence must be as long', '````\n```\n' + TICKED_BODY + '\n````'),
            ('comment swallowing a fence marker', '```\n<!--\n```\n' + TICKED_BODY),
            ('empty', ''), ('none', None), ('number', 5),
        ):
            with self.subTest(name):
                self.assertFalse(ticked(body))

    def test_release_request_is_any_ticked_pull_request_in_the_release_pr(self):
        decide = self.decision()['release_request']
        repo, ref = 'verbanium-lab/wiki', 'release-please--branches--main'
        older, newer = '2026-11-26T10:00:00Z', '2026-11-27T10:00:00Z'

        def run(closed, contained=lambda sha: True):
            return decide(closed, repo, ref, contained)

        self.assertTrue(run([merged_pr(5, older, TICKED_BODY)])[0])
        self.assertFalse(run([merged_pr(5, older, 'plain')])[0])
        # Ticked, then unticked: the later merge does not cancel the request, whatever the API order is.
        ticked_old, plain_new = merged_pr(5, older, TICKED_BODY), merged_pr(6, newer, 'plain')
        for closed in ([ticked_old, plain_new], [plain_new, ticked_old]):
            requested, reason = run(closed)
            self.assertTrue(requested)
            self.assertIn('#5 asked for the release', reason)
        # Unticked only: no request.
        plain_old = merged_pr(5, older, 'plain')
        self.assertFalse(run([plain_old, plain_new])[0])
        plain_old, ticked_new = merged_pr(5, older, 'plain'), merged_pr(6, newer, TICKED_BODY)
        for closed in ([plain_old, ticked_new], [ticked_new, plain_old]):
            requested, reason = run(closed)
            self.assertTrue(requested)
            self.assertIn('#6 asked for the release', reason)
        # Ticked but not contained in the Release PR head: no request.
        requested, reason = run([merged_pr(5, older, TICKED_BODY)], lambda sha: False)
        self.assertFalse(requested)
        self.assertIn('#5', reason)
        self.assertIn('not in the Release PR head yet', reason)
        # Two ticked, the older contained and the newer not: released, and the contained one is named.
        old_sha, new_sha = 'a' * 40, 'd' * 40
        two = [merged_pr(5, older, TICKED_BODY, sha=old_sha), merged_pr(6, newer, TICKED_BODY, sha=new_sha),
               merged_pr(7, newer, 'plain')]
        seen = []
        requested, reason = run(two, lambda sha: seen.append(sha) or sha == old_sha)
        self.assertTrue(requested)
        self.assertIn('#5 asked for the release', reason)
        self.assertNotIn('#6', reason)
        self.assertEqual(seen, [new_sha, old_sha])  # newest ticked first, stop at the first contained
        # Only ticked pull requests are checked for containment; the first contained one stops the search.
        seen = []
        self.assertTrue(run(two, lambda sha: seen.append(sha) or True)[0])
        self.assertEqual(seen, [new_sha])
        seen = []
        self.assertFalse(run([merged_pr(5, older, 'plain', sha=old_sha)], lambda sha: seen.append(sha) or True)[0])
        self.assertEqual(seen, [])
        # No merged pull request that can be in the Release PR.
        self.assertFalse(run([])[0])
        self.assertFalse(run([merged_pr(5, older, TICKED_BODY, merged=False)])[0])
        self.assertFalse(run([merged_pr(5, older, TICKED_BODY, base='next')])[0])
        self.assertFalse(run([merged_pr(5, older, TICKED_BODY, sha='unknown')])[0])
        # Pull requests merged before the last release are already released; Release PRs never count.
        previous = merged_pr(7, newer, TICKED_BODY, head_ref=ref)
        self.assertFalse(run([previous, merged_pr(5, older, TICKED_BODY)])[0])
        self.assertTrue(run([previous, merged_pr(8, '2026-11-27T11:00:00Z', TICKED_BODY)])[0])
        self.assertFalse(run([previous])[0])
        # A fork branch with the Release PR's name is an ordinary pull request.
        self.assertTrue(run([merged_pr(9, newer, TICKED_BODY, head_ref=ref, head_repo='someone/wiki')])[0])
        # The merge commit has to be in the Release PR head; the check sees exactly that commit.
        seen = []
        self.assertFalse(run([merged_pr(5, older, TICKED_BODY)], lambda sha: seen.append(sha) or False)[0])
        self.assertEqual(seen, ['c' * 40])

    def test_release_request_fails_closed_when_the_closed_page_is_incomplete(self):
        decide = self.decision()['release_request']
        repo, ref = 'verbanium-lab/wiki', 'release-please--branches--main'
        page = decide.__globals__['CLOSED_PAGE']
        self.assertEqual(page, 50)
        fill = lambda n, **changes: [merged_pr(100 + i, '2026-11-20T10:00:00Z', 'plain', **changes) for i in range(n)]
        incomplete = 'window is incomplete'
        ticked_new = merged_pr(5, '2026-11-27T10:00:00Z', TICKED_BODY)
        # A full page without a Release PR: never a request, even with a ticked, contained pull request.
        for closed in (fill(page), fill(page, merged=False), fill(page - 1) + [ticked_new],
                       fill(page - 1, merged=False) + [ticked_new]):
            with self.subTest(size=len(closed)):
                requested, reason = decide(closed, repo, ref, lambda sha: True)
                self.assertFalse(requested)
                self.assertIn(incomplete, reason)
        # A short page, or a full page that holds a Release PR, is complete and decides as before.
        release = merged_pr(7, '2026-11-26T10:00:00Z', TICKED_BODY, head_ref=ref)
        for closed in (fill(page - 2) + [ticked_new], fill(page - 2) + [release, ticked_new]):
            with self.subTest(size=len(closed)):
                requested, reason = decide(closed, repo, ref, lambda sha: True)
                self.assertTrue(requested)
                self.assertNotIn(incomplete, reason)

    def test_release_mode_on_request_summary_names_the_full_page(self):
        logs = {}
        closed = [merged_pr(100 + i, '2026-11-20T10:00:00Z', 'plain') for i in range(50)]
        result, log = self.release_merge([release_pr()], GOOD_FILES, mode='on-request', closed=closed, logs=logs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log, '')
        self.assertIn('the window is incomplete', logs['summary'])

    def test_release_pr_absent_or_ambiguous_is_left_alone(self):
        for pulls in ([], [release_pr(), release_pr(number=168)]):
            with self.subTest(count=len(pulls)):
                result, log = self.release_merge(pulls, GOOD_FILES)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log, '')

    def test_release_pr_outside_the_boundary_is_left_for_the_owner(self):
        code = dict(filename='portal/app/page.tsx', status='modified', patch='@@\n+evil()')
        dependency = dict(filename='portal/package.json', status='modified',
                          patch='@@\n-    "next": "16.0.0",\n+    "next": "16.0.1",')
        manifest = dict(filename='.release-please-manifest.json', status='modified',
                        patch='@@\n-  "portal": "1.7.1"\n+  "portal": "1.7.2", "x": 1')
        removed = dict(filename='portal/CHANGELOG.md', status='removed', patch='')
        for pulls, files in (
            ([release_pr(user=dict(login='someone', type='User'))], GOOD_FILES),
            ([release_pr(head=dict(sha='b' * 40, repo=dict(full_name='fork/wiki')))], GOOD_FILES),
            ([release_pr(head=dict(sha='b' * 40, repo=None))], GOOD_FILES),
            ([release_pr(draft=True)], GOOD_FILES),
            ([release_pr()], GOOD_FILES + [code]),
            ([release_pr()], [dependency]),
            ([release_pr()], [manifest]),
            ([release_pr()], [removed]),
            ([release_pr()], []),
            ([release_pr()], [dict(filename='portal/package.json', status='modified')]),
        ):
            with self.subTest(files=[f['filename'] for f in files], pr=pulls[0]['user']['login']):
                result, log = self.release_merge(pulls, files)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log, '')

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


def embedded_shell(path, step):
    """The `run: |` block of one named step, dedented."""
    text = (ROOT / path).read_text().split('- name: ' + step + '\n', 1)[1]
    match = re.search(r'(?m)^( +)run: \|\n((?:\1  .*\n|[ \t]*\n)+)', text)
    if not match:
        raise AssertionError('Shell run block not found')
    return textwrap.dedent(match[2])


BROWSER = '.github/workflows/browser.yml'
BROWSER_INPUTS = embedded_shell(BROWSER, 'Check the inputs')
BROWSER_TAG = embedded_shell(BROWSER, 'Read the image tag from the installed package')
BROWSER_VERSION = embedded_shell(BROWSER, 'Playwright matches the container image')


def run_shell(script, env, fake_pnpm=''):
    """Run a step script the way the runner does (bash -eo pipefail), with a fake pnpm first on PATH."""
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'pnpm').write_text('#!/bin/sh\n' + fake_pnpm + '\n')
        (bin_dir / 'pnpm').chmod(0o755)
        output, summary = Path(tmp) / 'output', Path(tmp) / 'summary'
        output.touch()
        summary.touch()
        result = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], capture_output=True, text=True,
                                env=dict(PATH=f'{bin_dir}:{os.environ["PATH"]}', GITHUB_OUTPUT=str(output),
                                         GITHUB_STEP_SUMMARY=str(summary), **env))
        return result, output.read_text()


class BrowserWorkflowTests(unittest.TestCase):
    """The shell steps of browser.yml against a fake pnpm; the container itself needs a real run."""

    TAG = 'mcr.microsoft.com/playwright:v1.65.1-noble'
    PRINT_OUTPUT = 'printf "%s" "$FAKE_OUTPUT"'

    def tag(self, printed):
        return run_shell(BROWSER_TAG, dict(PACKAGE_FILTER='@verbanium-lab/example-web', FAKE_OUTPUT=printed),
                         self.PRINT_OUTPUT)

    def test_the_image_tag_becomes_the_job_output(self):
        for printed in (self.TAG, self.TAG + '\n', 'mcr.microsoft.com/playwright:v1.66.0-alpha-2026-11-19-noble'):
            with self.subTest(printed=printed):
                result, output = self.tag(printed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(github_output(output), {'tag': printed.strip()})

    def test_only_a_playwright_image_on_mcr_can_become_the_container(self):
        for printed in (
            '', 'ubuntu:24.04', 'docker.io/library/alpine:latest',
            'mcr.microsoft.com/playwright:v1.65.1-jammy',
            'mcr.microsoft.com/playwright:1.65.1-noble',
            'mcr.microsoft.com/playwright:v1.65.1-noble --privileged',
            'mcr.example.com/mcr.microsoft.com/playwright:v1.65.1-noble',
            'mcr.microsoft.com/playwright:v1.65.1-noble\nmcr.microsoft.com/playwright:v1.64.0-noble',
            self.TAG + '\ntag=mcr.microsoft.com/playwright:v1.0.0-noble',
        ):
            with self.subTest(printed=printed):
                result, output = self.tag(printed)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')

    def test_a_failing_cli_fails_the_step(self):
        result, output = run_shell(BROWSER_TAG, dict(PACKAGE_FILTER='x'), 'exit 3')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')

    def version(self, image, installed):
        return run_shell(BROWSER_VERSION, dict(IMAGE=image, PACKAGE_FILTER='x', FAKE_VERSION=installed),
                         'printf "Version %s\\n" "$FAKE_VERSION"')

    def test_installed_playwright_must_match_the_image(self):
        result, _ = self.version(self.TAG, '1.65.1')
        self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.version('mcr.microsoft.com/playwright:v1.66.0-alpha-2026-11-19-noble', '1.66.0-alpha-2026-11-19')
        self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.version(self.TAG, '1.64.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('installed Playwright is 1.64.0, the image has 1.65.1', result.stdout)

    def inputs(self, **env):
        values = dict(STUB_COMMAND='', STACK_COMMAND='', POSTGRES='false', POSTGRES_IMAGE='')
        values.update(env)
        return run_shell(BROWSER_INPUTS, values)[0]

    def test_a_call_needs_a_command(self):
        self.assertNotEqual(self.inputs().returncode, 0)
        self.assertEqual(self.inputs(STUB_COMMAND='pnpm test').returncode, 0)
        self.assertEqual(self.inputs(STACK_COMMAND='pnpm test').returncode, 0)

    def test_postgres_needs_a_pinned_image_because_an_empty_image_starts_no_service(self):
        digest = 'postgres:18.6@sha256:' + 'a' * 64
        self.assertNotEqual(self.inputs(STUB_COMMAND='x', POSTGRES='true').returncode, 0)
        self.assertEqual(self.inputs(STUB_COMMAND='x', POSTGRES='true', POSTGRES_IMAGE=digest).returncode, 0)
        self.assertEqual(self.inputs(STUB_COMMAND='x', POSTGRES_IMAGE=digest).returncode, 0)

    def test_postgres_image_must_carry_a_digest(self):
        for image in ('postgres:18.6', 'postgres@sha256:abc', 'postgres:18.6@sha256:' + 'A' * 64,
                      'postgres:18.6@sha256:' + 'a' * 64 + ' --privileged', 'postgres:18.6@sha256:' + 'a' * 64 + '\n'):
            with self.subTest(image=image):
                result = self.inputs(STUB_COMMAND='x', POSTGRES='true', POSTGRES_IMAGE=image)
                self.assertNotEqual(result.returncode, 0)


class StatusWorkflowTests(unittest.TestCase):
    """Option C (2026-11-01): the README commit is opt-in."""

    def test_readme_commit_is_off_by_default(self):
        text = (ROOT / '.github/workflows/status.yml').read_text()
        block = text.split('commit-readme:', 1)[1].split('type: boolean', 1)[0]
        self.assertIn('default: false', block)
        step = text.split('- name: Commit status update', 1)[1].split('run:', 1)[0]
        self.assertIn('inputs.commit-readme &&', step)

    def test_status_scripts_come_from_this_workflow_commit(self):
        # github.action_ref inside the checkout step is the ref of actions/checkout itself.
        text = (ROOT / '.github/workflows/status.yml').read_text()
        step = text.split('- name: Check out status scripts', 1)[1].split('- name:', 1)[0]
        self.assertIn('ref: ${{ job.workflow_sha }}', step)
        self.assertNotIn('${{ github.action_ref }}', step)


if __name__ == '__main__':
    unittest.main()
