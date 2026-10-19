"""Replaceable model boundary. Only local Ollama is implemented."""
import json
import base64
import urllib.request
import hashlib
from pathlib import Path
from typing import Protocol
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['tool', 'sceneRevision', 'reason', 'parameters'], 'properties': {'tool': {'type': 'string', 'enum': ['render_scene', 'finish_exploration']}, 'sceneRevision': {'type': 'integer'}, 'reason': {'type': 'string'}, 'parameters': {'type': 'object', 'additionalProperties': False, 'required': [
    'tfStart', 'tfWidth', 'rotationXYZ', 'crop'], 'properties': {'tfStart': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'tfWidth': {'type': 'number', 'minimum': 0.00001, 'maximum': 1}, 'rotationXYZ': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {'type': 'number', 'minimum': -360, 'maximum': 360}}, 'crop': {'type': 'array', 'minItems': 6, 'maxItems': 6, 'items': {'type': 'number'}}}}}}


class ModelDriver(Protocol):
    def decide(self, messages, directory, stop): ...


class OllamaDriver:
    def __init__(self, model='qwen3.5:9b'): self.model = model

    def request(self, endpoint, payload=None, timeout=15):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            'http://127.0.0.1:11434/api/'+endpoint, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)

    def inventory(self):
        tags = self.request('tags')
        installed = next(
            (m for m in tags['models'] if m['name'] == self.model), None)
        if not installed:
            raise ValueError('Requested model is not installed locally')
        show = self.request('show', {'model': self.model})
        if 'vision' not in show.get('capabilities', []):
            raise ValueError('Model does not report vision capability')
        if show.get('remote_host') or show.get('remote_model') or ':cloud' in self.model:
            raise ValueError('Cloud models are not enabled')
        return {'installed': installed, 'show': show, 'ollama': self.request('version')}

    def decide(self, messages, directory, stop):
        directory = Path(directory)
        directory.mkdir()
        body = {'model': self.model, 'messages': messages, 'stream': True, 'think': False, 'format': SCHEMA,
                'options': {'temperature': 0, 'seed': 42, 'num_ctx': 8192, 'num_predict': 1536}, 'keep_alive': '5m'}
        encoded = json.dumps(body).encode()
        (directory/'request.json').write_bytes(encoded)
        (directory/'request.sha256').write_text(hashlib.sha256(encoded).hexdigest())
        req = urllib.request.Request(
            'http://127.0.0.1:11434/api/chat', data=encoded, headers={'Content-Type': 'application/json'})
        content = ''
        terminal = None
        with urllib.request.urlopen(req, timeout=300) as response, (directory/'response.ndjson').open('wb') as raw:
            # Stop closes the active HTTP transport; blocked reads must not wait for a token.
            import threading
            complete = threading.Event()

            def cancel():
                while not complete.wait(.1):
                    if stop.is_set():
                        try:
                            response.fp.raw._sock.shutdown(2)
                        except Exception:
                            pass
                        return
            threading.Thread(target=cancel, daemon=True).start()
            try:
                for line in response:
                    raw.write(line)
                    raw.flush()
                    if stop.is_set():
                        raise InterruptedError('Stopped during model call')
                    item = json.loads(line)
                    if item.get('error'):
                        raise RuntimeError(item['error'])
                    content += item.get('message', {}).get('content', '')
                    if item.get('done'):
                        terminal = item
            finally:
                complete.set()
        (directory/'content.txt').write_text(content)
        if terminal is None or terminal.get('done_reason') == 'length':
            raise ValueError('Incomplete model response')
        action = json.loads(content)
        # Validate independently of the provider. No repair/retry or hidden calls.
        if not isinstance(action, dict) or set(action) != {'tool', 'sceneRevision', 'reason', 'parameters'}:
            raise ValueError('Action schema mismatch')
        if action['tool'] not in ['render_scene', 'finish_exploration'] or type(action['sceneRevision']) != int or type(action['reason']) != str:
            raise ValueError('Invalid action types')
        p = action['parameters']
        if not isinstance(p, dict) or set(p) != {'tfStart', 'tfWidth', 'rotationXYZ', 'crop'}:
            raise ValueError('Invalid tool parameters')
        return action


def image_message(result, text):
    data = Path(result['image']).read_bytes()
    if hashlib.sha256(data).hexdigest() != result['imageSha256']:
        raise ValueError('Screenshot changed before model input')
    return {'role': 'user', 'content': text+'\n'+json.dumps({'scene': result['scene'], 'nativeState': result['nativeState'], 'nonblackPixels': result['nonblackPixels'], 'imageSha256': result['imageSha256']}), 'images': [base64.b64encode(data).decode()]}
