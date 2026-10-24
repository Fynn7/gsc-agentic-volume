"""External-agent operations on the application's existing Session and renderer.

No model calls, agent decisions, alternative scene store, or rendering logic.
"""
import copy
import hashlib
import json
import shutil
import threading
import time
from pathlib import Path

from renderer import initial_scene, render, validate_scene
from volume import inspect_slice, prepare


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


VECTOR3 = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3}
TOOLS = [
    {'name': 'inspect_scene', 'description': 'Read current actual scene, numeric volume facts and its real native PNG. Does not render or mutate.', 'inputSchema': obj({})},
    {'name': 'render_scene', 'description': 'Apply a sparse parameter patch to the current scene, execute Native Raycaster, verify native readback, and return the NEW actual PNG. Omitted parameters stay unchanged. Inspect the returned image before selecting another action. Camera Euler degrees apply X then Y then Z. Anatomical orientation is unknown. Crop is half-open storage voxel bounds, NOT normalized. TF smoothstep: values below start are transparent; above start+width are bright/opaque.', 'inputSchema': obj({
        'expectedRevision': {'type': 'integer', 'minimum': 0},
        'reason': {'type': 'string', 'maxLength': 3000},
        'parameters': obj({'rotationXYZ': VECTOR3, 'translation': VECTOR3, 'tfStart': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'tfWidth': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1}, 'crop': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 6, 'maxItems': 6}})
    }, ['expectedRevision', 'reason', 'parameters'])},
    {'name': 'inspect_slice', 'description': 'Return a real grayscale voxel slice, not a raycast. Storage axes x/y/z only; does not change the scene. Limited by the same observation budget as render_scene.', 'inputSchema': obj({'axis': {'type': 'string', 'enum': ['x', 'y', 'z']}, 'index': {'type': 'integer', 'minimum': 0}}, ['axis', 'index'])},
    {'name': 'finish_task', 'description': 'Save a previously rendered frame, matching scene/readback/GSC and the complete trace as the final result. No rendering or hidden improvement. Describe limitations honestly: your statement is an unverified model claim, not task success.', 'inputSchema': obj({'frameRevision': {'type': 'integer', 'minimum': 0}, 'summary': {'type': 'string', 'maxLength': 6000}}, ['frameRevision', 'summary'])},
]


def validate_arguments(value, schema, path='arguments'):
    """Small closed-schema validator for this finite optional adapter contract."""
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict): raise ValueError(path + ' must be an object')
        if set(value) - set(schema['properties']): raise ValueError(path + ' has unknown fields')
        if set(schema.get('required', [])) - set(value): raise ValueError(path + ' lacks required fields')
        for key, child in value.items(): validate_arguments(child, schema['properties'][key], path + '.' + key)
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100): raise ValueError(path + ' has invalid length')
        for child in value: validate_arguments(child, schema['items'], path)
    elif kind in ('integer', 'number'):
        import math
        if type(value) not in ((int,) if kind == 'integer' else (int, float)) or not math.isfinite(value): raise ValueError(path + ' is not a finite ' + kind)
        if 'minimum' in schema and value < schema['minimum']: raise ValueError(path + ' below minimum')
        if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']: raise ValueError(path + ' below exclusive minimum')
        if 'maximum' in schema and value > schema['maximum']: raise ValueError(path + ' above maximum')
    elif kind == 'string':
        if type(value) != str or len(value) > schema.get('maxLength', 10000): raise ValueError(path + ' must be a bounded string')
    if 'enum' in schema and value not in schema['enum']: raise ValueError(path + ' is not an allowed value')


class ExternalTools:
    def __init__(self, session_type, sessions):
        self.session_type = session_type
        self.sessions = sessions
        self.open_lock = threading.Lock()

    def open(self, body):
        validate_arguments(body, obj({'source': {'type': 'string'}, 'goal': {'type': 'string'}, 'model': {'type': 'string'}, 'budget': {'type': 'integer', 'minimum': 1, 'maximum': 30}}, ['source', 'goal', 'model', 'budget']))
        if not body['goal'].strip(): raise ValueError('Task required')
        with self.open_lock:
            if any(s.lock.locked() for s in self.sessions.values()): raise ValueError('Renderer is busy')
            s = self.session_type(body['source'], body['goal'], body['model'], body['budget'])
            s.external = True
            self.sessions[s.id] = s
            with s.lock:
                try:
                    s.state = 'running'
                    s.volume = prepare(s.source, s.directory / 'volume')
                    s.scene = initial_scene(s.volume)
                    s.result = render(s.scene, s.volume, s.directory / 'frame-0', s.stop)
                    s.event('initial_render', result=s.result, controller='external-agent')
                except Exception as error:
                    s.state = 'failed'; s.event('error', message=str(error)); raise
            return self.snapshot(s)

    def snapshot(self, s, image=None):
        result = image or s.result
        path = Path(result['image'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != result['imageSha256']: raise ValueError('Artifact changed')
        return {'id': s.id, 'state': s.state, 'goal': s.goal, 'scene': s.scene, 'nativeState': s.result['nativeState'],
                'volume': {k: s.volume[k] for k in ['dimensions', 'geometry', 'orientation', 'axisOrder', 'rendererMapping', 'spacingDefaulted']},
                'remainingObservations': s.budget - s.used, 'imageSha256': result['imageSha256'],
                'imageUrl': '/artifact/' + path.relative_to(s.directory.parent).as_posix(),
                'imageKind': 'voxel-slice' if image else 'native-raycast',
                'slice': {k: image[k] for k in ['axis', 'index', 'pixelAxes', 'verticalFlip', 'valueDomain']} if image else None,
                'taskSuccess': 'unverified'}

    def call(self, body):
        validate_arguments(body, obj({'id': {'type': 'string'}, 'name': {'type': 'string'}, 'arguments': {}}, ['id', 'name', 'arguments']))
        s = self.sessions[body['id']]
        if not getattr(s, 'external', False): raise ValueError('Session belongs to the existing Phase 1 controller')
        if not s.lock.acquire(blocking=False): raise ValueError('Session is busy')
        started = time.monotonic()
        try:
            name = body['name']; args = body['arguments']
            schema = next((t['inputSchema'] for t in TOOLS if t['name'] == name), None)
            if schema is None: raise ValueError('Unknown tool')
            validate_arguments(args, schema)
            s.event('external_tool_call', name=name, arguments=args)
            if name == 'inspect_scene': return self.snapshot(s)
            if s.stop.is_set() or s.state != 'running': raise ValueError('Session is not running')
            if name == 'finish_task':
                frame = s.directory / ('frame-' + str(args['frameRevision']))
                result = json.loads((frame / 'result.json').read_text())
                if result['scene']['revision'] != args['frameRevision']: raise ValueError('Frame revision mismatch')
                if hashlib.sha256((frame / 'image.png').read_bytes()).hexdigest() != result['imageSha256']: raise ValueError('Final PNG changed')
                final = s.directory / 'final'; final.mkdir()
                for f in ['image.png', 'scene.json', 'native-state.json', 'script.gsc', 'result.json']: shutil.copy2(frame / f, final / f)
                s.state = 'finished'; s.event('finish', modelClaim=args['summary'], selectedRevision=args['frameRevision'], externalTaskVerification='unverified')
                return {'id': s.id, 'state': s.state, 'selectedRevision': args['frameRevision'], 'imageSha256': result['imageSha256'], 'finalDirectory': str(final), 'taskSuccess': 'unverified'}
            if s.used >= s.budget: raise ValueError('Observation budget exhausted; finish with an existing frame')
            if name == 'inspect_slice':
                # Validate before consuming budget or making an artifact directory.
                axis = args['axis']; index = args['index']
                if index >= s.volume['dimensions']['xyz'.index(axis)]: raise ValueError('Slice index out of bounds')
                s.used += 1
                directory = s.directory / ('slice-' + str(s.used)); directory.mkdir()
                result = inspect_slice(s.volume, axis, index, directory)
                (directory / 'result.json').write_text(json.dumps(result, indent=2))
                s.event('slice', result=result)
                return self.snapshot(s, result)
            if args['expectedRevision'] != s.scene['revision']: raise ValueError('Stale scene revision')
            p = args['parameters']
            if not p: raise ValueError('Empty parameter patch')
            candidate = copy.deepcopy(s.scene)
            for k, owner, field in [('tfStart','tf','start'), ('tfWidth','tf','width'), ('rotationXYZ','camera','rotationXYZ'), ('translation','camera','translation')]:
                if k in p: candidate[owner][field] = p[k]
            if 'crop' in p: candidate['crop'] = p['crop']
            candidate['revision'] += 1
            validate_scene(candidate, s.volume)
            s.used += 1; s.save()
            result = render(candidate, s.volume, s.directory / ('frame-' + str(candidate['revision'])), s.stop)
            s.scene = candidate; s.result = result
            s.event('render', result=result)
            return self.snapshot(s)
        except Exception as error:
            try:
                s.event('tool_error', name=body.get('name'), message=str(error))
            except Exception:
                pass
            raise
        finally:
            try:
                s.event('external_tool_return', name=body.get('name'), seconds=time.monotonic() - started)
            except Exception:
                pass
            finally:
                s.lock.release()
