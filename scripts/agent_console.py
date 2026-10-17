"""Readable live console trace for the local VLM run.

The launcher already writes every original artifact. This module only *reads*
the evidence those artifacts contain and prints a compact chronological view:
model turns and HTTP inference calls, renderer MCP tool calls with their
arguments, native render results (scene revision and the real PNG path), tool
errors, the final selection and the total elapsed time.

Guarantees:
- It reads `harness.jsonl`, `runtime/<session-id>/events.jsonl` and the lossless
  `http/*.metadata.json` recorder files. Nothing is written to a run directory.
- It never prints base64 image data or a full raw request body.
- It never fabricates progress or image-viewer state: a line is printed only
  when the corresponding artifact proves it.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_INDEX = re.compile(r'http-(\d+)')

# One colour per message category. Used only when the terminal is interactive.
RESET = '\x1b[0m'
_COLORS = {
    'time': '90',        # dim grey timestamp
    'harness': '90',     # grey harness/thread plumbing
    'model': '36',       # cyan model turns and text
    'infer': '35',       # magenta inference HTTP calls
    'tool': '34',        # blue renderer MCP tool calls
    'image': '32',       # green real PNG artifact
    'finish': '32;1',    # bold green final selection
    'final': '32;1',     # bold green saved final image
    'warn': '33',        # yellow harness warnings
    'error': '31;1',     # bold red failures
    'summary': '37;1',   # bold white report
    'muted': '90',       # dim grey secondary lines
}


def _enable_vt(stream):
    """Turn on ANSI processing for the Windows console; harmless elsewhere."""
    if os.name != 'nt':
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _brief(value, limit=220):
    """Compact single-line JSON for tool arguments, capped without printing raw payloads."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        except (TypeError, ValueError):
            text = str(value)
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + '...'


def _thumbnail(path, width):
    """ANSI truecolor half-block preview. Returns None when Pillow is unavailable."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(path) as image:
            rgb = image.convert('RGB')
            rows = max(2, int(width * (rgb.height / max(rgb.width, 1))) // 2 * 2)
            small = rgb.resize((width, rows))
    except Exception:
        return None
    pixels = small.load()
    lines = []
    for y in range(0, rows, 2):
        cells = []
        for x in range(width):
            top = pixels[x, y]
            bottom = pixels[x, y + 1] if y + 1 < rows else top
            cells.append('\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm\u2580' % (bottom[0], bottom[1], bottom[2], top[0], top[1], top[2]))
        lines.append(''.join(cells) + '\x1b[0m')
    return '\n'.join(lines)


class ConsoleTrace:
    def __init__(self, result_directory, session_directory=None, show_images=False,
                 open_images=False, stream=None, poll=0.4, thumb_width=48, delay=0.0, color=None):
        self.result_directory = Path(result_directory)
        self.session_directory = Path(session_directory) if session_directory else None
        self.stream = stream if stream is not None else sys.stdout
        self.show_images = show_images
        self.open_images = open_images
        self.thumb_width = thumb_width
        self.poll = poll
        self.delay = delay
        self.t0 = time.monotonic()
        self.lines_emitted = 0
        self._stop = threading.Event()
        self._thread = None
        self._files = {}
        self._seen_request = set()
        self._seen_metadata = set()
        self._opened = set()
        self._turn = 0
        self.images = 0
        self.tool_calls = 0
        self.http_calls = 0
        self._tty = False
        try:
            self._tty = bool(self.stream.isatty())
        except Exception:
            self._tty = False
        if color is None:
            color = self._tty and not os.environ.get('NO_COLOR') and _enable_vt(self.stream)
        self.color = bool(color)

    # ---- output helpers -------------------------------------------------
    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except UnicodeEncodeError:
            try:
                self.stream.write(text.encode('ascii', 'replace').decode('ascii'))
                self.stream.flush()
            except Exception:
                pass
        except Exception:
            pass

    def _stamp(self):
        elapsed = max(0.0, time.monotonic() - self.t0)
        return '[+%02d:%04.1f]' % (int(elapsed // 60), elapsed % 60)

    def _paint(self, text, level):
        if not (self.color and level):
            return text
        code = _COLORS.get(level)
        return '%s%s%s' % ('\x1b[' + code + 'm', text, RESET) if code else text

    def _emit(self, message='', level=None):
        if not message:
            self._write('\n')
            return
        stamp = self._paint(self._stamp(), 'time')
        self._write(stamp + ' ' + self._paint(message, level) + '\n')
        self.lines_emitted += 1

    def _emit_block(self, text):
        self._write(text + '\n')
        self.lines_emitted += 1

    # ---- incremental file reading --------------------------------------
    def _lines(self, key, path):
        state = self._files.setdefault(key, {'path': None, 'offset': 0, 'buffer': ''})
        if state['path'] != str(path):
            state.update(path=str(path), offset=0, buffer='')
        if not path.exists():
            return
        try:
            if path.stat().st_size < state['offset']:
                state.update(offset=0, buffer='')
            with path.open('r', encoding='utf-8', errors='replace') as handle:
                handle.seek(state['offset'])
                data = handle.read()
                state['offset'] = handle.tell()
        except OSError:
            return
        if not data:
            return
        text = state['buffer'] + data
        parts = text.split('\n')
        state['buffer'] = parts.pop()
        for line in parts:
            line = line.strip()
            if line:
                yield line

    # ---- live tracing ---------------------------------------------------
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self._stop.wait(self.poll)

    def poll_once(self):
        self._poll_harness()
        self._poll_events()
        self._poll_http()

    def _poll_harness(self):
        path = self.result_directory / 'harness.jsonl'
        for line in self._lines('harness', path):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            self._harness(record)

    def _harness(self, record):
        kind = record.get('type')
        if kind == 'thread.started':
            self._emit('HARNESS thread %s' % record.get('thread_id'), 'harness')
        elif kind == 'turn.started':
            self._turn += 1
            self._emit('MODEL   turn %d started' % self._turn, 'model')
        elif kind == 'turn.completed':
            usage = record.get('usage') or {}
            self._emit('MODEL   turn %d done  input=%s cached=%s output=%s' % (
                self._turn, usage.get('input_tokens'), usage.get('cached_input_tokens'), usage.get('output_tokens')), 'model')
        elif kind == 'item.completed':
            item = record.get('item') or {}
            item_type = item.get('type')
            if item_type == 'agent_message':
                self._emit('MODEL   %s' % _brief(item.get('text', ''), 500), 'model')
            elif item_type == 'error':
                self._emit('WARN    %s' % _brief(item.get('message', ''), 320), 'warn')
            elif item_type == 'mcp_tool_call' and self.session_directory is None:
                # No service event log available: fall back to the harness view.
                self._emit('TOOL    <- %s.%s %s' % (item.get('server'), item.get('tool'), _brief(item.get('status'))), 'tool')
        elif kind == 'item.started':
            item = record.get('item') or {}
            if item.get('type') == 'mcp_tool_call' and self.session_directory is None:
                self.tool_calls += 1
                self._emit('TOOL    -> %s.%s %s' % (item.get('server'), item.get('tool'), _brief(item.get('arguments', {}), 240)), 'tool')

    def _poll_events(self):
        if self.session_directory is None:
            return
        for line in self._lines('events', self.session_directory / 'events.jsonl'):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            self._event(record)

    def _event(self, record):
        kind = record.get('kind')
        if kind in ('initial_render', 'render', 'slice'):
            self._render_event(record)
        elif kind == 'external_tool_call':
            self.tool_calls += 1
            self._emit('TOOL    -> %s %s' % (record.get('name'), _brief(record.get('arguments', {}), 240)), 'tool')
        elif kind == 'external_tool_return':
            try:
                seconds = ' %.2fs' % float(record['seconds'])
            except (KeyError, TypeError, ValueError):
                seconds = ''
            self._emit('TOOL    <- %s%s' % (record.get('name'), seconds), 'tool')
        elif kind == 'tool_error':
            self._emit('TOOL    !! %s: %s' % (record.get('name'), _brief(record.get('message', ''), 300)), 'error')
        elif kind == 'finish':
            self._emit('FINISH  selected revision %s  verification=%s' % (record.get('selectedRevision'), record.get('externalTaskVerification')), 'finish')
            self._emit('        claim: %s' % _brief(record.get('modelClaim', ''), 400), 'muted')
            self._emit_final()
        elif kind == 'error':
            self._emit('ERROR   %s' % _brief(record.get('message', ''), 300), 'error')
        elif kind == 'model_action':
            action = record.get('action') or {}
            self._emit('MODEL   decision tool=%s reason=%s' % (action.get('tool'), _brief(action.get('reason', ''), 240)), 'model')

    def _render_event(self, record):
        result = record.get('result') or {}
        image = result.get('image')
        scene = result.get('scene') or {}
        revision = scene.get('revision')
        nonblack = result.get('nonblackPixels')
        if record.get('kind') == 'initial_render':
            label = 'native frame-0 (initial, outside budget) rev=%s nonblack=%s' % (revision, nonblack)
        elif record.get('kind') == 'render':
            camera = (scene.get('camera') or {}).get('rotationXYZ')
            tf = scene.get('tf') or {}
            label = 'native frame rev=%s nonblack=%s rot=%s tf=%s/%s' % (revision, nonblack, camera, tf.get('start'), tf.get('width'))
        else:
            label = 'voxel slice axis=%s index=%s' % (result.get('axis'), result.get('index'))
        self._image(image, label)

    def _image(self, path, label):
        if not path:
            self._emit('IMAGE   %s (no image path recorded)' % label, 'warn')
            return
        self.images += 1
        self._emit('IMAGE   %s' % label, 'image')
        self._emit('        %s' % path, 'image')
        resolved = Path(path)
        if self.show_images and self._tty and resolved.exists():
            preview = _thumbnail(resolved, self.thumb_width)
            if preview:
                self._emit_block(preview)
            else:
                self._emit('        (thumbnail unavailable; original PNG kept)', 'muted')
        if self.open_images:
            self._open(resolved)

    def _emit_final(self):
        if self.session_directory is None:
            return
        final = self.session_directory / 'final' / 'image.png'
        if final.exists():
            resolved = final.resolve()
            self._emit('FINAL   %s' % resolved, 'final')
            if self.open_images:
                self._open(resolved)

    def _open(self, path):
        if not path.exists() or str(path) in self._opened:
            return
        self._opened.add(str(path))
        if not hasattr(os, 'startfile'):
            self._emit('        (image viewer launch is Windows-only)', 'muted')
            return
        try:
            os.startfile(str(path))
            self._emit('        opened in the default image viewer', 'muted')
        except Exception as error:
            self._emit('        (viewer did not open: %s)' % error, 'muted')

    def _poll_http(self):
        directory = self.result_directory / 'http'
        if not directory.is_dir():
            return
        for path in sorted(directory.glob('http-*.request.bin'), key=self._http_index):
            if path.name in self._seen_request:
                continue
            self._seen_request.add(path.name)
            self._emit('INFER   call #%s request sent, waiting for the model' % self._http_index(path), 'infer')
        for path in sorted(directory.glob('http-*.metadata.json'), key=self._http_index):
            if path.name in self._seen_metadata:
                continue
            self._seen_metadata.add(path.name)
            try:
                meta = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            self.http_calls += 1
            status = meta.get('status')
            path_name = meta.get('path')
            seconds = meta.get('seconds')
            if meta.get('error') or (isinstance(status, int) and status >= 400):
                self._emit('INFER   call #%s FAILED %s %s status=%s%s' % (
                    meta.get('index'), meta.get('method'), path_name, status,
                    (' %s' % _brief(meta.get('error'), 160)) if meta.get('error') else ''), 'error')
            else:
                self._emit('INFER   call #%s %s %s -> %s in %ss' % (
                    meta.get('index'), meta.get('method'), path_name, status,
                    ('%.2f' % seconds) if seconds is not None else '?'), 'infer')

    @staticmethod
    def _http_index(path):
        match = _INDEX.search(path.name)
        return int(match.group(1)) if match else 0

    # ---- final summary --------------------------------------------------
    def summary(self, manifest=None):
        manifest = manifest or {}

        def value(key):
            item = manifest.get(key)
            return 'unknown' if item is None else item

        def duration(key):
            try:
                return '%.1fs' % float(manifest[key])
            except (KeyError, TypeError, ValueError):
                return 'unknown'

        self._emit()
        self._emit('SUMMARY', 'summary')
        self._emit('        run            %s' % value('runId'), 'summary')
        self._emit('        session        %s' % value('sessionId'), 'summary')
        self._emit('        state          %s' % value('state'), 'summary')
        self._emit('        harness        %s (version %s)' % (value('harness'), value('harnessVersion')), 'summary')
        self._emit('        exit code      %s%s' % (value('exitCode'), ' (timed out)' if manifest.get('timedOut') else ''), 'summary')
        self._emit('        native PNGs    %d' % self.images, 'summary')
        self._emit('        MCP tool calls %d' % self.tool_calls, 'summary')
        self._emit('        inference calls %d' % self.http_calls, 'summary')
        self._emit('        harness time   %s' % duration('seconds'), 'summary')
        self._emit('        total elapsed  %s' % duration('totalSeconds'), 'summary')
        self._emit('        manifest       %s' % (self.result_directory / 'manifest.json'), 'summary')


def _resolve_session_directory(directory):
    session_file = directory / 'session.json'
    if not session_file.exists():
        return None
    try:
        session_id = json.loads(session_file.read_text(encoding='utf-8')).get('id')
    except (OSError, ValueError):
        return None
    if not session_id:
        return None
    candidate = ROOT / 'runtime' / session_id
    return candidate if candidate.is_dir() else None


def _recorded_manifest(directory):
    """Best-effort end-of-run summary for replay from the recorded artifacts."""
    summary = {}
    manifest = directory / 'manifest.json'
    if manifest.exists():
        try:
            summary = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            summary = {}
    session = directory / 'session.json'
    if session.exists():
        try:
            state = json.loads(session.read_text(encoding='utf-8'))
            summary.setdefault('sessionId', state.get('id'))
            summary.setdefault('state', state.get('state'))
        except (OSError, ValueError):
            pass
    return summary


def main():
    parser = argparse.ArgumentParser(description='Replay or follow a recorded run as a readable console trace.')
    parser.add_argument('run', help='result directory (must contain harness.jsonl and/or session.json)')
    parser.add_argument('--session-directory', help='override the runtime/<session-id> directory')
    parser.add_argument('--show-images', action='store_true', help='print a terminal thumbnail for each real PNG')
    parser.add_argument('--open-images', action='store_true', help='open each real PNG in the system image viewer')
    parser.add_argument('--follow', action='store_true', help='keep tailing new lines instead of stopping at the current end')
    parser.add_argument('--delay', type=float, default=0.0, help='seconds to wait between polls while replaying')
    parser.add_argument('--poll', type=float, default=0.4, help='live polling interval in seconds')
    parser.add_argument('--thumb-width', type=int, default=48)
    parser.add_argument('--color', dest='color', action='store_true', default=None, help='force ANSI colours even when not a TTY')
    parser.add_argument('--no-color', dest='color', action='store_false', help='disable ANSI colours')
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    directory = Path(args.run).resolve()
    session_directory = Path(args.session_directory).resolve() if args.session_directory else _resolve_session_directory(directory)
    trace = ConsoleTrace(directory, session_directory, show_images=args.show_images,
                         open_images=args.open_images, poll=args.poll, thumb_width=args.thumb_width,
                         color=args.color)
    if session_directory is not None:
        print('%sTRACE   session %s -> %s%s' % (
            ('\x1b[' + _COLORS['summary'] + 'm') if trace.color else '',
            session_directory.name, session_directory, RESET if trace.color else ''))

    if args.follow:
        trace.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        previous = -1
        while True:
            try:
                trace.poll_once()
            except Exception:
                break
            if trace.lines_emitted == previous:
                break
            previous = trace.lines_emitted
            if args.delay:
                time.sleep(args.delay)
    trace.stop()
    trace.summary(_recorded_manifest(directory))


if __name__ == '__main__':
    main()
