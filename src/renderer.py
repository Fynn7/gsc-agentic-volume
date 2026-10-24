"""One complete SceneSpec -> native GSC -> screenshot and native state readback."""
import json
import os
import subprocess
import sys
import threading
import math
from pathlib import Path
from PIL import Image
import numpy as np
from volume import sha256_file
ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT/'VirtualSampling/Raycaster/VS/x64/Release/Raycaster.exe'
CWD = ROOT/'VirtualSampling/Raycaster/VS'


def _tui_subproc():
    """Shared headless helper (stdlib-only); None when unreachable."""
    try:
        sys.path.insert(0, str(ROOT / 'scripts' / 'volume_tui'))
        import subproc as helper
        return helper
    except Exception:
        return None


_SUBPROC = _tui_subproc()


def _headless() -> bool:
    """Hide Raycaster windows unless the TUI debug toggle says otherwise."""
    if _SUBPROC is not None:
        try:
            return not _SUBPROC.debug_windows()
        except Exception:
            pass
    return os.environ.get('GAV_DEBUG_WINDOWS', '').strip().lower() not in ('1', 'true', 'yes', 'on')


def _hide_process_windows(pid: int) -> None:
    """Hide visible top-level windows owned by `pid` (no C++ rebuild needed).

    The C++ side hides its GLFW window itself once rebuilt with
    GAV_RAYCASTER_HIDDEN support; until then this Win32 fallback keeps
    batch screenshots from flashing. No-op off Windows or on any error.
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        IsWindowVisible = user32.IsWindowVisible
        ShowWindow = user32.ShowWindow
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _visit(hwnd, _lparam):
            try:
                owner = wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if int(owner.value) == pid and IsWindowVisible(hwnd):
                    ShowWindow(hwnd, 0)  # SW_HIDE
            except Exception:
                pass
            return True

        try:
            EnumWindows = user32.EnumWindows
            EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
            EnumWindows.restype = wintypes.BOOL
            EnumWindows(_visit, 0)
        finally:
            _keepalive = _visit  # noqa: F841 - keep the callback alive during the call
    except Exception:
        pass


def _hide_soon(pid: int) -> None:
    """The GLFW window appears after GL init; retry hiding briefly."""
    import time as _time

    def _work():
        deadline = _time.time() + 5.0
        while _time.time() < deadline:
            _hide_process_windows(pid)
            _time.sleep(0.25)

    threading.Thread(target=_work, daemon=True).start()


def initial_scene(volume):
    return {'version': 1, 'revision': 0, 'volumeHash': volume['volumeHash'], 'rendererSha256': sha256_file(EXE), 'tf': {'type': 'smoothstep', 'start': 0.1, 'width': 0.12}, 'camera': {'rotationXYZ': [0, 0, 0], 'translation': [0, 0, 0], 'orthographic': True}, 'crop': [v for d in volume['dimensions'] for v in (0, d)], 'output': [512, 512], 'samplingRate': 1, 'alphaThreshold': 0.99, 'method': 0, 'level': 0, 'subdiv': 1, 'background': [0, 0, 0, 1], 'mask': None}


def numbers(values, n, low, high):
    if not isinstance(values, list) or len(values) != n or any(type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high for v in values):
        raise ValueError('Invalid bounded numeric vector')


def validate_scene(scene, volume):
    expected = initial_scene(volume)
    if set(scene) != set(expected):
        raise ValueError('Scene keys mismatch')
    if scene['volumeHash'] != volume['volumeHash'] or scene['rendererSha256'] != expected['rendererSha256']:
        raise ValueError('Volume or renderer binding mismatch')
    tf = scene['tf']
    cam = scene['camera']
    if set(tf) != {'type', 'start', 'width'} or tf['type'] != 'smoothstep':
        raise ValueError('Unsupported TF')
    numbers([tf['start']], 1, 0, 1)
    numbers([tf['width']], 1, 0.00001, 1)
    if set(cam) != {'rotationXYZ', 'translation', 'orthographic'} or cam['orthographic'] is not True:
        raise ValueError('Only orthographic supported')
    numbers(cam['rotationXYZ'], 3, -360, 360)
    numbers(cam['translation'], 3, -2, 2)
    numbers(scene['crop'], 6, 0, max(volume['dimensions']))
    for i, d in enumerate(volume['dimensions']):
        lo, hi = scene['crop'][2*i:2*i+2]
        if not 0 <= lo < hi <= d:
            raise ValueError('Crop must be half-open voxel bounds')
    if scene['output'] != [512, 512]:
        raise ValueError('Phase 1 output fixed at 512')
    for k in ['samplingRate', 'alphaThreshold', 'method', 'level', 'subdiv', 'background', 'mask', 'version']:
        if scene[k] != expected[k]:
            raise ValueError('Unsupported setting '+k)
    if type(scene['revision']) != int or scene['revision'] < 0:
        raise ValueError('Bad revision')


NATIVE_LOCK = threading.Lock()


def render(scene, volume, directory, stop=None):
    if not NATIVE_LOCK.acquire(blocking=False):
        raise ValueError('Native renderer is busy')
    try:
        return _render_unlocked(scene, volume, directory, stop)
    finally:
        NATIVE_LOCK.release()


def _render_unlocked(scene, volume, directory, stop=None):
    validate_scene(scene, volume)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    if sha256_file(Path(volume['rendererPath']).with_name('render.raw')) != volume['rendererPayloadHash']:
        raise ValueError('Renderer payload changed')

    def token(p):
        s = str(p).replace('\\', '/')
        if any(c.isspace() or c in '$\"\'' for c in s):
            raise ValueError('Native unquoted path unsupported')
        return s

    def vals(xs): return ' '.join(map(str, xs))
    script = ['setinteraction false', 'resize 512 512', f'setdir {token(directory)}', 'logfile markers.txt', f'loadvolume {token(volume["rendererPath"])}', 'log ASSET_LOADED', 'resetrotation', 'settranslation '+vals(
        scene['camera']['translation']), 'setuseortho true', 'setmethod 0', 'setrate 1', 'setLevel 0', 'setsubdiv 1', 'setbackground 0 0 0 1', 'setalphathreshold 0.99']
    script += [f'addrotation{axis} {value}' for axis,
               value in zip('xyz', scene['camera']['rotationXYZ'])]
    script += [f'settfparams false {scene["tf"]["start"]} {scene["tf"]["width"]}', 'setclipbounds '+vals(
        scene['crop']), f'dumpstate {token(directory/"native-state.json")}', 'screenshot image.png', 'log COMPLETE', 'quit']
    (directory/'scene.json').write_text(json.dumps(scene, indent=2))
    (directory/'script.gsc').write_text('\n'.join(script)+'\n')
    env = dict(os.environ)
    if _headless():
        # Honoured by the C++ renderer once rebuilt (hidden GLFW window).
        env['GAV_RAYCASTER_HIDDEN'] = '1'
    if _SUBPROC is not None:
        popen_kw = _SUBPROC.with_kwargs({'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)})
    elif os.name == 'nt':
        popen_kw = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    else:
        popen_kw = {}
    p = subprocess.Popen([str(EXE), '--script', str(directory/'script.gsc')], cwd=CWD,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **popen_kw)
    if _headless():
        _hide_process_windows(p.pid)
        _hide_soon(p.pid)
    timedout = threading.Event()

    def kill():
        if p.poll() is None:
            kill_kw = _SUBPROC.kwargs() if _SUBPROC is not None else (
                {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if os.name == 'nt' else {})
            subprocess.run(['taskkill', '/pid', str(p.pid), '/T', '/F'],
                           capture_output=True, **kill_kw)
    timer = threading.Timer(60, lambda: (timedout.set(), kill()))
    timer.start()

    def watch():
        while p.poll() is None:
            if stop and stop.wait(.1):
                kill()
                return
            if not stop:
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        stdout, stderr = p.communicate()
    finally:
        timer.cancel()
    report = {'exitCode': p.returncode, 'timedOut': timedout.is_set(), 'stdout': stdout.decode(
        errors='replace'), 'stderr': stderr.decode(errors='replace')}
    (directory/'process.json').write_text(json.dumps(report, indent=2))
    if stop and stop.is_set():
        raise InterruptedError('Stopped')
    if p.returncode or timedout.is_set() or any(t in (report['stdout']+report['stderr']).lower() for t in ['scripting error:', 'invalid arguments', 'unknown command', 'loadvolume failed']):
        raise RuntimeError('Native execution failed; see process.json')
    markers = (directory/'markers.txt').read_text()
    if 'ASSET_LOADED' not in markers or 'COMPLETE' not in markers:
        raise RuntimeError('Native markers missing')
    state = json.loads((directory/'native-state.json').read_text())
    for actual, expected_value in [(state['tfStart'], scene['tf']['start']), (state['tfWidth'], scene['tf']['width'])]:
        if not math.isclose(actual, expected_value, abs_tol=1e-5):
            raise RuntimeError('Native TF readback mismatch')
    if state['dimensions'] != list(volume['dimensions']):
        raise RuntimeError('Native volume dimensions mismatch')
    if not np.allclose(state['crop'], scene['crop'], atol=1e-3):
        raise RuntimeError('Native crop readback mismatch')
    if not np.allclose(state['translation'], scene['camera']['translation'], atol=1e-5):
        raise RuntimeError('Native translation mismatch')
    # Commands pre-multiply X, Y, Z. Match native row-major rotation matrix.
    x, y, z = -np.deg2rad(scene['camera']['rotationXYZ'])
    cx, sx = np.cos(x), np.sin(x)
    cy, sy = np.cos(y), np.sin(y)
    cz, sz = np.cos(z), np.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    if not np.allclose(np.array(state['rotation']).reshape(4, 4)[:3, :3], rz@ry@rx, atol=1e-5):
        raise RuntimeError('Native rotation mismatch')
    with Image.open(directory/'image.png') as im:
        im.load()
        pixels = np.asarray(im.convert('RGB'))
        size = list(im.size)
    if size != scene['output']:
        raise RuntimeError('Actual output dimensions mismatch')
    result = {'version': 1, 'scene': scene, 'nativeState': state, 'image': str(directory/'image.png'), 'imageSha256': sha256_file(
        directory/'image.png'), 'nonblackPixels': int(np.count_nonzero(np.any(pixels > 0, axis=2))), 'taskSuccess': 'unverified'}
    (directory/'result.json').write_text(json.dumps(result, indent=2))
    return result
