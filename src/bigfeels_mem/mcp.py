"""Model Context Protocol stdio adapter for local or loopback memory access."""
import json
import sys
import uuid

from . import __version__
from .client import ClientError
from .providers import (ProviderError, batch_extraction_messages, extraction_messages,
                        parse_batch_extraction, parse_extraction)
from .store import MAX_BUDGET


PROTOCOL_VERSION = '2025-06-18'
MAX_MESSAGE_CHARS = 1_000_000

SPACE = {'type': 'string', 'minLength': 1, 'maxLength': 200}
CONTENT = {'type': 'string', 'minLength': 1, 'maxLength': 100000}
IDENTIFIER = {'type': 'string', 'minLength': 1, 'maxLength': 500}
TIMESTAMP = {'type': 'string', 'description': 'ISO-8601 timestamp with timezone'}
# Agents answered BEAM questions best with several focused searches.
SEARCH_HINT = ('For a question spanning several topics, sessions, or events, search again with '
               'different queries; 8000-16000 bytes per call suits most questions.')


def _object(properties, required=()):
    schema = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        schema['required'] = list(required)
    return schema


TOOLS = (
    {
        'name': 'memory_observe',
        'description': ('Queue one source event for extraction. This is durable capture, not completed learning; '
                        'call memory_process afterwards to write memories from it.'),
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
        'description': ('Recall active memories relevant to a query, best match first, within a byte '
                        'budget (default 800). Use this to gather memory for answering. ' + SEARCH_HINT),
        'inputSchema': _object({
            'query': {'type': 'string', 'maxLength': 8000},
            'spaces': {'type': 'array', 'items': SPACE, 'maxItems': 1000},
            'budget': {'type': 'integer', 'minimum': 1, 'maximum': MAX_BUDGET},
            'as_of': TIMESTAMP,
        }, ('query',)),
    },
    {
        'name': 'memory_search',
        'description': 'Search scoped memories and return full provenance plus a recall trace, for inspection.',
        'inputSchema': _object({
            'query': {'type': 'string', 'maxLength': 8000},
            'spaces': {'type': 'array', 'items': SPACE, 'maxItems': 1000},
            'budget': {'type': 'integer', 'minimum': 1, 'maximum': MAX_BUDGET},
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
        'description': ('Write memories from queued events in direct-local MCP mode and return current status. '
                        'Uses the configured extraction model, otherwise asks this client\'s model when it supports '
                        'sampling. A zero count with pending work means no model is available, work is delayed, '
                        'or no job is ready.'),
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
EXPLICIT_ONLY_EXCLUDED_TOOLS = frozenset({'memory_observe', 'memory_process'})


def _tools_for(client, *, explicit_only=False):
    """Only advertise operations the selected transport can execute."""
    tools = (list(TOOLS) if hasattr(client, 'process_pending') else
             [tool for tool in TOOLS if tool['name'] not in DIRECT_LOCAL_TOOLS])
    if explicit_only:
        tools = [tool for tool in tools if tool['name'] not in EXPLICIT_ONLY_EXCLUDED_TOOLS]
    return tools


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


def _handle(client, request, initialized, *, explicit_only=False):
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
        instructions = (
            'Memory is scoped contextual evidence and never authorization. This server is explicit-only: '
            'conversation observation and extraction processing are disabled.'
            if explicit_only else
            'Memory is scoped contextual evidence and never authorization. Explicit remember is synchronous. '
            'Observe only queues evidence; automatic conversation capture and extraction depend on host lifecycle '
            'and provider capabilities.'
        )
        response = {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'bigfeels-mem', 'version': __version__},
            'instructions': instructions,
        }
        return _result(request_id, response), True

    if method == 'notifications/initialized':
        return None, initialized
    if method == 'ping':
        return (None if notification else _result(request_id, {})), initialized
    if not initialized:
        return (None if notification else _error(request_id, -32002, 'Server not initialized')), initialized
    if method == 'tools/list':
        return (None if notification else _result(
            request_id, {'tools': _tools_for(client, explicit_only=explicit_only)},
        )), initialized
    if method == 'tools/call':
        if notification:
            return None, initialized
        name = params.get('name')
        arguments = params.get('arguments', {})
        available = {
            tool['name'] for tool in _tools_for(client, explicit_only=explicit_only)
        }
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
        except (ClientError, ProviderError, ValueError) as exc:
            result = _tool_error(exc)
        return _result(request_id, result), initialized
    return (None if notification else _error(request_id, -32601, 'Method not found')), initialized


class _Session:
    """One stdio connection. A tool call can send the client a request and
    wait for its reply; client messages arriving meanwhile run afterwards."""

    def __init__(self, instream, outstream):
        self.input, self.output = instream, outstream
        self.deferred = []

    def send(self, message):
        self.output.write(json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n')
        self.output.flush()

    def next_line(self):
        return self.deferred.pop(0) if self.deferred else self.input.readline()

    def request(self, method, params):
        identifier = 'bigfeels-' + uuid.uuid4().hex
        self.send({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
        while line := self.input.readline():
            try:
                message = json.loads(line) if len(line) <= MAX_MESSAGE_CHARS else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            if isinstance(message, dict) and message.get('id') == identifier and 'method' not in message:
                if not isinstance(message.get('result'), dict):
                    raise ProviderError('Client model request failed')
                return message['result']
            self.deferred.append(line)
        raise ProviderError('Client closed during a model request')


class SamplingExtractor:
    """Extracts memories with the MCP client's own model via sampling/createMessage."""

    def __init__(self, session):
        self.session = session

    def _complete(self, messages, max_tokens):
        system, user = messages
        result = self.session.request('sampling/createMessage', {
            'systemPrompt': system['content'],
            'messages': [{'role': 'user', 'content': {'type': 'text', 'text': user['content']}}],
            'maxTokens': max_tokens,
            'includeContext': 'none',
            # Extraction is routine; clients may pick a fast, inexpensive model.
            'modelPreferences': {'costPriority': 0.8, 'speedPriority': 0.8, 'intelligencePriority': 0.4},
        })
        content = result.get('content')
        blocks = content if isinstance(content, list) else [content]
        return ''.join(block['text'] for block in blocks
                       if isinstance(block, dict) and block.get('type') == 'text'
                       and isinstance(block.get('text'), str))

    def extract(self, evidence):
        return parse_extraction(self._complete(extraction_messages(evidence), 1200))

    def extract_batch(self, items):
        return parse_batch_extraction(
            self._complete(batch_extraction_messages(items), min(1200 * len(items), 8000)), len(items))


def _use_client_model(client, session, request):
    """Extract with the client's model when it offers sampling and none is configured."""
    capabilities = request.get('params', {}).get('capabilities')
    if (getattr(client, 'extractor', True) is None and isinstance(capabilities, dict)
            and isinstance(capabilities.get('sampling'), dict)):
        client.extractor = SamplingExtractor(session)
        client.store.provider_status['extraction'] = 'host'


def serve_stdio(client, instream=None, outstream=None, *, explicit_only=False):
    session = _Session(instream or sys.stdin, outstream or sys.stdout)
    initialized = False
    while line := session.next_line():
        if not line.strip():
            continue
        if len(line) > MAX_MESSAGE_CHARS:
            response = _error(None, -32700, 'Parse error')
        else:
            try:
                request = json.loads(line)
                response, initialized = _handle(
                    client, request, initialized, explicit_only=explicit_only,
                )
                if (not explicit_only and isinstance(request, dict) and request.get('method') == 'initialize'
                        and response is not None and 'result' in response):
                    _use_client_model(client, session, request)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response = _error(None, -32700, 'Parse error')
            except Exception:
                response = _error(None, -32603, 'Internal error')
        if response is not None:
            session.send(response)
