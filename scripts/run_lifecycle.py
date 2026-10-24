"""Run-directory lifecycle for one CLI experiment.

Every launcher invocation is a NEW experiment. The configured `resultDirectory`
is treated as a run base: each launch atomically reserves a fresh, unique child
directory inside it. Existing experiment directories are never reused, renamed,
deleted, cleared or written into, and a second launch of the same logical task is
refused while the first launcher is still running.

This module contains no model, renderer or harness logic. It only manages paths,
provenance and the cross-process run lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(Exception):
    """Invalid or unusable launcher configuration (detected before any side effect)."""


class RunConflictError(Exception):
    """The same logical task already has a live launcher instance."""


class ReservationError(Exception):
    """A unique, safe run directory could not be reserved."""


# Fields that identify the logical experiment. `resultDirectory`, `showImages` and
# unrelated transport options are deliberately excluded so that the same task via
# two different config files still collides (duplicate prevention) while a repeat
# launch after completion is allowed.
TASK_FIELDS = ('harness', 'model', 'endpoint', 'rendererService', 'volumePath', 'task', 'contextSize')


def make_run_id(now=None, token=None):
    """Unique run id: a timestamp for readability plus a random token.

    The token is not derived from the clock, so two launches in the same second
    still cannot share an id.
    """
    moment = time.localtime(time.time() if now is None else now)
    stamp = time.strftime('%Y%m%d-%H%M%S', moment)
    token = secrets.token_hex(3) if token is None else token
    return 'run-%s-%s' % (stamp, token)


EVIDENCE_MARKERS = ('run.json', 'manifest.json', 'harness.jsonl', 'session.json')


def resolve_run_base(configured):
    """Return (base, adjusted) for a configured `resultDirectory`.

    A configured path that is itself an existing run directory (it already holds
    experiment evidence) is never written into. In that legacy case the parent is
    used as the base, so the new run lands next to the old evidence instead of
    nesting inside it.
    """
    configured = Path(configured)
    if configured.is_dir() and any((configured / marker).exists() for marker in EVIDENCE_MARKERS):
        return configured.parent, True
    return configured, False


def reserve_directory(base, run_id_factory=None, attempts=64):
    """Atomically create a fresh unique child of `base` and return (path, run_id).

    `mkdir(exist_ok=False)` is the reservation primitive: it is atomic, so two
    concurrent launchers can never be handed the same directory. A timestamp is
    only one input; a name collision simply retries with a different id. An
    existing path is never touched.
    """
    base = Path(base)
    if base.exists() and not base.is_dir():
        raise ReservationError('Run base is not a directory: %s' % base)
    factory = run_id_factory or make_run_id
    for _ in range(max(1, attempts)):
        run_id = factory()
        candidate = base / run_id
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        except OSError as error:
            raise ReservationError('Cannot create run directory %s: %s' % (candidate, error)) from error
        return candidate, run_id
    raise ReservationError('Could not reserve a unique run directory under %s after %d attempts' % (base, attempts))


def process_alive(pid):
    """Return True only when `pid` is a currently live process."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def task_key(config):
    """Stable key for the logical experiment, independent of the config file path."""
    logical = {field: config.get(field) for field in TASK_FIELDS}
    encoded = json.dumps(logical, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


class RunLock:
    """Cross-process guard allowing only one live launcher per logical task.

    It refuses a duplicate launch with a clear diagnostic and never terminates
    another process. A lock whose owner process is gone is reclaimed.
    """

    def __init__(self, key, directory):
        self.directory = Path(directory)
        self.path = self.directory / (key + '.lock')
        self._held = False

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            return None

    def _recent(self, window=300.0):
        try:
            return (time.time() - self.path.stat().st_mtime) < window
        except OSError:
            return False

    def _conflict(self, existing, alive):
        owners = ', '.join('%s=%s' % (field, pid) for field, pid in alive)
        phase = existing.get('phase')
        return ('The same logical task already has a running launcher '
                '(alive: %s%s; run %s, directory %s, started %s). Refusing a duplicate '
                'run; no process was terminated and no evidence was changed. '
                'Wait for it to finish or inspect the lock file: %s' % (
                    owners, (', phase=' + str(phase)) if phase else '',
                    existing.get('runId'), existing.get('directory'),
                    existing.get('startedAt'), self.path))

    def acquire(self, payload):
        if self._held:
            raise RunConflictError('Run lock is already held by this process: %s' % self.path)
        self.directory.mkdir(parents=True, exist_ok=True)
        for _ in range(16):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                existing = self._read()
                if isinstance(existing, dict):
                    alive = [(field, existing.get(field)) for field in ('pid', 'harnessPid')
                             if existing.get(field) and process_alive(existing.get(field))]
                    if alive:
                        raise RunConflictError(self._conflict(existing, alive))
                if existing is None and self._recent():
                    raise RunConflictError('Another launcher appears to be starting (unreadable lock %s)' % self.path)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise RunConflictError('Stale lock %s cannot be cleared: %s' % (self.path, error)) from error
                continue
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2)
            self._held = True
            return self
        raise RunConflictError('Run lock %s could not be acquired safely' % self.path)

    def update(self, **fields):
        if not self._held:
            return
        payload = self._read()
        if not isinstance(payload, dict):
            payload = {}
        payload.update(fields)
        temporary = self.path.with_suffix('.lock.tmp')
        temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        os.replace(str(temporary), str(self.path))

    def release(self):
        if not self._held:
            return
        self._held = False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def validate_config(config):
    """Raise ConfigError with an explicit list of problems; no side effects."""
    if not isinstance(config, dict):
        raise ConfigError('Configuration must be a JSON object')
    problems = []

    def text(key, required=True):
        value = config.get(key)
        if value is None:
            if required:
                problems.append('missing required string %r' % key)
            return None
        if not isinstance(value, str) or not value.strip():
            problems.append('%r must be a non-empty string' % key)
            return None
        return value

    def number(key, required=True, minimum=None):
        value = config.get(key)
        if value is None:
            if required:
                problems.append('missing required number %r' % key)
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append('%r must be a finite number' % key)
            return None
        if minimum is not None and value < minimum:
            problems.append('%r must be >= %s' % (key, minimum))
            return None
        return value

    if config.get('harness') != 'codex':
        problems.append("'harness' must be 'codex' (only the validated route is implemented)")

    model = text('model')
    if model and ':cloud' in model:
        problems.append('cloud models are prohibited; %r is not an installed local model' % model)

    for key in ('endpoint', 'rendererService'):
        value = text(key)
        if value:
            try:
                host = urlsplit(value).hostname
            except ValueError:
                host = None
            if host not in ('127.0.0.1', 'localhost'):
                problems.append('%r must be a loopback URL, got %r' % (key, value))

    volume = text('volumePath')
    if volume and not Path(volume).is_file():
        problems.append('volumePath does not exist: %s' % volume)

    text('task')
    text('resultDirectory')
    number('contextSize', minimum=1)
    # No timeouts and no output-token cap: these keys are optional legacy
    # input and are never enforced by the launcher.
    number('timeoutSeconds', required=False, minimum=1)
    number('taskTimeoutSeconds', required=False, minimum=1)
    number('temperature', required=False)
    number('maxOutputTokens', required=False)
    budget = number('renderBudget', minimum=1)
    if isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 30:
        problems.append("'renderBudget' must be between 1 and 30")

    if problems:
        raise ConfigError('Invalid configuration:\n  - ' + '\n  - '.join(problems))
