"""Small authenticated client for the local bigfeels memory service."""
import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request


OPERATIONS = frozenset({
    'observe', 'remember', 'context', 'search', 'inspect', 'correct',
    'forget_preview', 'forget', 'process', 'status', 'export',
})
MAX_RESPONSE_BYTES = 16_000_000


class ClientError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ClientError('Service redirects are not allowed', 502)


def _is_loopback(hostname):
    if hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class MemoryClient:
    def __init__(self, base_url, token, timeout=10):
        if not isinstance(base_url, str):
            raise ClientError('Service URL must be a string', 400)
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != 'http' or not parsed.hostname or
                not _is_loopback(parsed.hostname) or parsed.username or
                parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ('', '/')):
            raise ClientError('Service URL must be loopback HTTP', 400)
        if (not isinstance(token, str) or not token or len(token) > 500 or
                not token.isascii() or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in token)):
            raise ClientError('Invalid credential', 401)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ClientError('Timeout must be between 0 and 60 seconds', 400)
        self.base_url = base_url.rstrip('/')
        self._token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
        )

    def call(self, operation, payload):
        if operation not in OPERATIONS:
            raise ClientError('Unknown operation', 404)
        if not isinstance(payload, dict):
            raise ClientError('Request must be an object', 400)
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError):
            raise ClientError('Request is not valid JSON', 400) from None
        if len(body) > 1_000_000:
            raise ClientError('Request body exceeds limit', 413)
        request = urllib.request.Request(
            f'{self.base_url}/v1/{operation}',
            data=body,
            headers={
                'Authorization': 'Bearer ' + self._token,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            method='POST',
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(65_537)
                status = exc.code
            finally:
                exc.close()
            message = self._error_message(raw)
            raise ClientError(message, status) from None
        except ClientError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError):
            raise ClientError('Memory service is unavailable', 503) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ClientError('Service response exceeds limit', 502)
        if status != 200:
            raise ClientError(self._error_message(raw), status)
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError('Service returned invalid JSON', 502) from None
        if not isinstance(result, dict):
            raise ClientError('Service returned an invalid response', 502)
        return result

    def process_pending(self, limit=8):
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('Processing limit must be between 1 and 8')
        result = self.call('process', {'limit': limit})
        processed = result.get('processed')
        if type(processed) is not int or processed < 0:
            raise ClientError('Service returned an invalid processing result', 502)
        return processed

    @staticmethod
    def _error_message(raw):
        try:
            result = json.loads(raw)
            message = result['error']['message']
            if isinstance(message, str) and 0 < len(message) <= 500:
                return message
        except (KeyError, TypeError, ValueError, UnicodeDecodeError):
            pass
        return 'Memory service request failed'
