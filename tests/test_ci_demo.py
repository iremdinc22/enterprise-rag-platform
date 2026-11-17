"""Network-free checks for CI guards, isolation, artifact publication and cleanup."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.ci import demo


class CIGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Linux',
            'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'RUNNER_TEMP': str(self.base)}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.root, run = demo.location(); self.root.mkdir(); (self.root / 'artifacts').mkdir()
        self.state = {'run': run, 'workspace': str(Path.cwd().resolve()), 'sha': 'a' * 40,
            'deadline': time.monotonic() + 300, 'controlled_project': 'atlas-controlled-123-1',
            'demo_project': 'atlas-demo-123-1', 'app_image': 'atlas-validation-app:123-1',
            'frontend_image': 'atlas-validation-frontend:123-1'}
        demo.private_json(self.root / 'state.json', self.state)

    def test_local_invocation_fails_before_subprocess(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), patch('subprocess.Popen') as popen:
            with self.assertRaises(demo.CheckFailed): demo.settings()
            popen.assert_not_called()

    def test_invalid_run_identity_and_workspace_rejected(self):
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '../foreign'}):
            with self.assertRaises(demo.CheckFailed): demo.location()
        self.state['workspace'] = '/foreign'
        (self.root / 'state.json').write_text(json.dumps(self.state))
        with self.assertRaises(demo.CheckFailed): demo.settings()

    def test_private_creation_refuses_overwrite_and_has_private_permissions(self):
        p = self.root / 'private.json'; demo.private_json(p, {'test': 'value'})
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): demo.private_json(p, {})

    def test_resource_stop_blocks_new_command_without_starting_process(self):
        (self.root / 'resource-stop').touch()
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(demo.CheckFailed): demo.command(['must-not-run'])
            popen.assert_not_called()

    def test_execution_budget_blocks_new_work(self):
        self.state['deadline'] = time.monotonic() - 1
        (self.root / 'state.json').write_text(json.dumps(self.state))
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(demo.CheckFailed): demo.command(['must-not-run'])
            popen.assert_not_called()

    def config(self):
        project = self.state['demo_project']
        return {'services': {
            'api': {'environment': {'DEMO_PROVIDER_MODE': 'deterministic', 'QDRANT_URL': 'http://qdrant:6333',
                'REDIS_CACHE_URL': 'redis://redis:6379/2'}, 'networks': {'demo': None}},
            'worker': {'environment': {'DEMO_PROVIDER_MODE': 'deterministic', 'QDRANT_URL': 'http://qdrant:6333',
                'REDIS_CACHE_URL': 'redis://redis:6379/2'}, 'networks': {'demo': None}},
            'frontend': {'ports': [{'host_ip': '127.0.0.1', 'published': '18030'}]},
            'qdrant': {}, 'redis': {}},
            'networks': {'demo': {'internal': True}}, 'volumes': {'uploads': {'name': project + '_uploads'}}}

    def test_safe_demo_config_and_unsafe_variants(self):
        project = self.state['demo_project']; safe = self.config()
        demo.validate_compose(safe, project, True)
        variants = []
        for value in ['0.0.0.0', '::']:
            c = copy.deepcopy(safe); c['services']['frontend']['ports'][0]['host_ip'] = value; variants.append(c)
        c = copy.deepcopy(safe); c['services']['api']['ports'] = [{'published': '8000'}]; variants.append(c)
        c = copy.deepcopy(safe); c['services']['api']['volumes'] = [{'type': 'bind'}]; variants.append(c)
        c = copy.deepcopy(safe); c['services']['api']['environment']['OPENAI_API_KEY'] = 'test-not-a-key'; variants.append(c)
        c = copy.deepcopy(safe); c['services']['worker']['networks']['edge'] = None; variants.append(c)
        c = copy.deepcopy(safe); c['volumes']['uploads']['name'] = 'existing_uploads'; variants.append(c)
        c = copy.deepcopy(safe); c['networks']['demo']['internal'] = False; variants.append(c)
        c = copy.deepcopy(safe); c['services']['api']['environment']['DEMO_PROVIDER_MODE'] = 'disabled'; variants.append(c)
        for config in variants:
            with self.subTest(config=config), self.assertRaises(demo.CheckFailed):
                demo.validate_compose(config, project, True)

    def test_publish_only_allowlisted_files(self):
        demo.output(self.root, 'http.json', {'checks': ['safe synthetic check']})
        demo.publish()
        self.assertEqual({p.name for p in (self.root / 'publish').iterdir()}, {'http.json', 'resources.json'})

    def test_unlisted_artifact_and_jwt_pattern_fail_closed(self):
        path = self.root / 'artifacts' / 'credentials.json'; path.write_text('{}')
        with self.assertRaises(demo.CheckFailed): demo.publish()
        self.assertFalse((self.root / 'publish').exists()); path.unlink()
        demo.output(self.root, 'http.json', {'bad': 'eyJ' + 'a' * 20 + '.' + 'b' * 20 + '.' + 'c' * 20})
        with self.assertRaises(demo.CheckFailed): demo.publish()
        self.assertFalse((self.root / 'publish').exists())

    def test_symlink_and_bad_image_fail_closed(self):
        outside = self.root / 'private.json'; outside.write_text('{}')
        link = self.root / 'artifacts' / 'http.json'; link.symlink_to(outside)
        with self.assertRaises(demo.CheckFailed): demo.publish()
        link.unlink(); (self.root / 'artifacts' / 'login.png').write_bytes(b'not PNG')
        with self.assertRaises(demo.CheckFailed): demo.publish()

    def test_cleanup_refuses_unowned_volume_and_removes_private_credentials(self):
        (self.root / 'ownership-confirmed').touch()
        demo.private_json(self.root / 'credentials.json', {'acme-demo': 'temporary-private-value'})
        demo.private_json(self.root / 'signing.json', {'secret': 'temporary-private-signing-value'})
        calls = []
        def fake(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['volume', 'ls']: return 0, 'foreign_volume\n'
            if args[1:3] == ['volume', 'inspect']: return 0, json.dumps([{'Labels': {'com.docker.compose.project': 'foreign'}}])
            return 0, ''
        with patch.object(demo, 'compose'), patch.object(demo, 'command', side_effect=fake):
            with self.assertRaises(demo.CheckFailed): demo.cleanup()
        self.assertFalse(any(args[1:3] == ['volume', 'rm'] for args in calls))
        self.assertFalse((self.root / 'credentials.json').exists())
        self.assertFalse((self.root / 'signing.json').exists())

    def test_cleanup_checks_actual_secret_before_allowing_publish(self):
        secret = 'private-password-for-redaction-test'
        demo.private_json(self.root / 'credentials.json', {'acme-demo': secret})
        demo.output(self.root, 'http.json', {'unsafe': secret})
        demo.cleanup()  # No ownership marker: no Docker operations.
        self.assertTrue((self.root / 'artifact-redaction-failed').exists())
        self.assertFalse((self.root / 'credentials.json').exists())
        with self.assertRaises(demo.CheckFailed): demo.publish()

    def test_artifact_size_limit_before_publication(self):
        (self.root / 'artifacts' / 'login.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * 128)
        with patch.object(demo, 'MAX_ARTIFACT_BYTES', 100):
            with self.assertRaises(demo.CheckFailed): demo.publish()
        self.assertFalse((self.root / 'publish').exists())

    def test_sustained_memory_guard_stops_at_fifteen_seconds(self):
        clock = [10.0]
        sample = {'available_memory_bytes': 100, 'free_disk_bytes': 2 * 1024**3}
        def sleep(seconds): clock[0] += seconds
        with patch.object(demo, 'resource_sample', return_value=sample), \
             patch.object(demo.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(demo.time, 'sleep', side_effect=sleep):
            demo.monitor()
        self.assertTrue((self.root / 'resource-stop').exists())
        self.assertEqual(clock[0], 25.0)

    def test_active_command_is_terminated_on_resource_stop(self):
        from unittest.mock import Mock
        process = Mock(); process.pid = 999999; process.poll.side_effect = [None, None]
        def stop(_): (self.root / 'resource-stop').touch()
        with patch.object(demo.subprocess, 'Popen', return_value=process), \
             patch.object(demo.time, 'sleep', side_effect=stop), patch.object(demo.os, 'killpg') as kill:
            with self.assertRaises(demo.CheckFailed): demo.command(['fake-command'])
        kill.assert_called_once_with(999999, demo.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=10)

    def test_duplicate_no_store_headers_are_accepted(self):
        from types import SimpleNamespace
        self.assertTrue(demo.has_no_store(SimpleNamespace(headers={'cache-control': 'no-store, no-store'})))
        self.assertFalse(demo.has_no_store(SimpleNamespace(headers={'cache-control': 'public'})))

    def test_owned_cleanup_scopes_volumes_and_image_tags(self):
        (self.root / 'ownership-confirmed').touch(); calls = []
        project = self.state['demo_project']; name = project + '_uploads'
        def fake(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['volume', 'ls']:
                return 0, name if args[-1].endswith(project) else ''
            if args[1:3] == ['volume', 'inspect']:
                return 0, json.dumps([{'Labels': {'com.docker.compose.project': project}}])
            return 0, ''
        with patch.object(demo, 'compose') as compose, patch.object(demo, 'command', side_effect=fake):
            demo.cleanup()
        self.assertEqual(compose.call_count, 2)
        self.assertIn(['docker', 'volume', 'rm', name], calls)
        self.assertEqual([args[-1] for args in calls if args[1:3] == ['image', 'rm']],
                         [self.state['app_image'], self.state['frontend_image']])
        self.assertFalse(any('prune' in args or '--force' in args for args in calls))

    def test_missing_or_stale_monitor_prevents_commands(self):
        (self.root / 'monitor-required').touch()
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(demo.CheckFailed): demo.command(['must-not-run'])
            (self.root / 'resource-heartbeat').write_text(str(time.monotonic() - 30))
            with self.assertRaises(demo.CheckFailed): demo.command(['must-not-run'])
            popen.assert_not_called()
        (self.root / 'resource-heartbeat').write_text(str(time.monotonic()))
        self.assertFalse(demo.monitor_failed(self.root))

    def test_controlled_report_drops_sensitive_failure_messages(self):
        import io
        from tests.ci.controlled import SanitizedResult
        class Example(unittest.TestCase):
            def test_sensitive(self):
                raise AssertionError('PRIVATE-CREDENTIAL-SENTINEL')
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=SanitizedResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Example))
        report = json.dumps(result.cases)
        self.assertNotIn('PRIVATE-CREDENTIAL-SENTINEL', report)
        self.assertEqual(result.cases, [{'name': 'test_sensitive', 'status': 'failed', 'category': 'AssertionError'}])


if __name__ == '__main__':
    unittest.main()
