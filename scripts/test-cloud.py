#!/usr/bin/env python3
"""Build and test an isolated local Worker; delete all test data on exit."""
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

root = Path(__file__).resolve().parent.parent
uv = shutil.which('uv') or str(root / 'cloud/.venv/bin/uv')
os.environ['PATH'] = str(Path(uv).parent) + os.pathsep + os.environ['PATH']
subprocess.run([sys.executable, str(root / 'cloud/build.py')], check=True)
# Resolve once before selecting the port; lockfiles keep the deployment repeatable.
subprocess.run([uv, 'sync', '--locked'], cwd=root / 'cloud', check=True)
subprocess.run(['npm', 'ci', '--ignore-scripts'], cwd=root / 'cloud', check=True, stdout=subprocess.DEVNULL)
with tempfile.TemporaryDirectory(prefix='bigfeels-cloud-test-') as temporary:
    work = Path(temporary)
    shutil.copytree(root / 'cloud/src', work / 'src')
    shutil.copy2(root / 'cloud/pyproject.toml', work / 'pyproject.toml')
    shutil.copy2(root / 'cloud/uv.lock', work / 'uv.lock')
    shutil.copy2(root / 'cloud/package.json', work / 'package.json')
    (work / 'node_modules').symlink_to(root / 'cloud/node_modules', target_is_directory=True)
    config = json.loads((root / 'cloud/wrangler.jsonc').read_text())
    config['name'] = 'bigfeels-test'
    (work / 'wrangler.jsonc').write_text(json.dumps(config))
    password = secrets.token_urlsafe(32)
    (work / '.dev.vars').write_text('ADMIN_PASSWORD=' + password + '\n')
    (work / 'secret').write_text(password)
    (work / '.dev.vars').chmod(0o600)
    (work / 'secret').chmod(0o600)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    with (work / 'runtime.log').open('w+') as log:
        process = subprocess.Popen([uv, 'run', '--locked', 'pywrangler', 'dev', '--port', str(port), '--ip', '127.0.0.1', '--test-scheduled'],
                                   cwd=work, stdout=log, stderr=log, start_new_session=True)
        try:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError('Workers runtime exited during startup')
                try:
                    with urllib.request.urlopen(url + '/v1/info', timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError('Workers runtime did not become ready')
            env = {**os.environ, 'PYTHONPATH': str(root / 'src'), 'BIGFEELS_CLOUD_TEST_URL': url,
                   'BIGFEELS_CLOUD_TEST_SECRET_FILE': str(work / 'secret')}
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_cloud_runtime.py', '-v'], cwd=root, env=env)
            if result.returncode:
                raise RuntimeError('Cloud runtime integration tests failed')
            log.flush()
            runtime_output = (work / 'runtime.log').read_text()
            if 'PythonError' in runtime_output or 'Uncaught' in runtime_output:
                raise RuntimeError('Workers runtime reported an unhandled exception')
        except Exception:
            log.flush()
            print((work / 'runtime.log').read_text()[-8000:], file=sys.stderr)
            raise
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
