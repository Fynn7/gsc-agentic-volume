"""Run lifecycle regressions: unique run directories, duplicate prevention, provenance.

No Codex harness, model or renderer is started. `execute` (the model/harness phase)
is replaced with a test double or an HTTP failure, so these tests never recurse into
the real agent harness.
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_lifecycle as lifecycle
import run_local_agent as launcher


def snapshot(directory):
    """Byte hashes of every file below `directory`, keyed by relative posix path."""
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*')) if path.is_file()}


class LifecyclePrimitives(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_run_id_uses_random_token_not_only_the_clock(self):
        stamp = 1_760_000_000.0
        first = lifecycle.make_run_id(now=stamp)
        second = lifecycle.make_run_id(now=stamp)
        self.assertNotEqual(first, second)
        self.assertIn(time.strftime('%Y%m%d-%H%M%S', time.localtime(stamp)), first)

    def test_reserve_creates_a_fresh_directory(self):
        base = self.root / 'nested' / 'results'
        directory, run_id = lifecycle.reserve_directory(base)
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.name, run_id)
        self.assertEqual(directory.parent, base)

    def test_reserve_retries_on_name_collision_and_keeps_the_old_directory(self):
        base = self.root / 'results'; base.mkdir()
        taken = base / 'run-fixed'
        taken.mkdir(); (taken / 'evidence.json').write_text('original')
        candidates = iter(['run-fixed', 'run-fixed', 'run-unique'])
        directory, run_id = lifecycle.reserve_directory(base, run_id_factory=lambda: next(candidates))
        self.assertEqual(run_id, 'run-unique')
        self.assertTrue(directory.is_dir())
        self.assertEqual((taken / 'evidence.json').read_text(), 'original')

    def test_same_timestamp_different_token_stays_distinct(self):
        base = self.root / 'results'
        stamp = time.strftime('%Y%m%d-%H%M%S')
        ids = iter(['run-%s-aaa' % stamp, 'run-%s-bbb' % stamp])
        first, first_id = lifecycle.reserve_directory(base, run_id_factory=lambda: next(ids))
        second, second_id = lifecycle.reserve_directory(base, run_id_factory=lambda: next(ids))
        self.assertNotEqual(first_id, second_id)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())

    def test_reserve_exhaustion_reports_clearly(self):
        base = self.root / 'results'; base.mkdir()
        (base / 'run-dup').mkdir()
        with self.assertRaisesRegex(lifecycle.ReservationError, 'Could not reserve'):
            lifecycle.reserve_directory(base, run_id_factory=lambda: 'run-dup', attempts=3)

    def test_reserve_rejects_a_file_as_base(self):
        base = self.root / 'not-a-directory'; base.write_text('occupied')
        with self.assertRaisesRegex(lifecycle.ReservationError, 'not a directory'):
            lifecycle.reserve_directory(base)

    def test_concurrent_reservations_never_share_a_directory(self):
        base = self.root / 'results'
        results, errors, guard = [], [], threading.Lock()

        def worker():
            try:
                directory, _ = lifecycle.reserve_directory(base)
                with guard: results.append(directory)
            except Exception as error:  # pragma: no cover - surfaced by assertion below
                with guard: errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(results)), 8)
        self.assertTrue(all(path.is_dir() for path in results))

    def test_lock_refuses_a_live_owner(self):
        directory = self.root / 'locks'; directory.mkdir()
        key = 'deadbeefdeadbeef'
        (directory / (key + '.lock')).write_text(json.dumps({'pid': os.getpid(), 'runId': 'run-live', 'directory': 'D:/x'}))
        with self.assertRaisesRegex(lifecycle.RunConflictError, 'already has a running launcher'):
            lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()})

    def test_lock_refuses_an_orphaned_harness_even_if_the_launcher_is_gone(self):
        directory = self.root / 'locks'; directory.mkdir()
        key = 'a23456789abcdef0'
        (directory / (key + '.lock')).write_text(json.dumps(
            {'pid': 99999999, 'harnessPid': os.getpid(), 'phase': 'harness-running', 'runId': 'run-orphan'}))
        with self.assertRaisesRegex(lifecycle.RunConflictError, 'harnessPid'):
            lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()})

    def test_lock_is_reclaimed_when_the_owner_is_gone(self):
        directory = self.root / 'locks'; directory.mkdir()
        key = 'cafecafecafecafe'
        (directory / (key + '.lock')).write_text(json.dumps({'pid': 99999999, 'runId': 'run-dead'}))
        lock = lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()})
        self.assertTrue((directory / (key + '.lock')).is_file())
        lock.release()
        self.assertFalse((directory / (key + '.lock')).exists())

    def test_unreadable_recent_lock_fails_closed_then_old_lock_is_reclaimed(self):
        directory = self.root / 'locks'; directory.mkdir()
        key = '0123456789abcdef'
        path = directory / (key + '.lock')
        path.write_text('{ truncated')
        with self.assertRaisesRegex(lifecycle.RunConflictError, 'appears to be starting'):
            lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()})
        old = time.time() - 3600
        os.utime(path, (old, old))
        lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()}).release()

    def test_two_concurrent_launchers_only_one_wins_the_task_lock(self):
        directory = self.root / 'locks'
        key = 'abcdabcdabcdabcd'
        barrier = threading.Barrier(2)
        winner_ready, loser_done = threading.Event(), threading.Event()
        outcomes, guard = [], threading.Lock()

        def worker(name):
            lock = lifecycle.RunLock(key, directory)
            barrier.wait()
            try:
                lock.acquire({'pid': os.getpid(), 'runId': name})
                with guard: outcomes.append('acquired')
                winner_ready.set()
                loser_done.wait(5)
                lock.release()
            except lifecycle.RunConflictError:
                with guard: outcomes.append('refused')
                loser_done.set()

        threads = [threading.Thread(target=worker, args=(str(index),)) for index in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertTrue(winner_ready.is_set())
        self.assertEqual(sorted(outcomes), ['acquired', 'refused'])

    def test_lock_release_allows_a_later_launch(self):
        directory = self.root / 'locks'
        key = 'fedcba9876543210'
        first = lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()})
        first.release()
        lifecycle.RunLock(key, directory).acquire({'pid': os.getpid()}).release()

    def test_task_key_ignores_result_directory_and_config_location(self):
        base = {'harness': 'codex', 'model': 'qwen3.6:35b', 'endpoint': 'http://127.0.0.1:11434',
                'rendererService': 'http://127.0.0.1:8765', 'volumePath': 'D:/v.dat',
                'task': 'rotate', 'contextSize': 16384}
        changed_directory = dict(base, resultDirectory='D:/a')
        other_task = dict(base, task='different')
        self.assertEqual(lifecycle.task_key(base), lifecycle.task_key(changed_directory))
        self.assertNotEqual(lifecycle.task_key(base), lifecycle.task_key(other_task))

    def test_a_legacy_run_directory_is_used_only_as_a_sibling_anchor(self):
        old = self.root / 'old-run'; old.mkdir()
        (old / 'manifest.json').write_text('{}')
        base, adjusted = lifecycle.resolve_run_base(old)
        self.assertTrue(adjusted)
        self.assertEqual(base, old.parent)
        fresh = self.root / 'normal-base'
        base, adjusted = lifecycle.resolve_run_base(fresh)
        self.assertFalse(adjusted)
        self.assertEqual(base, fresh)


class ConfigValidation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.volume = self.root / 'volume.dat'; self.volume.write_text('unit-test-header')

    def tearDown(self):
        self.temporary.cleanup()

    def config(self, **overrides):
        config = {'harness': 'codex', 'model': 'qwen3.6:35b', 'endpoint': 'http://127.0.0.1:11434',
                  'rendererService': 'http://127.0.0.1:8765', 'contextSize': 16384, 'timeoutSeconds': 300,
                  'taskTimeoutSeconds': 900, 'temperature': 0, 'maxOutputTokens': 1200,
                  'volumePath': str(self.volume), 'renderBudget': 4, 'task': 'unit task',
                  'resultDirectory': str(self.root / 'results')}
        config.update(overrides)
        return config

    def test_valid_config_passes(self):
        self.assertIsNone(lifecycle.validate_config(self.config()))

    def test_missing_keys_are_listed(self):
        broken = self.config(); del broken['model']; del broken['resultDirectory']
        with self.assertRaises(lifecycle.ConfigError) as raised:
            lifecycle.validate_config(broken)
        self.assertIn("'model'", str(raised.exception))
        self.assertIn("'resultDirectory'", str(raised.exception))

    def test_cloud_model_and_public_endpoint_are_rejected(self):
        with self.assertRaisesRegex(lifecycle.ConfigError, 'cloud'):
            lifecycle.validate_config(self.config(model='gpt-5:cloud'))
        with self.assertRaisesRegex(lifecycle.ConfigError, 'loopback'):
            lifecycle.validate_config(self.config(endpoint='https://api.example.com'))

    def test_missing_volume_is_rejected(self):
        with self.assertRaisesRegex(lifecycle.ConfigError, 'volumePath does not exist'):
            lifecycle.validate_config(self.config(volumePath=str(self.root / 'missing.dat')))

    def test_wrong_types_are_rejected(self):
        with self.assertRaises(lifecycle.ConfigError):
            lifecycle.validate_config(self.config(contextSize='big'))
        with self.assertRaises(lifecycle.ConfigError):
            lifecycle.validate_config(self.config(renderBudget=True))


class LauncherIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.volume = self.root / 'volume.dat'; self.volume.write_text('unit-test-header')
        self.lock_directory = self.root / 'locks'
        self.patch = patch.object(launcher, 'LOCK_DIRECTORY', self.lock_directory)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def config(self, **overrides):
        config = {'harness': 'codex', 'model': 'qwen3.6:35b', 'endpoint': 'http://127.0.0.1:11434',
                  'rendererService': 'http://127.0.0.1:8765', 'contextSize': 16384, 'timeoutSeconds': 300,
                  'taskTimeoutSeconds': 900, 'temperature': 0, 'maxOutputTokens': 1200,
                  'volumePath': str(self.volume), 'renderBudget': 4, 'task': 'unit task',
                  'resultDirectory': str(self.root / 'results')}
        config.update(overrides)
        return config

    def write_config(self, config, name='config.json'):
        path = self.root / name
        path.write_text(json.dumps(config, indent=2), encoding='utf-8')
        return path

    def run_main(self, config_path, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.main(['--config', str(config_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def run_directories(self):
        base = self.root / 'results'
        return sorted(path for path in base.iterdir() if path.is_dir()) if base.is_dir() else []

    # -- directory lifecycle -------------------------------------------------
    def test_first_run_creates_a_new_directory_with_provenance(self):
        config = self.config()
        config_path = self.write_config(config)
        raw = config_path.read_bytes()
        context = launcher.prepare_run(config, config_path, raw)
        try:
            self.assertTrue(context.directory.is_dir())
            self.assertEqual(context.directory.parent, Path(config['resultDirectory']))
            self.assertEqual(json.loads((context.directory / 'run.json').read_text())['status'], 'running')
            self.assertEqual(json.loads((context.directory / 'run.json').read_text())['mode'], 'new-experiment')
            self.assertEqual(json.loads((context.directory / 'run.json').read_text())['originalResultDirectory'],
                             config['resultDirectory'])
            self.assertEqual((context.directory / 'config.source.json').read_bytes(), raw)
            effective = json.loads((context.directory / 'config.json').read_text())
            self.assertEqual(effective['resultDirectory'], str(context.directory))
            self.assertEqual(Path((Path(config['resultDirectory']) / 'LATEST.txt').read_text()), context.directory)
            self.assertEqual(json.loads((context.directory / 'run.json').read_text())['configSha256'],
                             hashlib.sha256(raw).hexdigest())
        finally:
            context.lock.release()
        self.assertEqual(config_path.read_bytes(), raw)

    def test_second_run_of_the_same_config_gets_an_independent_directory(self):
        config = self.config()
        config_path = self.write_config(config)
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            first, out_first, _ = self.run_main(config_path)
            second, out_second, _ = self.run_main(config_path)
        self.assertEqual((first, second), (0, 0))
        directories = self.run_directories()
        self.assertEqual(len(directories), 2)
        run_ids = [json.loads((path / 'run.json').read_text())['runId'] for path in directories]
        self.assertEqual(len(set(run_ids)), 2)
        self.assertIn('new-experiment', out_first)
        self.assertIn('new-experiment', out_second)

    def test_old_evidence_is_unchanged_after_success_and_after_failure(self):
        config = self.config()
        config_path = self.write_config(config)
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            self.run_main(config_path)
        first = self.run_directories()[0]
        before = snapshot(first)
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            self.run_main(config_path)
        with patch.object(launcher, 'execute', side_effect=RuntimeError('simulated harness failure')):
            code, _, _ = self.run_main(config_path)
        self.assertEqual(code, 1)
        self.assertEqual(snapshot(first), before)
        self.assertEqual(len(self.run_directories()), 3)
        failed = next(path for path in self.run_directories() if (path / 'failure.json').exists())
        self.assertEqual(json.loads((failed / 'run.json').read_text())['status'], 'failed')
        self.assertTrue((failed / 'failure.json').is_file())
        self.assertFalse((first / 'failure.json').exists())

    def test_prefix_collision_selects_a_fresh_directory(self):
        config = self.config()
        config_path = self.write_config(config)
        base = Path(config['resultDirectory']); base.mkdir(parents=True)
        taken = base / 'run-collision'
        taken.mkdir(); (taken / 'evidence.bin').write_bytes(b'original-evidence')
        candidates = iter(['run-collision', lifecycle.make_run_id()])
        with patch.object(launcher, 'reserve_directory',
                          side_effect=lambda b, **k: lifecycle.reserve_directory(b, run_id_factory=lambda: next(candidates))), \
             patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            code, _, _ = self.run_main(config_path, argv=())
        self.assertEqual(code, 0)
        self.assertEqual((taken / 'evidence.bin').read_bytes(), b'original-evidence')
        self.assertEqual(len(self.run_directories()), 2)

    def test_legacy_run_directory_config_never_nests_inside_old_evidence(self):
        old = self.root / 'old-run'; old.mkdir()
        (old / 'manifest.json').write_text('{"state": "finished"}')
        (old / 'session.json').write_text('{"id": "old"}')
        config = self.config(resultDirectory=str(old))
        config_path = self.write_config(config)
        before = snapshot(old)
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            code, out, _ = self.run_main(config_path)
        self.assertEqual(code, 0)
        self.assertIn('run_base_adjusted', out)
        self.assertEqual(snapshot(old), before)          # old evidence untouched, nothing nested
        created = [path for path in old.parent.iterdir() if path.is_dir() and path.name.startswith('run-')]
        self.assertEqual(len(created), 1)
        record = json.loads((created[0] / 'run.json').read_text())
        self.assertTrue(record['resultBaseAdjusted'])
        self.assertEqual(record['configuredResultDirectory'], str(old))
        self.assertEqual(record['resultBase'], str(old.parent))

    # -- duplicate / concurrent launches ------------------------------------
    def test_a_live_duplicate_launch_is_refused_without_a_new_directory(self):
        config = self.config()
        config_path = self.write_config(config)
        context = launcher.prepare_run(config, config_path, config_path.read_bytes())
        try:
            code, _, err = self.run_main(config_path)
            self.assertEqual(code, 3)
            self.assertIn('duplicate-run', err)
            self.assertIn('already has a running launcher', err)
            self.assertEqual(len(self.run_directories()), 1)
        finally:
            context.lock.release()
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            code, _, _ = self.run_main(config_path)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.run_directories()), 2)

    def test_a_stale_lock_does_not_block_a_new_experiment(self):
        config = self.config()
        config_path = self.write_config(config)
        self.lock_directory.mkdir(parents=True, exist_ok=True)
        key = lifecycle.task_key(config)
        (self.lock_directory / (key + '.lock')).write_text(json.dumps({'pid': 99999999, 'runId': 'run-old'}))
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 0):
            code, _, _ = self.run_main(config_path)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.run_directories()), 1)

    def test_invalid_config_reports_clearly_and_creates_nothing(self):
        config = self.config(contextSize=0)
        config_path = self.write_config(config)
        before = snapshot(self.root)
        code, _, err = self.run_main(config_path)
        self.assertEqual(code, 2)
        self.assertIn('STARTUP FAILED (configuration)', err)
        self.assertIn('contextSize', err)
        self.assertNotIn('Traceback (most recent call last)', err)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / 'results').exists())

    def test_resource_conflict_keeps_evidence_and_clear_message(self):
        config = self.config()
        config_path = self.write_config(config)
        conflict = urllib.error.HTTPError('http://127.0.0.1:11434/api/version', 500, 'Renderer is busy', {},
                                          io.BytesIO(b'{"error": "Renderer is busy"}'))

        def failing_http(base, path, body=None, timeout=120):
            raise conflict

        with patch.object(launcher, 'http', side_effect=failing_http):
            code, _, err = self.run_main(config_path)
        self.assertEqual(code, 1)
        self.assertIn('Renderer is busy', err)
        self.assertNotIn('Traceback (most recent call last)', err)
        directories = self.run_directories()
        self.assertEqual(len(directories), 1)
        failure = json.loads((directories[0] / 'failure.json').read_text())
        self.assertEqual(failure['status'], 'failed')
        self.assertEqual(json.loads((directories[0] / 'run.json').read_text())['status'], 'failed')

    # -- exit codes / flags --------------------------------------------------
    def test_show_images_does_not_change_the_lifecycle(self):
        config = self.config()
        config_path = self.write_config(config)
        seen = []
        with patch.object(launcher, 'execute', side_effect=lambda ctx, show_images=False, open_images=False:
                          seen.append((show_images, open_images)) or 0):
            plain, _, _ = self.run_main(config_path)
            flagged, _, _ = self.run_main(config_path, argv=('--show-images', '--open-images'))
        self.assertEqual((plain, flagged), (0, 0))
        self.assertEqual(seen, [(False, False), (True, True)])
        self.assertEqual(len(self.run_directories()), 2)
        run_ids = [json.loads((path / 'run.json').read_text())['runId'] for path in self.run_directories()]
        self.assertEqual(len(set(run_ids)), 2)

    def test_harness_exit_code_and_timeout_are_propagated(self):
        self.assertEqual(launcher.exit_code_from_manifest({'exitCode': 0}), 0)
        self.assertEqual(launcher.exit_code_from_manifest({'exitCode': 7}), 7)
        self.assertEqual(launcher.exit_code_from_manifest({'exitCode': 1, 'timedOut': True}), 124)
        config = self.config()
        config_path = self.write_config(config)
        with patch.object(launcher, 'execute', side_effect=lambda *a, **k: 7):
            code, _, _ = self.run_main(config_path)
        self.assertEqual(code, 7)

    def test_failed_prepare_leaves_the_original_config_byte_identical(self):
        config = self.config()
        config_path = self.write_config(config)
        raw = config_path.read_bytes()
        with patch.object(launcher, 'reserve_directory', side_effect=lifecycle.ReservationError('base is not a directory')):
            code, _, err = self.run_main(config_path)
        self.assertEqual(code, 2)
        self.assertIn('reservation', err)
        self.assertEqual(config_path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
