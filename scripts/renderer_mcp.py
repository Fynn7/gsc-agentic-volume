"""Optional stdio MCP adapter. All operations use the running application service.

MCP JSON-RPC newline transport; image content uses base64 image/png blocks.
No inference, renderer implementation, or shadow scene state exists here.
"""
import argparse
import base64
import hashlib
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def main():
    # MCP stdio is UTF-8 even when Windows' redirected Python defaults to ACP.
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--service')
    parser.add_argument('--session')
    parser.add_argument('--desktop-config')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if args.desktop_config:
        from desktop_mcp import serve
        return serve(args.desktop_config)
    if not args.service or not args.session: parser.error('--service and --session are required in legacy mode')
    base = args.service.rstrip('/')
    if urlsplit(base).hostname not in ('127.0.0.1', 'localhost'): raise ValueError('Renderer service must be loopback')

    def request(path, body=None):
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=args.timeout) as response: return response.read()

    for line in sys.stdin:
        message = None
        try:
            message = json.loads(line)
            if 'id' not in message: continue
            method = message.get('method'); params = message.get('params', {})
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'gav-native-renderer', 'version': '1.0.0'}}
            elif method == 'ping': result = {}
            elif method == 'tools/list': result = {'tools': json.loads(request('/api/tools'))}
            elif method == 'tools/call':
                try:
                    result_data = json.loads(request('/api/tools/call', {'id': args.session, 'name': params['name'], 'arguments': params.get('arguments', {})}))
                    content = [{'type': 'text', 'text': json.dumps(result_data, allow_nan=False)}]
                    if result_data.get('imageUrl'):
                        image = request(result_data['imageUrl'])
                        if hashlib.sha256(image).hexdigest() != result_data['imageSha256']: raise ValueError('Service screenshot hash mismatch')
                        content.append({'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(image).decode()})
                    result = {'content': content, 'isError': False}
                except Exception as error:
                    detail = error.read().decode(errors='replace') if isinstance(error, urllib.error.HTTPError) else str(error)
                    result = {'content': [{'type': 'text', 'text': detail}], 'isError': True}
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
        except Exception as error:
            print(json.dumps({'jsonrpc': '2.0', 'id': message.get('id') if isinstance(message, dict) else None, 'error': {'code': -32603, 'message': str(error)}}), flush=True)


if __name__ == '__main__': main()
