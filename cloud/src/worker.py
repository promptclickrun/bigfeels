"""User-owned Worker + one durable vault per deployment. Never log content."""
import json
import asyncio
from urllib.parse import parse_qs, urlsplit
from workers import WorkerEntrypoint, DurableObject, Response
from database import CloudStore
from bigfeels_mem.cloud_service import CloudService
from bigfeels_mem.cloud_oauth import OAuth
from bigfeels_mem.store import MemoryError
from bigfeels_mem.sync import MAX_BYTES

HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'no-referrer',
           'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}


def reply(status, body, extra=None):
    headers = {**HEADERS, **(extra or {})}
    if isinstance(body, (dict, list)):
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
    return Response(body, status=status, headers=headers)


def parameters(raw):
    return {k: v if k == 'spaces' else v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}


class MemoryVault(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.ctx.storage.transactionSync(self.initialize)

    def initialize(self):
        self.store = CloudStore(self.ctx.storage)
        spaces = [s.strip() for s in getattr(self.env, 'SYNC_SPACES', 'owner').split(',') if s.strip()]
        self.service = CloudService(self.store, getattr(self.env, 'ADMIN_PASSWORD', ''), spaces)

    async def fetch(self, request):
        p = urlsplit(request.url)
        origin = p.scheme + '://' + p.netloc
        cors = {}
        try:
            if p.scheme != 'https' and p.hostname not in ('127.0.0.1', 'localhost', '::1'):
                raise MemoryError('HTTPS is required', 426)
            # Rate accounting commits separately, including rejected attempts.
            maximum = 20 if p.path.startswith('/oauth/') or p.path in ('/v1/devices/connect', '/v1/devices/join') else 600
            self.ctx.storage.transactionSync(lambda: self.service.limit((request.headers.get('CF-Connecting-IP') or 'local') + ':' + str(maximum), maximum))
            supplied_origin = request.headers.get('Origin')
            allowed = [origin] + [s.strip() for s in getattr(self.env, 'ALLOWED_ORIGINS', '').split(',') if s.strip()]
            if supplied_origin and supplied_origin not in allowed:
                raise MemoryError('Origin is not allowed', 403)
            if supplied_origin:
                cors = {'Access-Control-Allow-Origin': supplied_origin,
                        'Access-Control-Expose-Headers': 'WWW-Authenticate'}
            protocol = request.headers.get('MCP-Protocol-Version')
            if p.path == '/mcp' and protocol and protocol not in ('2025-03-26', '2025-06-18'):
                raise MemoryError('Unsupported MCP protocol version')
            if request.method == 'OPTIONS':
                return reply(204, '', {'Access-Control-Allow-Origin': supplied_origin or origin,
                    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Authorization, Content-Type, MCP-Protocol-Version',
                    'Access-Control-Expose-Headers': 'WWW-Authenticate'})
            if p.path == '/mcp' and request.method in ('GET', 'DELETE'):
                token = (request.headers.get('Authorization') or '').removeprefix('Bearer ')
                self.service.authenticate(token, origin + '/mcp')
                return reply(405, {'error': 'This stateless MCP endpoint accepts POST'}, {'Allow': 'POST'})
            public_get = p.path.startswith('/.well-known/') or p.path in ('/oauth/authorize', '/v1/info', '/v1/sync/pull')
            if request.method not in ('GET', 'POST') or (request.method == 'GET' and not public_get):
                raise MemoryError('Method not allowed', 405)
            if request.method == 'POST' and p.path in ('/v1/info', '/v1/sync/pull'):
                raise MemoryError('Method not allowed', 405)
            payload = parameters(p.query)
            if request.method == 'POST':
                # Read incrementally before decoding to cap bodies even without
                # Content-Length. Workers' Request.body is an async iterator.
                raw = bytearray()
                async for chunk in request.body:
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise MemoryError('Request exceeds 2 MB', 413)
                kind = (request.headers.get('Content-Type') or '').split(';')[0]
                if kind == 'application/json':
                    payload = json.loads(raw)
                elif kind == 'application/x-www-form-urlencoded' and p.path.startswith('/oauth/'):
                    payload = parameters(raw.decode())
                else:
                    raise MemoryError('Content-Type must be application/json', 415)
                if not isinstance(payload, dict):
                    raise MemoryError('Request must be an object')
            rerank = payload.pop('rerank', False) if p.path in ('/v1/search', '/v1/context') else False
            if type(rerank) is not bool:
                raise MemoryError('rerank must be boolean')
            if rerank and (getattr(self.env, 'RERANK_ENABLED', 'false') != 'true' or not hasattr(self.env, 'AI')):
                raise MemoryError('Workers AI reranking is not enabled', 400)
            token = (request.headers.get('Authorization') or '').removeprefix('Bearer ')
            def operation():
                self.store.maintenance()
                if p.path.startswith('/oauth/') or p.path.startswith('/.well-known/'):
                    return OAuth(self.service, origin).handle(p.path, request.method, payload, request.headers.get('Cookie') or '')
                result = self.service.handle(p.path, payload, token, parameters(p.query), origin)
                return (202, '', {}) if result is None else (200, result, {})
            status, body, headers = self.ctx.storage.transactionSync(operation)
            if rerank and body.get('memories'):
                try:
                    from bigfeels_mem.cloud_rerank import reorder
                    response = await asyncio.wait_for(self.env.AI.run('@cf/baai/bge-reranker-base', {
                        'query': payload.get('query', ''),
                        'contexts': [{'text': m['content'][:2000]} for m in body['memories'][:20]],
                        'top_k': min(20, len(body['memories'])),
                    }), timeout=10)
                    body = reorder(body, response)
                except Exception:
                    body['rerank'] = 'unavailable_keyword_fallback'
            if supplied_origin:
                headers['Access-Control-Allow-Origin'] = supplied_origin
            return reply(status, body, {**headers, **cors})
        except MemoryError as e:
            headers = {'WWW-Authenticate': 'Bearer resource_metadata="' + origin + '/.well-known/oauth-protected-resource/mcp"'} if e.status == 401 else {}
            if e.status == 429:
                headers['Retry-After'] = '60'
            return reply(e.status, {'error': str(e)}, {**headers, **cors})
        except (ValueError, TypeError, KeyError, UnicodeError):
            return reply(400, {'error': 'Invalid request'}, cors)
        except Exception:
            return reply(500, {'error': 'Internal cloud service error'}, cors)

    async def schedule_maintenance(self):
        await self.ctx.storage.setAlarm(__import__('time').time() * 1000 + 1000)

    async def alarm(self):
        self.ctx.storage.transactionSync(lambda: self.store.maintenance())
        await self.ctx.storage.setAlarm(__import__('time').time() * 1000 + 3600_000)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await self.env.VAULT.getByName('owner').fetch(request)

    async def scheduled(self, controller, env, ctx):
        # Alarm avoids sending administrator credentials through a public URL.
        await self.env.VAULT.getByName('owner').schedule_maintenance()
