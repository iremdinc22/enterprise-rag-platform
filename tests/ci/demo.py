"""CI-only Docker lifecycle. No application feature changes or private env loading.

All reports are constructed from explicit fields. Raw subprocess output, resolved
Compose files and runtime credentials stay private and are never artifacts.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

MAX_ARTIFACT_BYTES = 50_000_000
TARGET_ARTIFACT_BYTES = 30_000_000

ARTIFACT_NAMES = {
    'controlled.json', 'http.json', 'authorization.json', 'provenance.json',
    'resources.json', 'stages.json', 'browser-empty.json', 'browser-workflow.json',
    'login.png', 'workspace-overview.png', 'document-library.png',
    'answer-citations.png', 'mobile-dashboard.png', 'mobile-answer.png',
    'tablet-dashboard.png', 'desktop-1280.png',
}


class CheckFailed(RuntimeError):
    """Messages are static categories, never upstream payloads."""


def private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)


def read_json(path):
    return json.loads(Path(path).read_text())


def location():
    if os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('RUNNER_OS') != 'Linux':
        raise CheckFailed('GitHub Actions Linux runner required')
    run = os.getenv('GITHUB_RUN_ID', '') + '-' + os.getenv('GITHUB_RUN_ATTEMPT', '')
    if re.fullmatch(r'[0-9]+-[0-9]+', run) is None:
        raise CheckFailed('Invalid run identity')
    base = Path(os.environ['RUNNER_TEMP']).resolve()
    return base / ('atlas-validation-' + run), run


def settings():
    root, run = location()
    state = read_json(root / 'state.json')
    if state['run'] != run or state['workspace'] != str(Path.cwd().resolve()):
        raise CheckFailed('Invalid ownership marker')
    return root, state


def output(root, name, data):
    if name not in ARTIFACT_NAMES or not name.endswith('.json'):
        raise CheckFailed('Unapproved report')
    (root / 'artifacts' / name).write_text(json.dumps(data, indent=2) + '\n')


def resource_sample():
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        memory[key] = int(value.split()[0]) * 1024
    return {'elapsed_seconds': round(time.monotonic() - settings()[1]['started_monotonic'], 1),
            'available_memory_bytes': memory['MemAvailable'],
            'swap_used_bytes': memory['SwapTotal'] - memory['SwapFree'],
            'free_disk_bytes': shutil.disk_usage(Path.cwd()).free}


def monitor():
    root, _ = settings()
    low_since = None
    while not (root / 'monitor-stop').exists():
        sample = resource_sample()
        with (root / 'resource-samples.jsonl').open('a') as stream:
            stream.write(json.dumps(sample) + '\n')
        heartbeat = root / 'resource-heartbeat.tmp'
        heartbeat.write_text(str(time.monotonic()))
        heartbeat.replace(root / 'resource-heartbeat')
        low = sample['available_memory_bytes'] < 768 * 1024**2
        low_since = low_since or time.monotonic() if low else None
        if sample['free_disk_bytes'] < 1024**3 or (low_since and time.monotonic() - low_since >= 15):
            (root / 'resource-stop').touch()
            return
        time.sleep(5)


def monitor_failed(root):
    if not (root / 'monitor-required').exists():
        return False
    try:
        return time.monotonic() - float((root / 'resource-heartbeat').read_text()) > 20
    except (OSError, ValueError):
        return True


def command(args, *, env=None, timeout=300, check=True, ignore_resource_stop=False):
    root, state = settings()
    if not ignore_resource_stop and time.monotonic() >= state['deadline']:
        raise CheckFailed('Validation execution budget exhausted')
    if not ignore_resource_stop and ((root / 'resource-stop').exists() or monitor_failed(root)):
        raise CheckFailed('Resource safety stop or monitor unavailable')
    # A private temporary log avoids deadlock and never enters published artifacts.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, stdout=log, stderr=log, env=env, start_new_session=True)
        deadline = time.monotonic() + timeout
        if not ignore_resource_stop:
            deadline = min(deadline, state['deadline'])
        while process.poll() is None:
            if time.monotonic() > deadline or (not ignore_resource_stop and ((root / 'resource-stop').exists() or monitor_failed(root))):
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise CheckFailed('Command deadline or resource stop')
            time.sleep(0.2)
        log.seek(0)
        text = log.read().decode('utf-8', errors='replace')
    if check and process.returncode:
        raise CheckFailed('Command failed')
    return process.returncode, text


def compose(kind, *args, **kwargs):
    root, state = settings()
    return command(['docker', 'compose', '--env-file', '/dev/null', '-p', state[kind + '_project'],
                    '-f', str(root / (kind + '.json')), *args], **kwargs)


def validate_compose(config, project, demo):
    services = config['services']
    expected = {'api', 'worker', 'frontend', 'redis', 'qdrant'} if demo else {'e2e', 'worker', 'redis', 'qdrant'}
    if set(services) != expected:
        raise CheckFailed('Unexpected services')
    for name, service in services.items():
        if any(v.get('type') == 'bind' for v in service.get('volumes', [])):
            raise CheckFailed('Host-data mounts forbidden')
        ports = service.get('ports', [])
        if demo and name == 'frontend':
            if len(ports) != 1 or ports[0]['host_ip'] != '127.0.0.1' or str(ports[0]['published']) != '18030':
                raise CheckFailed('Unexpected frontend exposure')
        elif ports:
            raise CheckFailed('Backend exposure forbidden')
        if service.get('privileged'):
            raise CheckFailed('Privileged service forbidden')
    for value in config.get('volumes', {}).values():
        if value.get('external') or not value['name'].startswith(project + '_'):
            raise CheckFailed('Unowned volume')
    network = 'demo' if demo else 'isolated'
    if not config['networks'][network].get('internal'):
        raise CheckFailed('Internal network required')
    if demo:
        for name in ('api', 'worker'):
            e = services[name]['environment']
            if (e.get('DEMO_PROVIDER_MODE') != 'deterministic' or e.get('OPENAI_API_KEY')
                    or e.get('QDRANT_URL') != 'http://qdrant:6333'
                    or e.get('REDIS_CACHE_URL') != 'redis://redis:6379/2'
                    or set(services[name]['networks']) != {'demo'}):
                raise CheckFailed('Unsafe demo provider configuration')


def prepare():
    root, run = location()
    root.mkdir(mode=0o700)  # Refuse reuse, including accidental reruns of setup.
    (root / 'artifacts').mkdir()
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if sha != os.environ['GITHUB_SHA']:
        raise CheckFailed('Checkout SHA mismatch')
    state = {'run': run, 'workspace': str(Path.cwd().resolve()), 'sha': sha,
             'deadline': time.monotonic() + 50 * 60, 'started_monotonic': time.monotonic(),
             'controlled_project': 'atlas-controlled-' + run, 'demo_project': 'atlas-demo-' + run,
             'app_image': 'atlas-validation-app:' + run, 'frontend_image': 'atlas-validation-frontend:' + run}
    private_json(root / 'state.json', state)
    sample = resource_sample()
    if sample['available_memory_bytes'] < 6 * 1024**3 or sample['free_disk_bytes'] < 10 * 1024**3:
        raise CheckFailed('Insufficient runner resources')
    command(['docker', 'info'], timeout=30)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 18030))
    # Cryptographically random temporary credentials; no repository secrets needed.
    credentials = {name: secrets.token_urlsafe(24) for name in ('acme-demo', 'globex-demo')}
    signing_secret = secrets.token_urlsafe(48)
    from src.demo.passwords import hash_password
    values = {'DEMO_JWT_SECRET': signing_secret, 'DEMO_PROVIDER_MODE': 'deterministic', 'DEMO_PORT': '18030',
              'DEMO_ACME_PASSWORD_HASH': hash_password(credentials['acme-demo']),
              'DEMO_GLOBEX_PASSWORD_HASH': hash_password(credentials['globex-demo'])}
    private_json(root / 'credentials.json', credentials)
    private_json(root / 'signing.json', {'secret': signing_secret})
    (root / 'fixtures').mkdir()
    from tests.demo_e2e import synthetic_pdf
    fixtures = {}
    for tenant, marker, amount in [('acme', 'Beacon', '419'), ('globex', 'Comet', '862')]:
        name = f'SYNTHETIC-{tenant}-{marker.lower()}.pdf'
        data = synthetic_pdf(tenant, marker, amount)
        (root / 'fixtures' / name).write_bytes(data)
        fixtures[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'tenant': tenant}
    private_json(root / 'fixtures.json', fixtures)
    # Render privately from committed files; image tags only are changed.
    for kind, source in [('controlled', 'compose.e2e.yaml'), ('demo', 'compose.demo.yaml')]:
        _, raw = command(['docker', 'compose', '--env-file', '/dev/null', '-p', state[kind + '_project'],
                          '-f', source, 'config', '--format', 'json'], env={**os.environ, **values})
        config = json.loads(raw)
        for name, service in config['services'].items():
            if name in ('api', 'worker', 'e2e'):
                service['image'] = state['app_image']
            if name == 'frontend':
                service['image'] = state['frontend_image']
            service.pop('build', None)
        validate_compose(config, state[kind + '_project'], kind == 'demo')
        private_json(root / (kind + '.json'), config)
        compose(kind, 'config', '--quiet')
    # No overwrite or reuse of existing resources, even on a nonstandard runner.
    for kind in ('controlled', 'demo'):
        project = state[kind + '_project']
        for resource in ('container', 'volume', 'network'):
            _, names = command(['docker', resource, 'ls', '-aq' if resource == 'container' else '-q', '--filter', 'label=com.docker.compose.project=' + project])
            if names.strip():
                raise CheckFailed('Project already owns resources')
    for image in (state['app_image'], state['frontend_image']):
        code, _ = command(['docker', 'image', 'inspect', image], check=False)
        if code == 0:
            raise CheckFailed('Image tag already exists')
    (root / 'ownership-confirmed').touch()
    watcher = subprocess.Popen([sys.executable, '-m', 'tests.ci.demo', 'monitor'], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 10
    while not (root / 'resource-heartbeat').exists():
        if watcher.poll() is not None or time.monotonic() > deadline:
            raise CheckFailed('Resource monitor startup failed')
        time.sleep(0.1)
    (root / 'monitor-required').touch()


def build():
    _, state = settings()
    for tag, dockerfile in [(state['app_image'], 'Dockerfile'), (state['frontend_image'], 'frontend/Dockerfile')]:
        print('Building ' + ('frontend' if dockerfile.startswith('frontend') else 'application'), flush=True)
        command(['docker', 'build', '--pull', '--no-cache', '--label', 'org.opencontainers.image.revision=' + state['sha'],
                 '-t', tag, '-f', dockerfile, '.'], timeout=900 if dockerfile == 'Dockerfile' else 360)


def verify_runtime(kind):
    root, state = settings()
    project = state[kind + '_project']
    network = project + ('_demo' if kind == 'demo' else '_isolated')
    _, data = command(['docker', 'network', 'inspect', network])
    if not json.loads(data)[0]['Internal']:
        raise CheckFailed('Runtime network not internal')
    _, text = compose(kind, 'ps', '-q')
    for identifier in text.split():
        _, raw = command(['docker', 'inspect', identifier]); d = json.loads(raw)[0]
        if d['Config']['Labels'].get('com.docker.compose.project') != project:
            raise CheckFailed('Container ownership mismatch')
        service = d['Config']['Labels']['com.docker.compose.service']
        ports = d['HostConfig'].get('PortBindings') or {}
        if service != 'frontend' and ports:
            raise CheckFailed('Unexpected runtime port')
        if service == 'frontend' and ports != {'5173/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18030'}]}:
            raise CheckFailed('Unexpected frontend runtime binding')
        if kind == 'demo':
            for mount in d['Mounts']:
                if mount['Type'] != 'volume' or not mount.get('Name', '').startswith(project + '_'):
                    raise CheckFailed('Runtime mount ownership mismatch')
                if service == 'worker' and mount['Destination'] == '/demo/uploads' and mount['RW']:
                    raise CheckFailed('Worker upload mount must be read-only')
        if kind == 'controlled' and any(m['Type'] in ('bind', 'volume') for m in d['Mounts']):
            raise CheckFailed('Controlled stack has persistent mounts')
        if kind == 'demo' and service in ('api', 'worker', 'frontend'):
            tag = state['frontend_image'] if service == 'frontend' else state['app_image']
            _, raw_image = command(['docker', 'image', 'inspect', tag]); image = json.loads(raw_image)[0]
            if d['Image'] != image['Id'] or image['Config']['Labels']['org.opencontainers.image.revision'] != state['sha']:
                raise CheckFailed('Image provenance mismatch')


def controlled():
    root, _ = settings()
    try:
        compose('controlled', 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'qdrant', 'redis', 'worker')
        verify_runtime('controlled')
        code, raw = compose('controlled', 'run', '--rm', '--no-deps', 'e2e', 'python', '-m',
                            'tests.ci.controlled', timeout=300, check=False)
        matches = re.findall(r'^ATLAS_TEST_RESULT=(.+)$', raw, flags=re.M)
        if len(matches) != 1:
            raise CheckFailed('Controlled result unavailable')
        report = json.loads(matches[0]); output(root, 'controlled.json', report)
        if code or report['run'] != 18 or report['passed'] != 18:
            raise CheckFailed('Controlled E2E failed')
    finally:
        compose('controlled', 'down', '--remove-orphans', '--timeout', '20', ignore_resource_stop=True)


def start():
    root, state = settings()
    # The first environment must be gone before starting the demo.
    _, containers = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + state['controlled_project']])
    if containers.strip():
        raise CheckFailed('Controlled environment still present')
    compose('demo', 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'qdrant', 'redis')
    compose('demo', 'run', '--rm', '--no-deps', 'api', 'python', '-m', 'src.demo.run', 'init-collection')
    compose('demo', 'up', '-d', '--no-build', '--wait', '--wait-timeout', '180')
    verify_runtime('demo')
    _, raw = compose('demo', 'ps', '--format', 'json')
    rows = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != 5 or any(row.get('Health') != 'healthy' for row in rows):
        raise CheckFailed('Five healthy services required')
    import httpx
    with httpx.Client(base_url='http://127.0.0.1:18030', trust_env=False) as client:
        if client.get('/health').headers.get('x-demo-provider-mode') != 'deterministic' or client.get('/ready').status_code != 200:
            raise CheckFailed('Demo not ready')
        index = client.get('/').text
        assets = re.findall(r'(?:src|href)="(/assets/[^"?]+)"', index)
        if not assets:
            raise CheckFailed('Frontend assets unavailable')
        hashes = {}
        for asset in assets:
            _, expected = compose('demo', 'exec', '-T', 'frontend', 'sha256sum', '/usr/share/nginx/html' + asset)
            actual = hashlib.sha256(client.get(asset).content).hexdigest()
            if actual != expected.split()[0]:
                raise CheckFailed('Served frontend differs from image')
            hashes[asset] = actual
    images = {}
    for label, tag in [('app', state['app_image']), ('frontend', state['frontend_image'])]:
        _, raw = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]); images[label] = raw.strip()
    output(root, 'provenance.json', {'commit': state['sha'], 'images': images, 'frontend_asset_sha256': hashes,
                                   'healthy_services': 5, 'provider': 'deterministic'})


def jwt_token(secret, tenant='acme', role='admin', expired=False):
    def encode(x):
        return base64.urlsafe_b64encode(json.dumps(x, separators=(',', ':')).encode()).rstrip(b'=')
    now = int(time.time())
    payload = {'sub': 'ci-authorization', 'tenant_id': tenant, 'role': role, 'iat': now - 120,
               'exp': now - 60 if expired else now + 900}
    head = encode({'alg': 'HS256', 'typ': 'JWT'}) + b'.' + encode(payload)
    return (head + b'.' + base64.urlsafe_b64encode(hmac.new(secret.encode(), head, hashlib.sha256).digest()).rstrip(b'=')).decode()


def has_no_store(response):
    return 'no-store' in [part.strip().lower() for part in response.headers.get('cache-control', '').split(',')]


def http_checks():
    root, _ = settings()
    from tests.demo_e2e import run
    output(root, 'http.json', run(read_json(root / 'credentials.json')))
    import httpx
    secret = read_json(root / 'signing.json')['secret']
    checks = []
    credentials = read_json(root / 'credentials.json')
    with httpx.Client(base_url='http://127.0.0.1:18030', timeout=15, trust_env=False) as c:
        for label, body, status in [
            ('missing login fields', {}, 422),
            ('client login claims rejected', {'username': 'acme-demo', 'password': credentials['acme-demo'], 'tenant_id': 'globex'}, 422),
            ('unknown account generic rejection', {'username': 'unknown-demo', 'password': 'invalid-ci-account-password'}, 401),
        ]:
            response = c.post('/api/v1/demo/session', json=body)
            if response.status_code != status or not has_no_store(response):
                raise CheckFailed('Session validation failed')
            if status == 401 and response.json() != {'detail': 'Invalid credentials'}:
                raise CheckFailed('Unsafe login error')
            checks.append(label)
        for label, token in [('expired JWT', jwt_token(secret, expired=True)), ('forged JWT', jwt_token(secret + 'invalid'))]:
            response = c.get('/api/v1/documents', headers={'Authorization': 'Bearer ' + token})
            if response.status_code != 401:
                raise CheckFailed('JWT rejection failed')
            checks.append(label)
        headers = {'Authorization': 'Bearer ' + jwt_token(secret)}
        if c.post('/api/v1/query', headers=headers, json={'query': 'Synthetic', 'tenant_id': 'globex'}).status_code != 422:
            raise CheckFailed('Client tenant override accepted')
        checks.append('client tenant override rejected')
        employee = {'Authorization': 'Bearer ' + jwt_token(secret, role='employee'), 'Idempotency-Key': '00000000-0000-4000-8000-000000000001'}
        if c.post('/api/v1/documents', headers=employee, files={'file': ('invalid.pdf', b'not PDF', 'application/pdf')}).status_code != 403:
            raise CheckFailed('Upload role enforcement failed')
        checks.append('employee upload forbidden')
        if c.get('/api/v1/documents', headers=headers, params={'limit': 51}).status_code != 422:
            raise CheckFailed('Pagination validation failed')
        checks.append('pagination limit enforced')
        response = c.get('/api/v1/documents', headers=headers)
        if response.status_code != 200 or not has_no_store(response):
            raise CheckFailed('Document metadata cache policy failed')
        checks.append('document metadata no-store')
    output(root, 'authorization.json', {'checks': checks})


def browser(phase):
    root, _ = settings()
    command(['npm', '--prefix', 'frontend', 'run', 'test:e2e', '--', '--grep', '@' + phase],
            env={**os.environ, 'ATLAS_CI_DIR': str(root), 'ATLAS_BROWSER_PHASE': phase}, timeout=240)


def cleanup():
    root, state = settings()
    (root / 'monitor-stop').touch()
    try:
        # Check actual runtime secret literals before deleting private configuration.
        literals = []
        if (root / 'credentials.json').exists():
            literals.extend(read_json(root / 'credentials.json').values())
        if (root / 'signing.json').exists():
            literals.append(read_json(root / 'signing.json')['secret'])
        if (root / 'demo.json').exists():
            e = read_json(root / 'demo.json')['services']['api']['environment']
            literals.extend(e.get(k, '') for k in ('DEMO_ACME_PASSWORD_HASH', 'DEMO_GLOBEX_PASSWORD_HASH'))
        for path in (root / 'artifacts').glob('*.json'):
            if any(value and value in path.read_text() for value in literals):
                (root / 'artifact-redaction-failed').touch()
        if not (root / 'ownership-confirmed').exists():
            return
        failures = False
        for kind in ('controlled', 'demo'):
            try:
                compose(kind, 'down', '--remove-orphans', '--timeout', '20', ignore_resource_stop=True)
            except CheckFailed:
                failures = True
        # Explicit per-volume ownership, never down -v or global prune.
        for project in (state['controlled_project'], state['demo_project']):
            _, text = command(['docker', 'volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + project], ignore_resource_stop=True)
            for name in text.split():
                _, raw = command(['docker', 'volume', 'inspect', name], ignore_resource_stop=True)
                volume = json.loads(raw)[0]
                if not name.startswith(project + '_') or volume.get('Labels', {}).get('com.docker.compose.project') != project:
                    raise CheckFailed('Refusing unowned volume cleanup')
                command(['docker', 'volume', 'rm', name], ignore_resource_stop=True)
        for tag in (state['app_image'], state['frontend_image']):
            command(['docker', 'image', 'rm', tag], check=False, ignore_resource_stop=True)
        if failures:
            raise CheckFailed('Scoped container cleanup failed')
    finally:
        for name in ('credentials.json', 'signing.json', 'demo.json', 'controlled.json'):
            (root / name).unlink(missing_ok=True)


def publish():
    root, state = settings()
    if (root / 'artifact-redaction-failed').exists():
        raise CheckFailed('Runtime secret detected in report')
    samples = [json.loads(line) for line in (root / 'resource-samples.jsonl').read_text().splitlines()] if (root / 'resource-samples.jsonl').exists() else []
    output(root, 'resources.json', {'samples': samples, 'safety_stop': (root / 'resource-stop').exists()})
    dest = root / 'publish'
    if dest.exists():
        raise CheckFailed('Publish directory already exists')
    total = 0
    for path in (root / 'artifacts').iterdir():
        if path.name not in ARTIFACT_NAMES or path.is_symlink() or not path.is_file():
            raise CheckFailed('Artifact allowlist violation')
        if total + path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise CheckFailed('Artifact size exceeds maximum')
        data = path.read_bytes(); total += len(data)
        if path.suffix == '.json':
            json.loads(data)
            text = data.decode()
            if re.search(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|scrypt\$131072\$', text):
                raise CheckFailed('Sensitive artifact content')
        elif not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise CheckFailed('Invalid screenshot format')
    if total > MAX_ARTIFACT_BYTES:
        raise CheckFailed('Artifact size exceeds maximum')
    # Stage only after every candidate passed. No logs/config/env files are copied.
    dest.mkdir()
    for path in (root / 'artifacts').iterdir():
        shutil.copyfile(path, dest / path.name)
    print('Allowlisted artifacts: ' + str(total) + ' bytes; below target: ' + str(total < TARGET_ARTIFACT_BYTES))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'build', 'controlled', 'start', 'http', 'browser-empty', 'browser-workflow', 'cleanup', 'publish', 'monitor'])
    phase = parser.parse_args().phase
    if phase == 'monitor':
        monitor(); return
    started = time.monotonic()
    ok = False
    try:
        if phase.startswith('browser-'):
            browser(phase.split('-', 1)[1])
        else:
            globals()[{'http': 'http_checks'}.get(phase, phase)]()
        ok = True
    except Exception as error:
        # Never print subprocess output, error reprs, credentials or request bodies.
        print('CI stage failed: ' + phase + ' (' + type(error).__name__ + ')', flush=True)
        raise SystemExit(1) from None
    finally:
        try:
            root, _ = settings()
            path = root / 'artifacts' / 'stages.json'
            stages = read_json(path) if path.exists() else []
            stages.append({'phase': phase, 'passed': ok, 'duration_seconds': round(time.monotonic() - started, 2)})
            output(root, 'stages.json', stages)
        except Exception:
            pass


if __name__ == '__main__':
    main()
