"""Shared eval constants and small helpers. Stdlib only.

No model, renderer or harness logic lives here: this module only names the
frozen prompt files, resolves dataset paths and parses the final claim.
"""
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'eval'

# Frozen task table (eval/CONFIG.md §8). Volume paths are repo-relative.
TASKS = {
    'T1': {'volume': 'volumes/foot.dat', 'kind': 'solvable', 'split': 'dev'},
    'T2': {'volume': 'volumes/foot.dat', 'kind': 'solvable', 'split': 'held-out'},
    'T3': {'volume': 'volumes/skull.dat', 'kind': 'solvable', 'split': 'held-out'},
    'T4': {'volume': 'volumes/aneurism_256x256x256_uint8.dat', 'kind': 'solvable', 'split': 'dev'},
    'T5': {'volume': 'volumes/foot.dat', 'kind': 'unsolvable', 'split': 'held-out'},
    'T6': {'volume': 'volumes/skull.dat', 'kind': 'unsolvable', 'split': 'held-out'},
    'T7': {'volume': 'volumes/present246x246x221.dat', 'kind': 'unsolvable', 'split': 'held-out'},
}

CLAIM_STATUSES = ('DONE', 'PARTIAL', 'CANNOT')


def task_info(task):
    key = str(task).upper()
    if key not in TASKS:
        raise ValueError('Unknown task %r (expected one of %s)' % (task, ', '.join(sorted(TASKS))))
    return key, TASKS[key]


def prompt(task):
    key, _ = task_info(task)
    path = EVAL / 'tasks' / key / 'prompt.txt'
    text = path.read_text(encoding='utf-8').strip()
    if not text:
        raise ValueError('Empty prompt file: %s' % path)
    return text


def instruction():
    path = EVAL / 'HARNESS_INSTRUCTION.txt'
    text = path.read_text(encoding='utf-8').strip()
    if not text:
        raise ValueError('Empty harness instruction: %s' % path)
    return text


def compose_task(task):
    """Exactly what the launcher will append after its own fixed prefix."""
    return instruction() + '\n\n' + prompt(task)


def volume_path(task):
    key, info = task_info(task)
    path = ROOT / info['volume']
    if not path.is_file():
        raise FileNotFoundError('%s volume is missing: %s' % (key, path))
    return path


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def http_json(base, path, body=None, timeout=60):
    raw = None if body is None else json.dumps(body, allow_nan=False).encode('utf-8')
    request = urllib.request.Request(base.rstrip('/') + path, data=raw,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def git_sha():
    try:
        import subprocess
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT),
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


_STATUS = re.compile(r'^[\s>*_`#-]*STATUS:\s*[*_`]*\s*(DONE|PARTIAL|CANNOT)\b', re.I)
_REASON = re.compile(r'^[\s>*_`#-]*REASON:\s*(.*)$', re.I)
_IMAGE = re.compile(r'^[\s>*_`#-]*IMAGE:\s*(.*)$', re.I)


def _clean(value):
    return value.strip().strip('*_`').strip()


def parse_claim(message):
    """Parse the last STATUS/REASON/IMAGE block; NONE when the format is missing."""
    text = (message or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    status_index = None
    status = 'NONE'
    for index in range(len(lines) - 1, -1, -1):
        match = _STATUS.match(lines[index])
        if match:
            status = match.group(1).upper()
            status_index = index
            break
    reason = ''
    image = ''
    if status_index is not None:
        tail = lines[status_index + 1:]
        reason_lines = []
        for line in tail:
            reason_match = _REASON.match(line)
            if reason_match and not reason_lines:
                reason_lines.append(_clean(reason_match.group(1)))
                continue
            image_match = _IMAGE.match(line)
            if image_match:
                image = _clean(image_match.group(1))
                break
            if reason_lines:
                reason_lines.append(_clean(line))
        reason = ' '.join(part for part in reason_lines if part).strip()
    return {'status': status, 'reason': reason, 'image': image,
            'basis': 'explicit' if status_index is not None else 'missing'}


def last_agent_message(harness_path):
    text = ''
    try:
        with open(harness_path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get('type') != 'item.completed':
                    continue
                item = record.get('item') or {}
                if item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                    text = item['text']
    except OSError:
        return ''
    return text


def read_events(session_directory):
    path = Path(session_directory) / 'events.jsonl'
    events = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return events


def relative(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except Exception:
        return str(path)
