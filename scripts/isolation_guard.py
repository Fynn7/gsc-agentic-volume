"""Passive file baseline guard shared by the private recorder and renderer MCP."""
import hashlib
import json
import time
from pathlib import Path


def require_unchanged(run):
    run=Path(run);baseline=run/'isolation-baseline.json';alerts=run/'isolation-alerts.jsonl'
    if not baseline.exists():return # Legacy/interface runs have no Desktop baseline.
    if alerts.exists():raise ValueError('Isolation alert is latched; stop the experiment and inspect isolation-alerts.jsonl. No daily file was restored.')
    files=json.loads(baseline.read_text())['files'];changed=[]
    for name,expected in files.items():
        path=Path(name);actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual!=expected:changed.append(name)
    if changed:
        with alerts.open('a',encoding='utf-8') as output:output.write(json.dumps({'time':time.time(),'changedFiles':changed,'action':'block subsequent inference/tool requests; no restore or process termination'})+'\n')
        raise ValueError('Protected daily files changed. Experiment inference/tools blocked; inspect isolation-alerts.jsonl.')
