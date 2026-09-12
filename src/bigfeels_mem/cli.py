"""Administrative command line for bigfeels memory."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import urllib.parse

from .client import ClientError, MemoryClient
from .mcp import serve_stdio
from .providers import OpenAIProvider, ProviderError
from .server import serve
from .store import MemoryError, Store


CONFIG_VERSION = 1
DATABASE_NAME = 'memory.sqlite'
CONFIG_NAME = 'config.json'
MAX_CONFIG_BYTES = 100_000
MAX_EXPORT_BYTES = 64_000_000


def default_data_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'bigfeels-mem'
    if os.name == 'nt' and os.environ.get('APPDATA'):
        return Path(os.environ['APPDATA']) / 'bigfeels-mem'
    base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'bigfeels-mem'


def _paths(value):
    data_dir = Path(value).expanduser() if value else default_data_dir()
    return data_dir, data_dir / DATABASE_NAME, data_dir / CONFIG_NAME


def _default_config():
    return {'version': CONFIG_VERSION, 'provider': {}}


def _private_json(path, value, replace):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and not replace:
        raise FileExistsError(f'Refusing to overwrite {path}')
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        if replace:
            os.replace(temporary, path)
            temporary = None
        else:
            # Hard-link publication is atomic and fails when another writer
            # creates the destination between our initial check and this step.
            os.link(temporary, path)
            os.unlink(temporary)
            temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def load_config(path):
    path = Path(path)
    if not path.exists():
        return _default_config()
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError('Config file exceeds limit')
    try:
        with path.open(encoding='utf-8') as source:
            config = json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Config file is not valid JSON') from None
    if (not isinstance(config, dict) or config.get('version') != CONFIG_VERSION or
            not isinstance(config.get('provider'), dict)):
        raise ValueError('Unsupported config file')
    return config


def ensure_config(path):
    if not Path(path).exists():
        _private_json(path, _default_config(), replace=False)
    return load_config(path)


def _provider(store, config):
    provider = OpenAIProvider.from_config(config['provider'])
    store.embedder = provider if provider and provider.can_embed else None
    store.provider_status = {
        'extraction': 'configured' if provider and provider.can_extract else 'not_configured',
        'embeddings': 'configured' if provider and provider.can_embed else 'not_configured',
    }
    return provider


def _token(environment_name):
    if not isinstance(environment_name, str) or not environment_name.isidentifier():
        raise ValueError('Token environment must be a valid name')
    token = os.environ.get(environment_name)
    if not token:
        raise ValueError(f'Set {environment_name} to a paired credential')
    return token


def _write(output, payload):
    output.write(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
    output.flush()


def build_parser():
    parser = argparse.ArgumentParser(prog='bigfeels-mem', description='Private local memory service')
    parser.add_argument('--data-dir', help='Local data directory')
    commands = parser.add_subparsers(dest='command', required=True)

    commands.add_parser('init', help='Initialize private local storage')

    space = commands.add_parser('space', help='Create a memory space')
    space.add_argument('name')

    pair = commands.add_parser('pair', help='Create a scoped client credential')
    pair.add_argument('name')
    pair.add_argument('--space', action='append', required=True, dest='spaces')

    revoke = commands.add_parser('revoke', help='Revoke a paired client credential')
    revoke.add_argument('--token-env', default='BIGFEELS_MEM_TOKEN')

    configure = commands.add_parser('configure', help='Configure an optional OpenAI-compatible provider')
    configure.add_argument('--base-url')
    configure.add_argument('--api-key-env')
    configure.add_argument('--extraction-model')
    configure.add_argument('--embedding-model')
    configure.add_argument('--timeout', type=float)
    configure.add_argument('--allow-remote', action=argparse.BooleanOptionalAction, default=None)

    serve_command = commands.add_parser('serve', help='Run the authenticated loopback service')
    serve_command.add_argument('--host', default='127.0.0.1')
    serve_command.add_argument('--port', type=int, default=8765)
    serve_command.add_argument('--worker-interval', type=float, default=1.0)

    commands.add_parser('doctor', help='Check local storage and provider configuration')

    export = commands.add_parser('export', help='Export credential-scoped memory')
    export.add_argument('--output', required=True)
    export.add_argument('--token-env', default='BIGFEELS_MEM_TOKEN')

    restore = commands.add_parser('restore', help='Restore an export into an empty store')
    restore.add_argument('input')

    commands.add_parser('maintenance', help='Run retention and database maintenance')

    mcp = commands.add_parser('mcp', help='Run the MCP adapter over stdio')
    mcp.add_argument('--url', default='http://127.0.0.1:8765')
    mcp.add_argument('--token-env', default='BIGFEELS_MEM_TOKEN')
    return parser


def _doctor(database_path, config_path):
    result = {
        'status': 'ok',
        'database': 'missing',
        'config': 'missing',
        'providers': {'extraction': 'not_configured', 'embeddings': 'not_configured'},
    }
    if database_path.exists():
        database_uri = database_path.resolve().as_uri() + '?mode=ro&immutable=1'
        try:
            with sqlite3.connect(database_uri, uri=True, timeout=2) as connection:
                connection.execute('PRAGMA query_only=ON')
                integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
                version = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        except sqlite3.Error:
            raise ValueError('Database could not be read') from None
        if integrity != 'ok' or not version or version[0] != '1':
            raise ValueError('Database integrity or schema check failed')
        result['database'] = 'ok'
    if config_path.exists():
        config = load_config(config_path)
        result['config'] = 'ok'
        provider = OpenAIProvider.from_config(config['provider'])
        if provider:
            local = urllib.parse.urlsplit(provider.base_url).hostname in ('localhost', '127.0.0.1', '::1')
            key_present = bool(os.environ.get(provider.key_env))
            available = local or key_present
            result['providers'] = {
                'extraction': ('available' if available else 'credential_missing') if provider.can_extract else 'not_configured',
                'embeddings': ('available' if available else 'credential_missing') if provider.can_embed else 'not_configured',
            }
            result['api_key_environment'] = {'name': provider.key_env, 'present': key_present}
    if result['database'] != 'ok' or result['config'] != 'ok':
        result['status'] = 'needs_attention'
    return result


def main(argv=None, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    data_dir, database_path, config_path = _paths(args.data_dir)
    try:
        if args.command == 'init':
            data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            Store(database_path)
            ensure_config(config_path)
            _write(stdout, {'status': 'initialized', 'data_dir': str(data_dir)})
        elif args.command == 'space':
            result = Store(database_path).create_space(args.name)
            _write(stdout, {'status': 'created', **result})
        elif args.command == 'pair':
            token = Store(database_path).pair(args.name, args.spaces)
            _write(stdout, {'name': args.name, 'spaces': args.spaces, 'token': token})
        elif args.command == 'revoke':
            Store(database_path).revoke(_token(args.token_env))
            _write(stdout, {'status': 'revoked'})
        elif args.command == 'configure':
            config = ensure_config(config_path)
            provider_config = dict(config['provider'])
            mapping = {
                'base_url': args.base_url,
                'api_key_env': args.api_key_env,
                'extraction_model': args.extraction_model,
                'embedding_model': args.embedding_model,
                'timeout': args.timeout,
                'allow_remote': args.allow_remote,
            }
            provider_config.update({key: value for key, value in mapping.items() if value is not None})
            if provider_config.get('extraction_model') or provider_config.get('embedding_model'):
                OpenAIProvider.from_config(provider_config)
            config['provider'] = provider_config
            _private_json(config_path, config, replace=True)
            _write(stdout, {
                'status': 'configured',
                'api_key_environment': provider_config.get('api_key_env', 'OPENAI_API_KEY'),
                'remote_content_transfer': provider_config.get('allow_remote') is True,
            })
        elif args.command == 'serve':
            store = Store(database_path)
            provider = _provider(store, ensure_config(config_path))
            serve(
                store, host=args.host, port=args.port, provider=provider,
                worker_interval=args.worker_interval,
                started=lambda actual_port: _write(stdout, {
                    'status': 'serving', 'url': f'http://{args.host}:{actual_port}',
                }),
            )
        elif args.command == 'doctor':
            _write(stdout, _doctor(database_path, config_path))
        elif args.command == 'export':
            store = Store(database_path)
            principal = store.authenticate(_token(args.token_env))
            bundle = store.dispatch(principal, 'export', {})
            _private_json(args.output, bundle, replace=False)
            _write(stdout, {'status': 'exported', 'output': str(Path(args.output))})
        elif args.command == 'restore':
            source = Path(args.input)
            if source.stat().st_size > MAX_EXPORT_BYTES:
                raise ValueError('Export file exceeds limit')
            with source.open(encoding='utf-8') as input_file:
                bundle = json.load(input_file)
            result = Store(database_path).restore(bundle)
            _write(stdout, result)
        elif args.command == 'maintenance':
            _write(stdout, {'status': 'ok', **Store(database_path).maintenance()})
        elif args.command == 'mcp':
            client = MemoryClient(args.url, _token(args.token_env))
            serve_stdio(client, sys.stdin, stdout)
        return 0
    except (MemoryError, ClientError, ProviderError, OSError, ValueError, json.JSONDecodeError) as exc:
        status = getattr(exc, 'status', 1)
        _write(stderr, {'error': str(exc), 'status': status})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
