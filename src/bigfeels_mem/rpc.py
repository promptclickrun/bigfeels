"""Private parent/child stdio bridge for native non-Python hosts.

One operation per child. Model calls return to the parent host; provider
credentials never enter this process or bigfeels storage.
"""
import argparse
import json
import sys
import uuid

from .client import ClientError
from .local import LocalClient
from .providers import extraction_messages, parse_extraction, ProviderError


MAX_LINE = 2_000_000


def _read(stream):
    line = stream.readline(MAX_LINE + 1)
    if not line or len(line) > MAX_LINE:
        raise ValueError('Invalid bridge input')
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError('Invalid bridge input')
    return value


def _write(stream, payload):
    stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
    stream.flush()


class _HostExtractor:
    def __init__(self, instream, outstream):
        self.input, self.output = instream, outstream

    def extract(self, evidence):
        identifier = 'extract_' + uuid.uuid4().hex
        _write(self.output, {'id': identifier, 'method': 'extract', 'messages': extraction_messages(evidence)})
        response = _read(self.input)
        if response.get('id') != identifier or response.get('error'):
            raise ProviderError('Host model unavailable')
        return parse_extraction(response.get('result'))


def main(argv=None, instream=None, outstream=None):
    parser = argparse.ArgumentParser(description='Native bigfeels child transport')
    parser.add_argument('--data-dir')
    parser.add_argument('--space', action='append', dest='spaces')
    args = parser.parse_args(argv)
    instream, outstream = instream or sys.stdin, outstream or sys.stdout
    identifier = None
    local = None
    try:
        request = _read(instream)
        identifier = request.get('id')
        if type(identifier) not in (str, int):
            raise ValueError('Invalid request identity')
        operation = request.get('operation')
        payload = request.get('payload', {})
        if not isinstance(operation, str) or not isinstance(payload, dict):
            raise ValueError('Invalid request')
        local = LocalClient(args.data_dir, spaces=args.spaces or ['owner'], name='native-bridge',
                            extractor=_HostExtractor(instream, outstream), auto_process=False)
        if operation == 'process':
            result = {'processed': local.process_pending(payload.get('limit', 8))}
        else:
            result = local.call(operation, payload)
        _write(outstream, {'id': identifier, 'result': result})
        return 0
    except ClientError as error:
        _write(outstream, {'id': identifier, 'error': {'status': error.status, 'message': str(error)}})
        return 1
    except Exception:
        _write(outstream, {'id': identifier, 'error': {'status': 400, 'message': 'Local memory request failed'}})
        return 1
    finally:
        if local:
            local.close()


if __name__ == '__main__':
    raise SystemExit(main())
