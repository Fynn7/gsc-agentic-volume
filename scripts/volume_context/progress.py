"""Optional, bounded stderr progress; never changes the result or numerical method."""
import json
import os
import sys
import time

_last = {}

def report(phase, label, completed=0, total=None, unit="", force=False):
    if os.environ.get("VOLUME_CONTEXT_PROGRESS") != "1":
        return
    now = time.monotonic()
    key = (phase, label)
    if not force and completed != total and now - _last.get(key, 0) < 0.1:
        return
    _last[key] = now
    event = {"phase": phase, "label": label, "completed": int(completed), "total": total, "unit": unit}
    print("@@volume-progress " + json.dumps(event, allow_nan=False), file=sys.stderr, flush=True)

def counted(chunks, phase, label, total, item_bytes):
    completed = 0
    report(phase, label, 0, total, "voxels", True)
    for chunk in chunks:
        yield chunk
        completed += chunk.nbytes // item_bytes
        report(phase, label, completed, total, "voxels")
