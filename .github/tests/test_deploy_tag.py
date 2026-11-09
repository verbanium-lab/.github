"""Offline deploy/retag regression tests; subprocesses never contact a registry or Coolify."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.github/scripts/retag-deployed.sh'
WORKFLOW = ROOT / '.github/workflows/deploy-coolify.yml'
IMAGE = 'ghcr.io/verbanium-lab/studio'
DIGEST = 'sha256:' + 'a' * 64

FAKE_DOCKER = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_LOG'], 'a') as log:
    print(json.dumps(args), file=log)
if args[:3] == ['buildx', 'imagetools', 'create']:
    sys.exit(int(os.environ.get('CREATE_EXIT', '0')))
if args[:3] == ['buildx', 'imagetools', 'inspect']:
    print(os.environ['ACTUAL_DIGEST'])
    sys.exit(int(os.environ.get('INSPECT_EXIT', '0')))
'''

FAKE_CURL = '''#!/usr/bin/env python3
import json, os, sys
url = sys.argv[-1]
if '/applications/' in url:
    print('{}')
elif '/deploy?uuid=' in url:
    print(json.dumps({'deployments': [{'deployment_uuid': 'test-deploy'}]}))
elif '/deployments/' in url:
    print(json.dumps({'status': os.environ['DEPLOY_STATUS']}))
else:
    print(json.dumps({'sha': os.environ['LIVE_SHA']}))
'''


class DeployTagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for name, source in [('docker', FAKE_DOCKER), ('curl', FAKE_CURL),
                             ('sleep', '#!/bin/sh\nexit 0\n'), ('seq', '#!/bin/sh\necho 1\n')]:
            path = self.directory / name
            path.write_text(source)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.directory) + os.pathsep + os.environ['PATH'],
                        IMAGE=IMAGE, DIGEST=DIGEST, TAG='deployed-prod', ACTUAL_DIGEST=DIGEST,
                        DOCKER_LOG=str(self.directory / 'docker.log'),
                        GITHUB_STEP_SUMMARY=str(self.directory / 'summary'),
                        GITHUB_OUTPUT=str(self.directory / 'output'))
        self.workflow = yaml.safe_load(WORKFLOW.read_text())

    def calls(self):
        log = self.directory / 'docker.log'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def run_script(self, **changes):
        return subprocess.run(['bash', str(SCRIPT)], env=dict(self.env, **changes),
                              capture_output=True, text=True)

    def test_manifest_only_retag_and_digest_check(self):
        for target in ('dev', 'rc', 'prod'):
            with self.subTest(target=target):
                result = self.run_script(TAG='deployed-' + target)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls()[-2:], [
                    ['buildx', 'imagetools', 'create', '--tag', IMAGE + ':deployed-' + target, IMAGE + '@' + DIGEST],
                    ['buildx', 'imagetools', 'inspect', IMAGE + ':deployed-' + target, '--format', '{{.Manifest.Digest}}'],
                ])
        self.assertIn('Result: match', (self.directory / 'summary').read_text())

    def test_retag_rejects_invalid_inputs_before_registry_access(self):
        for changes in ({'IMAGE': 'ghcr.io/other/studio'}, {'IMAGE': '--help'}, {'DIGEST': 'sha256:abc'},
                        {'TAG': 'deployed-production'}, {'TAG': 'prod\ninjected=true'}, {'IMAGE': ''}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.run_script(**changes).returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_retag_fails_on_registry_error_or_digest_mismatch(self):
        for changes in ({'CREATE_EXIT': '1'}, {'INSPECT_EXIT': '1'}, {'ACTUAL_DIGEST': 'sha256:' + 'b' * 64}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.run_script(**changes).returncode, 0)
        self.assertNotIn('Result: match', (self.directory / 'summary').read_text())

    def test_plan_validates_environment_and_exports_tag(self):
        plan = self.workflow['jobs']['plan']['steps'][0]['run']
        env = dict(self.env, APP='studio', TAGS=IMAGE + ':sha-' + 'a' * 40, SHA7='a' * 7,
                   BUILD_NUMBER='', APP_UUID='a' * 24, SITE_URL='https://studio.verbanium.dev',
                   VERIFY_URL='https://studio.verbanium.dev/healthz', VERIFY_FIELD='sha',
                   CLIENT_ID='Iv' + 'a' * 12, GITHUB_REPOSITORY_OWNER='verbanium-lab',
                   GITHUB_EVENT_NAME='push', GITHUB_REF='refs/heads/main', GITHUB_SHA='a' * 40)
        for environment, target in [('production', 'prod'), ('release-candidate', 'rc'), ('dev', 'dev'),
                                    ('production', 'dev'), ('dev', 'prod'), ('production', 'other')]:
            with self.subTest(environment=environment, target=target):
                output = self.directory / 'output'
                output.unlink(missing_ok=True)
                result = subprocess.run(['bash', '-e', '-c', plan],
                                        env=dict(env, TARGET_ENV=environment, TAG_ENV=target),
                                        capture_output=True, text=True)
                valid = (environment, target) in [('production', 'prod'), ('release-candidate', 'rc'), ('dev', 'dev')]
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                if valid:
                    self.assertIn('tag=deployed-' + target + '\n', output.read_text())
                else:
                    self.assertFalse(output.exists())

    def test_only_a_verified_live_deploy_reaches_retag(self):
        steps = self.workflow['jobs']['deploy']['steps']
        verify = next(s for s in steps if s['name'] == 'Deploy the digest in Coolify and wait for the live build')
        retag = next(s for s in steps if s.get('name') == 'Retag the deployed digest and verify')
        self.assertLess(steps.index(verify), steps.index(retag))
        self.assertNotIn('if', retag)
        script_dir = self.directory / '_github-defaults/.github/scripts'
        script_dir.mkdir(parents=True)
        shutil.copyfile(SCRIPT, script_dir / SCRIPT.name)
        env = dict(self.env, COOLIFY_URL='https://coolify.verbanium.dev', COOLIFY_TOKEN='synthetic',
                   APP='studio', APP_UUID='a' * 24, SHA7='a' * 7, BUILD_NUMBER='',
                   VERIFY_URL='https://studio.verbanium.dev/healthz', VERIFY_FIELD='sha')
        for status, live, success in [('failed', 'aaaaaaa', False), ('cancelled', 'aaaaaaa', False),
                                      ('running', 'aaaaaaa', False), ('finished', 'bbbbbbb', False),
                                      ('finished', 'aaaaaaa', True)]:
            with self.subTest(status=status, live=live):
                result = subprocess.run(['bash', '-e', '-c', verify['run']],
                                        env=dict(env, DEPLOY_STATUS=status, LIVE_SHA=live),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                if result.returncode == 0:
                    tagged = subprocess.run(['bash', '-e', '-c', retag['run']], cwd=self.directory,
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(tagged.returncode, 0, tagged.stderr)
                self.assertEqual(bool(self.calls()), success)


if __name__ == '__main__':
    unittest.main()
