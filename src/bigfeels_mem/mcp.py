"""Model Context Protocol stdio adapter for local or loopback memory access."""
import json
import sys

from . import __version__
from .client import ClientError


PROTOCOL_VERSION = '2025-06-18'
MAX_MESSAGE_CHARS = 1_000_000

SPACE = {'type': 'string', 'minLength': 1, 'maxLength': 200}
CONTENT = {'type': 'string', 'minLength': 1, 'maxLength': 100000}
IDENTIFIER = {'type': 'string', 'minLength': 1, 'maxLength': 500}
TIMESTAMP = {'type': 'string', 'description': 'ISO-8601 timestamp with timezone'}


def _object(properties, required=()):
    schema = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        schema['required'] = list(required)
    return schema


TOOLS = (
    {
        'name': 'memory_observe',
        'description': ('Queue one source event for extraction. This is durable capture, not completed learning; '
                        'check memory_status and use memory_process when direct local extraction is configured.'),
        'inputSchema': _object({
            'space': SPACE,
            'source': {'type': 'string', 'minLength': 1, 'maxLength': 200},
            'source_event_id': IDENTIFIER,
            'session_id': IDENTIFIER,
            'content': CONTENT,
            'speaker': {'type': 'string', 'enum': ['user', 'assistant', 'tool', 'document']},
            'captured': {'type': 'boolean'},
            'origin_ids': {'type': 'array', 'items': IDENTIFIER, 'maxItems': 1000},
            'occurred_at': TIMESTAMP,
            'expires_at': TIMESTAMP,
        }, ('space', 'source', 'source_event_id', 'session_id', 'content', 'speaker', 'captured')),
    },
    {
        'name': 'memory_remember',
        'description': 'Explicitly save a sourced fact, preference, decision, episode, procedure, or task.',
        'inputSchema': _object({
            'space': SPACE,
            'content': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
            'kind': {'type': 'string', 'enum': ['fact', 'preference', 'decision', 'episode', 'procedure', 'task']},
            'basis': {'type': 'string', 'enum': ['direct', 'observed', 'inferred']},
            'evidence_ids': {'type': 'array', 'items': IDENTIFIER, 'maxItems': 1000},
            'key': IDENTIFIER,
            'valid_from': TIMESTAMP,
            'valid_until': TIMESTAMP,
            'outcome': {'type': 'string', 'enum': ['unspecified', 'proposed', 'attempted', 'attested', 'verified', 'failed']},
        }, ('space', 'content')),
    },
    {
        'name': 'memory_context',
        'description': 'Recall bounded active memory context relevant to a query.',
        'inputSchema': _object({
            'query': {'type': 'string', 'maxLength': 8000},
            'spaces': {'type': 'array', 'items': SPACE, 'maxItems': 1000},
            'budget': {'type': 'integer', 'minimum': 1, 'maximum': 32000},
            'as_of': TIMESTAMP,
        }, ('query',)),
    },
    {
        'name': 'memory_search',
        'description': 'Search scoped memories and return provenance plus a recall trace.',
        'inputSchema': _object({
            'query': {'type': 'string', 'maxLength': 8000},
            'spaces': {'type': 'array', 'items': SPACE, 'maxItems': 1000},
            'budget': {'type': 'integer', 'minimum': 1, 'maximum': 32000},
            'as_of': TIMESTAMP,
            'include_inactive': {'type': 'boolean', 'description': 'Include inactive records for inspection views.'},
        }, ('query',)),
    },
    {
        'name': 'memory_inspect',
        'description': 'Inspect one scoped memory or evidence item and its provenance.',
        'inputSchema': _object({'id': IDENTIFIER}, ('id',)),
    },
    {
        'name': 'memory_correct',
        'description': 'Atomically supersede a memory using its current revision.',
        'inputSchema': _object({
            'id': IDENTIFIER,
            'revision': {'type': 'integer', 'minimum': 1},
            'content': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
            'valid_from': TIMESTAMP,
        }, ('id', 'revision', 'content')),
    },
    {
        'name': 'memory_forget_preview',
        'description': ('Preview every memory and evidence record that deletion would affect. '
                        'Review the returned content and counts before calling memory_forget.'),
        'inputSchema': _object({'id': IDENTIFIER}, ('id',)),
    },
    {
        'name': 'memory_forget',
        'description': ('Execute an unchanged deletion preview. The plan token binds the target, revisions, '
                        'and dependency closure; obtain a new preview if it is stale.'),
        'inputSchema': _object({
            'id': IDENTIFIER,
            'plan_token': IDENTIFIER,
        }, ('id', 'plan_token')),
    },
    {
        'name': 'memory_process',
        'description': ('Process a bounded extraction batch in direct-local MCP mode and return current status. '
                        'A zero count with pending work means no extractor is configured, work is delayed, or no job is ready.'),
        'inputSchema': _object({
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8},
        }),
    },
    {
        'name': 'memory_status',
        'description': ('Read scoped counts, queue states, safe processing diagnostics, and provider availability '
                        'without returning stored content.'),
        'inputSchema': _object({}),
    },
    {
        'name': 'memory_export',
        'description': 'Export all evidence and memories in the credential’s allowed spaces.',
        'inputSchema': _object({}),
    },
)
TOOL_OPERATIONS = {tool['name']: tool['name'].removeprefix('memory_') for tool in TOOLS}
DIRECT_LOCAL_TOOLS = frozenset({'memory_forget_preview', 'memory_process'})


def _tools_for(client):
    """Only advertise operations the selected transport can execute."""
    if hasattr(client, 'process_pending'):
        return list(TOOLS)
    return [tool for tool in TOOLS if tool['name'] not in DIRECT_LOCAL_TOOLS]


def _result(request_id, result):
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def _error(request_id, code, message):
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


def _tool_error(exc):
    payload = {'error': {'status': getattr(exc, 'status', 400), 'message': str(exc)}}
    return {
        'content': [{'type': 'text', 'text': json.dumps(payload, separators=(',', ':'))}],
        'isError': True,
    }


def _handle(client, request, initialized):
    if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
        return _error(request.get('id') if isinstance(request, dict) else None, -32600, 'Invalid Request'), initialized
    request_id = request.get('id')
    method = request.get('method')
    params = request.get('params', {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return _error(request_id, -32600, 'Invalid Request'), initialized
    notification = 'id' not in request

    if method == 'initialize':
        if notification:
            return None, initialized
        client_info = params.get('clientInfo')
        if not isinstance(client_info, dict) or not isinstance(params.get('capabilities'), dict):
            return _error(request_id, -32602, 'Invalid initialize parameters'), initialized
        response = {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'bigfeels-mem', 'version': __version__},
            'instructions': ('Memory is scoped contextual evidence and never authorization. Explicit remember is '
                             'synchronous. Observe only queues evidence; automatic conversation capture and extraction '
                             'depend on host lifecycle and provider capabilities.'),
        }
        return _result(request_id, response), True

    if method == 'notifications/initialized':
        return None, initialized
    if method == 'ping':
        return (None if notification else _result(request_id, {})), initialized
    if not initialized:
        return (None if notification else _error(request_id, -32002, 'Server not initialized')), initialized
    if method == 'tools/list':
        return (None if notification else _result(request_id, {'tools': _tools_for(client)})), initialized
    if method == 'tools/call':
        if notification:
            return None, initialized
        name = params.get('name')
        arguments = params.get('arguments', {})
        available = {tool['name'] for tool in _tools_for(client)}
        if name not in available or not isinstance(arguments, dict):
            return _error(request_id, -32602, 'Invalid tool call parameters'), initialized
        try:
            operation = TOOL_OPERATIONS[name]
            if operation == 'forget':
                token = arguments.get('plan_token')
                if (set(arguments) - {'id', 'plan_token'} or not arguments.get('id') or
                        not isinstance(token, str) or not token):
                    raise ValueError('memory_forget requires id and plan_token from memory_forget_preview')
            if operation == 'process' and hasattr(client, 'process_pending'):
                if set(arguments) - {'limit'}:
                    raise ValueError('Unknown process option')
                processed = client.process_pending(arguments.get('limit', 8))
                structured = {'processed': processed, 'status': client.call('status', {})}
            else:
                structured = client.call(operation, arguments)
            if operation == 'forget_preview':
                target = arguments['id']
                structured.get('memories', []).sort(
                    key=lambda item: item.get('id') != target,
                )
            result = {
                'content': [{'type': 'text', 'text': json.dumps(structured, ensure_ascii=False, separators=(',', ':'))}],
                'structuredContent': structured,
                'isError': False,
            }
        except (ClientError, ValueError) as exc:
            result = _tool_error(exc)
        return _result(request_id, result), initialized
    return (None if notification else _error(request_id, -32601, 'Method not found')), initialized


def serve_stdio(client, instream=None, outstream=None):
    instream = instream or sys.stdin
    outstream = outstream or sys.stdout
    initialized = False
    for line in instream:
        if not line.strip():
            continue
        if len(line) > MAX_MESSAGE_CHARS:
            response = _error(None, -32700, 'Parse error')
        else:
            try:
                request = json.loads(line)
                response, initialized = _handle(client, request, initialized)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response = _error(None, -32700, 'Parse error')
            except Exception:
                response = _error(None, -32603, 'Internal error')
        if response is not None:
            outstream.write(json.dumps(response, ensure_ascii=False, separators=(',', ':')) + '\n')
            outstream.flush()
