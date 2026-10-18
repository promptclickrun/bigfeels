"""CLI setup usable by a person or an agent, with secret-safe input options."""
import getpass
import os
from pathlib import Path
import time
from . import cloud
from .store import Store


def add_commands(commands):
    top = commands.add_parser('cloud', help='Connect your own Cloudflare memory vault (Workers Paid recommended)')
    sub = top.add_subparsers(dest='cloud_command', required=True)
    for name in ('connect', 'join'):
        p = sub.add_parser(name, help='Connect using an administrator password' if name == 'connect' else 'Join using a one-time invitation')
        p.add_argument('url')
        p.add_argument('--secret-env', help='Read the password/invitation from this environment variable; otherwise prompt privately')
        p.add_argument('--space', action='append', dest='spaces')
        p.add_argument('--name', default='device')
        p.add_argument('--allow-local', action='store_true', help='Allow loopback HTTP for local integration testing')
        p.add_argument('--interval', type=int, default=30)
    sub.add_parser('status')
    sub.add_parser('sync', help='Synchronize once')
    sub.add_parser('run', help='Keep syncing in the foreground; suitable for a user service')
    invite = sub.add_parser('invite', help='Create a one-time, ten-minute invitation')
    invite.add_argument('--space', action='append', dest='spaces')
    sub.add_parser('devices', help='List registered devices (administrator only)')
    revoke = sub.add_parser('revoke', help='Revoke a device')
    revoke.add_argument('device_id')
    sub.add_parser('rotate', help='Rotate this device credential')
    disconnect = sub.add_parser('disconnect', help='Revoke this device and stop local cloud sync')
    disconnect.add_argument('--offline', action='store_true', help='Remove local credentials without revoking on the cloud')
    commands.add_parser('sync', help='Synchronize with the configured cloud once')
    wiki = commands.add_parser('wiki', help='Generate a local Obsidian-compatible wiki')
    wsub = wiki.add_subparsers(dest='wiki_command', required=True)
    export = wsub.add_parser('export')
    export.add_argument('vault')
    export.add_argument('--space', action='append', dest='spaces')
    export.add_argument('--include-evidence', action='store_true')


def run(args, data_dir, stdout, stderr):
    from .cli import _private_json, _write
    if args.command == 'wiki':
        from .wiki import export_wiki
        result = export_wiki(Store(data_dir / 'memory.sqlite'), args.vault,
                             args.spaces or ['owner'], args.include_evidence)
    elif args.command == 'sync' or args.cloud_command == 'sync':
        result = cloud.sync_once(data_dir)
    elif args.cloud_command in ('connect', 'join'):
        stderr.write(cloud.PLAN_RECOMMENDATION + '\n')
        stderr.write('Selected spaces will be readable in your Cloudflare account.\n')
        secret = os.environ.get(args.secret_env) if args.secret_env else getpass.getpass('Password or invitation: ', stream=stderr)
        if not secret:
            raise ValueError('Password or invitation is required')
        if args.interval < 1:
            raise ValueError('Interval must be at least one second')
        result = cloud.connect(data_dir, args.url, secret, spaces=args.spaces or ['owner'], name=args.name,
                               invite=args.cloud_command == 'join', allow_local=args.allow_local, interval=args.interval)
        _write(stdout, result)
        # Pairing is persisted even if bootstrap is offline; a later sync resumes.
        result = cloud.sync_once(data_dir)
    elif args.cloud_command == 'status':
        result = cloud.status(Store(data_dir / 'memory.sqlite'))
    elif args.cloud_command == 'disconnect':
        result = cloud.disconnect(data_dir, offline=args.offline)
    elif args.cloud_command == 'run':
        cloud.configured_client(data_dir)
        worker = cloud.BackgroundSync(data_dir).start()
        _write(stdout, {'status': 'running', 'recommendation': cloud.PLAN_RECOMMENDATION})
        try:
            while worker.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            worker.close()
        return
    else:
        config, client = cloud.configured_client(data_dir)
        if args.cloud_command == 'invite':
            result = client.request('/v1/devices/invite', {'spaces': args.spaces or config['spaces']})
        elif args.cloud_command == 'devices':
            result = client.request('/v1/devices/list', {})
        elif args.cloud_command == 'revoke':
            result = client.request('/v1/devices/revoke', {'device_id': args.device_id})
        else:
            rotated = client.request('/v1/devices/rotate', {})
            config['token'] = rotated['token']
            _private_json(data_dir / cloud.CONFIG_NAME, config, replace=True)
            result = {'status': 'rotated'}
    _write(stdout, result)
