"""Machine-readable access to the local workbench API, without browser automation."""
import argparse
import json
import mimetypes
import sys
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def api_path(value):
    parsed = urlsplit(value)
    if (parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith('/api/')
            or '\\' in parsed.path or any(part in {'.', '..'} for part in parsed.path.split('/'))):
        raise argparse.ArgumentTypeError('path must be a local /api/... path')
    return value


def parser_for_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--timeout', type=float, default=120)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('schema', help='Return the OpenAPI schema')
    chat = commands.add_parser('chat', help='Ask the workbench Agent')
    chat.add_argument('question')
    chat.add_argument('--session', default='', help='Continue a chat session by ID')
    req = commands.add_parser('request', help='Call an API endpoint')
    req.add_argument('method', type=str.upper, choices=['GET', 'POST', 'PUT', 'DELETE'])
    req.add_argument('path', type=api_path, help='Local /api/... path; query string is allowed')
    req.add_argument('--json', dest='body', help='JSON value, or - to read JSON from stdin')
    req.add_argument('--field', action='append', default=[], metavar='NAME=VALUE', help='Multipart form field')
    req.add_argument('--file', action='append', default=[], metavar='NAME=PATH', help='Multipart upload (repeatable)')
    req.add_argument('--output', type=Path, help='Save response bytes to a new file')
    return parser


def pairs(values, parser):
    result = []
    for value in values:
        name, sep, data = value.partition('=')
        if not sep or not name or not data:
            parser.error('form fields and files must use NAME=VALUE')
        result.append((name, data))
    return result


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
    parser = parser_for_cli()
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.timeout <= 0:
        parser.error('invalid port or timeout')
    try:
        with ExitStack() as stack:
            fields = pairs(getattr(args, 'field', []), parser)
            uploads = pairs(getattr(args, 'file', []), parser)
            if getattr(args, 'body', None) is not None and (fields or uploads):
                parser.error('--json cannot be combined with multipart fields or files')
            if args.command == 'schema':
                method, path, kwargs = 'GET', '/openapi.json', {}
            elif args.command == 'chat':
                method, path = 'POST', '/api/learning-chat'
                kwargs = {'json': {'question': args.question, 'session_id': args.session, 'use_tools': True}}
            else:
                method, path, kwargs = args.method, args.path, {}
                if args.output and method != 'GET':
                    parser.error('--output is only supported for GET requests')
                if args.body is not None:
                    kwargs['json'] = json.loads(sys.stdin.read() if args.body == '-' else args.body)
                if fields or uploads:
                    # Use multipart for form-only requests as well; repeatable names are preserved.
                    kwargs['files'] = [(name, (None, value)) for name, value in fields]
                    for name, filename in uploads:
                        file = Path(filename)
                        kwargs['files'].append((name, (file.name, stack.enter_context(file.open('rb')),
                                                      mimetypes.guess_type(file.name)[0] or 'application/octet-stream')))
            with httpx.Client(base_url=f'http://127.0.0.1:{args.port}', trust_env=False, timeout=args.timeout) as client:
                response = client.request(method, path, **kwargs)
            result = {'ok': response.is_success, 'status': response.status_code}
            if getattr(args, 'output', None) and response.is_success:
                with args.output.open('xb') as output:
                    output.write(response.content)
                result.update(output=str(args.output.resolve()), bytes=len(response.content))
            else:
                try:
                    result['data'] = response.json()
                except ValueError:
                    result['data'] = response.text[:4000]
            print(json.dumps(result, ensure_ascii=False))
            return 0 if response.is_success else 1
    except (httpx.HTTPError, OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
