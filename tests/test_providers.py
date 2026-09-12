import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

try:
    from bigfeels_mem.providers import OpenAIProvider, ProviderError
except ImportError:
    OpenAIProvider = None


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(OpenAIProvider, 'Hosted model providers are not implemented')
        self.requests = []
        self.reply = {'choices': [{'message': {'content': json.dumps({'memories': [
            {'content': 'I prefer Python', 'quote': 'I prefer Python', 'basis': 'direct', 'kind': 'preference'}]})}}]}
        self.status = 200
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, dict(self.headers), body))
                data = json.dumps(owner.reply).encode()
                self.send_response(owner.status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = dict(base_url=f'http://127.0.0.1:{self.server.server_port}/v1',
                           extraction_model='fixture-extract', embedding_model='fixture-embed',
                           api_key_env='BIGFEELS_TEST_KEY', allow_remote=False)

    def test_extract_uses_role_bounded_input_and_redacts_secrets(self):
        with patch.dict(os.environ, BIGFEELS_TEST_KEY='key123'):
            p = OpenAIProvider.from_config(self.config)
            result = p.extract({'content': 'I prefer Python. api_key=mysecret', 'speaker': 'user', 'id': 'ev_one'})
        self.assertEqual(result[0]['basis'], 'direct')
        path, headers, body = self.requests[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(headers['Authorization'], 'Bearer key123')
        self.assertEqual(body['messages'][1]['role'], 'user')
        self.assertNotIn('mysecret', str(body))
        self.assertEqual(body['model'], 'fixture-extract')

    def test_embedding_reorders_by_index_and_rejects_nonfinite(self):
        p = OpenAIProvider.from_config(self.config)
        self.reply = {'data': [{'index': 1, 'embedding': [0, 1]}, {'index': 0, 'embedding': [1, 0]}]}
        self.assertEqual(p.embed(['a', 'b']), [[1, 0], [0, 1]])
        self.reply = {'data': [{'index': 0, 'embedding': [float('nan'), 1]}]}
        with self.assertRaises(ProviderError):
            p.embed(['a'])

    def test_remote_data_transfer_requires_explicit_configuration(self):
        cfg = dict(self.config, base_url='https://api.example.com/v1')
        with self.assertRaises(ProviderError):
            OpenAIProvider.from_config(cfg)
        cfg['allow_remote'] = True
        self.assertIsNotNone(OpenAIProvider.from_config(cfg))
        cfg['base_url'] = 'http://api.example.com/v1'
        with self.assertRaises(ProviderError):
            OpenAIProvider.from_config(cfg)

    def test_provider_error_does_not_expose_body_or_key(self):
        p = OpenAIProvider.from_config(self.config)
        self.status = 401
        self.reply = {'error': 'private secret from server'}
        with self.assertRaises(ProviderError) as cm:
            p.embed(['sensitive'])
        self.assertNotIn('private secret', str(cm.exception))

    def test_malformed_extraction_is_rejected_and_unconfigured_is_none(self):
        self.assertIsNone(OpenAIProvider.from_config({}))
        p = OpenAIProvider.from_config(self.config)
        self.reply = {'choices': [{'message': {'content': '{not json'}}]}
        with self.assertRaises(ProviderError):
            p.extract({'content': 'a', 'speaker': 'user'})


if __name__ == '__main__':
    unittest.main()
