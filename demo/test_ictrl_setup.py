"""ictrl_setup 测试。Apple 接口和手机是本地假的，openssl 是真的；不代表真实账号或设备已跑通。"""
import base64
import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

import ictrl_setup

OPENSSL = 'openssl'
CERT_DER = b'fake-certificate-der'
UDID = '00008140-001A2B3C4D5E801C'


class FakeAsc:
    def __init__(self):
        self.calls = []
        self.tokens = []
        self.devices = []
        self.bundles = []
        self.certs = []
        self.profiles = []
        self.fail = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def handle_any(self, method):
                size = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(size)) if size else None
                owner.tokens.append(self.headers['Authorization'].removeprefix('Bearer '))
                url = urlparse(self.path)
                owner.calls.append((method, url.path, parse_qs(url.query), body))
                if (method, url.path) in owner.fail:
                    payload = json.dumps({'errors': [{'detail': owner.fail[(method, url.path)]}]}).encode()
                    self.send_response(409)
                else:
                    payload = json.dumps(owner.respond(method, url.path, body, parse_qs(url.query))).encode()
                    self.send_response(201 if method == 'POST' else 200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self): self.handle_any('GET')
            def do_POST(self): self.handle_any('POST')
            def do_DELETE(self): self.handle_any('DELETE')

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.http.server_port}/v1'
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def respond(self, method, path, body, query):
        kind = path.split('/')[2]
        store = {'devices': self.devices, 'bundleIds': self.bundles, 'certificates': self.certs, 'profiles': self.profiles}[kind]
        if method == 'GET':
            if kind == 'devices' and 'filter[status]' in query:
                return {'data': [d for d in store if d['attributes'].get('status') in query['filter[status]']]}
            if kind == 'devices' and 'filter[udid]' in query:
                wanted = query['filter[udid]']
                return {'data': [d for d in store if d['attributes'].get('udid', wanted[0]) in wanted]}
            return {'data': store}
        if method == 'DELETE':
            store[:] = [item for item in store if item['id'] != path.split('/')[3]]
            return {}
        attributes = dict(body['data']['attributes'])
        if kind == 'devices':
            attributes.update(status='ENABLED', deviceClass='IPHONE')
        if kind == 'certificates':
            attributes['certificateContent'] = base64.b64encode(self.real_cert(attributes['csrContent'])).decode()
        if kind == 'profiles':
            attributes['profileContent'] = base64.b64encode(b'profile-with-device-id').decode()
        item = {'id': f'{kind}-{len(store) + 1}', 'attributes': attributes}
        store.append(item)
        return {'data': item}

    def real_cert(self, csr):
        """像 Apple 一样用 CSR 里的公钥签发，这样 p12 导出才对得上私钥。"""
        with tempfile.TemporaryDirectory() as tmp:
            ca, ca_cert, request, der = (os.path.join(tmp, n) for n in ('ca', 'ca.pem', 'r.csr', 'c.der'))
            with open(request, 'w') as handle:
                handle.write(csr)
            subprocess.run([OPENSSL, 'genrsa', '-out', ca, '2048'], check=True, capture_output=True)
            subprocess.run([OPENSSL, 'req', '-x509', '-key', ca, '-subj', '/CN=Fake CA', '-out', ca_cert],
                           check=True, capture_output=True)
            subprocess.run([OPENSSL, 'x509', '-req', '-in', request, '-CA', ca_cert, '-CAkey', ca, '-CAcreateserial',
                            '-days', '1', '-outform', 'DER', '-out', der], check=True, capture_output=True)
            with open(der, 'rb') as handle:
                return handle.read()

    def posts(self, kind):
        return [c for c in self.calls if c[0] == 'POST' and c[1] == '/v1/' + kind]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = os.path.join(self.tmp.name, 'AuthKey_TEST.p8')
        subprocess.run([OPENSSL, 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', self.key],
                       check=True, capture_output=True)
        self.state = os.path.join(self.tmp.name, 'state')
        self.keychain = set()
        self.commands = []
        self.asc = FakeAsc()
        self.addCleanup(self.asc.http.server_close)
        self.addCleanup(self.asc.http.shutdown)
        for patch in (mock.patch.object(ictrl_setup, 'STATE_DIR', self.state),
                      mock.patch.object(ictrl_setup, 'OPENSSL', OPENSSL),
                      mock.patch.object(ictrl_setup, 'run', self.fake_run)):
            patch.start()
            self.addCleanup(patch.stop)

    def fake_run(self, cmd, *, input=None, check=True):
        self.commands.append(cmd)
        name = os.path.basename(cmd[0])
        if name == OPENSSL:
            return subprocess.run(cmd, input=input, capture_output=True, check=True)
        if name == 'security' and cmd[1] == 'find-identity':
            lines = ''.join(f'  1) {sha} "Apple Development: x"\n' for sha in sorted(self.keychain))
            return subprocess.CompletedProcess(cmd, 0, lines.encode(), b'')
        if name == 'security' and cmd[1] == 'import' and cmd[2].endswith('.p12'):
            # 真实钥匙串会把证书变成身份；用 p12 里的证书算指纹。
            out = subprocess.run([OPENSSL, 'pkcs12', '-in', cmd[2], '-passin', 'pass:' + cmd[cmd.index('-P') + 1],
                                  '-nokeys', '-clcerts'], capture_output=True, check=True).stdout
            der = subprocess.run([OPENSSL, 'x509', '-outform', 'DER'], input=out, capture_output=True, check=True).stdout
            self.keychain.add(hashlib.sha1(der).hexdigest().upper())
        return subprocess.CompletedProcess(cmd, 0, b'', b'')

    def provision(self, *extra):
        argv = ['provision', '--key-file', self.key, '--key-id', 'KEY1', '--issuer-id', 'ISS1',
                '--udid', UDID, '--yes', '--api-base', self.asc.base, *extra]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ictrl_setup.main(argv)
        return code, out.getvalue()


class Token(Base):
    def test_jwt_is_valid_es256(self):
        token = ictrl_setup.make_token(self.key, 'KEY1', 'ISS1', now=1000)
        head, body, signature = token.split('.')
        pad = lambda s: base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
        self.assertEqual(json.loads(pad(head)), {'alg': 'ES256', 'kid': 'KEY1', 'typ': 'JWT'})
        claims = json.loads(pad(body))
        self.assertEqual((claims['iss'], claims['aud'], claims['exp'] - claims['iat']), ('ISS1', 'appstoreconnect-v1', 900))
        raw = pad(signature)
        self.assertEqual(len(raw), 64)
        # 转回 DER 用 openssl 验证，确认 r||s 没有弄错。
        def integer(value):
            value = value.lstrip(b'\0') or b'\0'
            if value[0] & 0x80: value = b'\0' + value
            return bytes([2, len(value)]) + value
        seq = integer(raw[:32]) + integer(raw[32:])
        der = bytes([0x30, len(seq)]) + seq
        with tempfile.TemporaryDirectory() as tmp:
            sig, pub = os.path.join(tmp, 's'), os.path.join(tmp, 'p')
            open(sig, 'wb').write(der)
            subprocess.run([OPENSSL, 'pkey', '-in', self.key, '-pubout', '-out', pub], check=True, capture_output=True)
            result = subprocess.run([OPENSSL, 'dgst', '-sha256', '-verify', pub, '-signature', sig],
                                    input=f'{head}.{body}'.encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class Provision(Base):
    def test_first_run_creates_everything_and_keeps_no_key_on_disk(self):
        code, output = self.provision()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.asc.posts('devices')[0][3]['data']['attributes']['udid'], UDID)
        self.assertEqual(self.asc.posts('bundleIds')[0][3]['data']['attributes']['identifier'], '*')
        self.assertEqual(self.asc.posts('certificates')[0][3]['data']['attributes']['certificateType'], 'DEVELOPMENT')
        relations = self.asc.posts('profiles')[0][3]['data']['relationships']
        self.assertEqual(relations['devices']['data'][0]['id'], 'devices-1')
        self.assertEqual(relations['certificates']['data'][0]['id'], 'certificates-1')
        self.assertEqual(self.asc.posts('profiles')[0][3]['data']['attributes']['profileType'], 'IOS_APP_DEVELOPMENT')
        path = os.path.join(self.state, 'phone-pilot.mobileprovision')
        self.assertEqual(open(path, 'rb').read(), b'profile-with-device-id')
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(os.listdir(self.state), ['phone-pilot.mobileprovision'])
        self.assertNotIn(UDID, output)
        self.assertNotIn('KEY1', output)
        self.assertNotIn(open(self.key).read()[:60], output)

    def test_profile_keeps_phones_already_registered_and_leaves_out_others(self):
        self.asc.devices += [
            {'id': 'old-phone', 'attributes': {'udid': 'OLD', 'status': 'ENABLED', 'deviceClass': 'IPHONE'}},
            {'id': 'off-phone', 'attributes': {'udid': 'OFF', 'status': 'DISABLED', 'deviceClass': 'IPHONE'}},
            {'id': 'watch', 'attributes': {'udid': 'W', 'status': 'ENABLED', 'deviceClass': 'APPLE_WATCH'}},
        ]
        code, output = self.provision()
        self.assertEqual(code, 0, output)
        listed = self.asc.posts('profiles')[0][3]['data']['relationships']['devices']['data']
        self.assertEqual({d['id'] for d in listed}, {'old-phone', 'devices-4'})
        self.assertIn('2 台设备', output)

    def test_second_run_reuses_and_replaces_only_its_own_profile(self):
        self.assertEqual(self.provision()[0], 0)
        self.asc.profiles.append({'id': 'other', 'attributes': {'name': 'Someone Else'}})
        self.assertEqual(self.provision()[0], 0)
        self.assertEqual(len(self.asc.posts('devices')), 1)
        self.assertEqual(len(self.asc.posts('bundleIds')), 1)
        self.assertEqual(len(self.asc.posts('certificates')), 1)
        deletes = [c[1] for c in self.asc.calls if c[0] == 'DELETE']
        self.assertEqual(deletes, ['/v1/profiles/profiles-1'])
        self.assertIn('other', [p['id'] for p in self.asc.profiles])

    def test_existing_cert_not_in_keychain_is_not_reused(self):
        self.asc.certs.append({'id': 'remote', 'attributes': {'certificateContent': base64.b64encode(CERT_DER).decode()}})
        self.assertEqual(self.provision()[0], 0)
        self.assertEqual(len(self.asc.posts('certificates')), 1)

    def test_cert_in_keychain_is_reused(self):
        self.keychain.add(hashlib.sha1(CERT_DER).hexdigest().upper())
        self.asc.certs.append({'id': 'mine', 'attributes': {'certificateContent': base64.b64encode(CERT_DER).decode()}})
        self.assertEqual(self.provision()[0], 0)
        self.assertEqual(self.asc.posts('certificates'), [])
        self.assertEqual(self.asc.posts('profiles')[0][3]['data']['relationships']['certificates']['data'][0]['id'], 'mine')

    def test_declining_changes_nothing(self):
        argv = ['provision', '--key-file', self.key, '--key-id', 'K', '--issuer-id', 'I', '--udid', UDID,
                '--api-base', self.asc.base]
        with mock.patch('builtins.input', return_value='n'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ictrl_setup.main(argv), 1)
        self.assertEqual([c for c in self.asc.calls if c[0] != 'GET'], [])

    def test_apple_error_is_shown_and_no_profile_written(self):
        self.asc.fail[('POST', '/v1/bundleIds')] = 'identifier is invalid'
        code, output = self.provision()
        self.assertEqual(code, 1)
        self.assertIn('identifier is invalid', output)
        self.assertEqual(self.asc.posts('profiles'), [])
        self.assertFalse(os.path.exists(os.path.join(self.state, 'phone-pilot.mobileprovision')))

    def test_identity_rejected_by_codesign_stops_before_profile(self):
        self.asc.certs.append({'id': 'remote', 'attributes': {'certificateContent': base64.b64encode(CERT_DER).decode()}})
        with mock.patch.object(ictrl_setup, 'create_certificate', return_value=('c', 'NOTINKEYCHAIN')):
            code, output = self.provision()
        self.assertEqual(code, 1)
        self.assertIn('codesign', output)
        self.assertEqual(self.asc.posts('profiles'), [])

    def test_disabled_device_stops(self):
        self.asc.devices.append({'id': 'd', 'attributes': {'status': 'DISABLED'}})
        code, output = self.provision()
        self.assertEqual(code, 1)
        self.assertIn('停用', output)

    def test_der_with_leading_zero_and_short_values(self):
        der = bytes([0x30, 0x27, 2, 0x21, 0]) + b'\x80' * 32 + bytes([2, 2]) + b'\x01\x02'
        raw = ictrl_setup.der_to_raw(der)
        self.assertEqual(raw, b'\x80' * 32 + b'\0' * 30 + b'\x01\x02')


class CiProvision(Base):
    def test_prefix_wildcard_and_temporary_keychain(self):
        with mock.patch.dict(os.environ, {'ICTRL_KEYCHAIN': '/tmp/ci.keychain'}):
            self.assertEqual(self.provision('--app-id', 'io.tikpal.*')[0], 0)
        self.assertEqual(self.asc.posts('bundleIds')[0][3]['data']['attributes']['identifier'], 'io.tikpal.*')
        imports = [c for c in self.commands if c[:2] == ['security', 'import']]
        self.assertTrue(imports)
        self.assertTrue(all(c[c.index('-k') + 1] == '/tmp/ci.keychain' for c in imports))

    def test_exports_new_certificate_with_given_password(self):
        out = os.path.join(self.tmp.name, 'dev.p12')
        with mock.patch.dict(os.environ, {'ICTRL_P12_PASSWORD': 'pw123'}):
            self.assertEqual(self.provision('--export-p12', out)[0], 0)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        subprocess.run([OPENSSL, 'pkcs12', '-in', out, '-passin', 'pass:pw123', '-noout'], check=True, capture_output=True)

    def test_reused_certificate_exports_nothing(self):
        self.keychain.add(hashlib.sha1(CERT_DER).hexdigest().upper())
        self.asc.certs.append({'id': 'mine', 'attributes': {'certificateContent': base64.b64encode(CERT_DER).decode()}})
        out = os.path.join(self.tmp.name, 'dev.p12')
        self.assertEqual(self.provision('--export-p12', out)[0], 0)
        self.assertFalse(os.path.exists(out))

    def test_export_without_password_stops_before_creating(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('ICTRL_P12_PASSWORD', None)
            code, output = self.provision('--export-p12', os.path.join(self.tmp.name, 'x'))
        self.assertEqual(code, 1)
        self.assertIn('ICTRL_P12_PASSWORD', output)
        self.assertEqual(self.asc.posts('profiles'), [])


class InstallCi(Base):
    def setUp(self):
        super().setUp()
        self.password = os.path.join(self.tmp.name, 'pass')
        with open(self.password, 'w') as handle:
            handle.write('artifact-secret\n')
        self.members = {'iCTRLUITests-Runner-signed.ipa': b'runner', 'iCTRL-signed.ipa': b'app'}
        self.runs = [{'databaseId': 42}]
        self.download_ok = True
        self.installed = []
        self.secret_input = None
        for patch in (mock.patch.object(ictrl_setup, 'connected_udid', lambda: UDID),
                      mock.patch.object(ictrl_setup, 'install_on_phone',
                                        lambda p: self.installed.append((os.path.basename(p), open(p, 'rb').read())))):
            patch.start()
            self.addCleanup(patch.stop)

    def fake_run(self, cmd, *, input=None, check=True):
        if cmd[0] != 'gh':
            return super().fake_run(cmd, input=input, check=check)
        self.commands.append(cmd)
        if cmd[1:3] == ['run', 'list']:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.runs).encode(), b'')
        if cmd[1:3] == ['run', 'download']:
            if not self.download_ok:
                return subprocess.CompletedProcess(cmd, 1, b'', b'no artifact')
            target = cmd[cmd.index('-D') + 1]
            plain = os.path.join(target, 'plain.tar')
            with tarfile.open(plain, 'w') as archive:
                for name, content in self.members.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
            subprocess.run([OPENSSL, 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-in', plain, '-out',
                            os.path.join(target, 'ictrl-signed.tar.enc'), '-pass', 'pass:artifact-secret'],
                           check=True, capture_output=True)
            os.remove(plain)
            return subprocess.CompletedProcess(cmd, 0, b'', b'')
        if cmd[1:3] == ['secret', 'set']:
            self.secret_input = (cmd[3], input)
            return subprocess.CompletedProcess(cmd, 0, b'', b'')
        raise AssertionError(cmd)

    def install(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return ictrl_setup.main(['install-ci', '--password-file', self.password]), out.getvalue()

    def test_decrypts_and_installs_private_files_only(self):
        code, output = self.install()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.installed, [('iCTRLUITests-Runner-signed.ipa', b'runner'), ('iCTRL-signed.ipa', b'app')])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, 'iCTRL-signed.ipa')).st_mode), 0o600)
        self.assertIsNone(self.secret_input)

    def test_names_with_dot_slash_prefix_like_tar_of_a_directory(self):
        # 工作流用 tar -C out . 打包，成员名是 ./iCTRL-signed.ipa。
        self.members = {'./iCTRLUITests-Runner-signed.ipa': b'runner', './iCTRL-signed.ipa': b'app',
                        './dev-cert.p12': b'p12-bytes'}
        code, output = self.install()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.installed, [('iCTRLUITests-Runner-signed.ipa', b'runner'), ('iCTRL-signed.ipa', b'app')])
        self.assertEqual(self.secret_input, ('ICTRL_DEV_P12_B64', base64.b64encode(b'p12-bytes')))

    def test_new_certificate_goes_to_repo_secret_and_leaves_no_file(self):
        self.members['dev-cert.p12'] = b'p12-bytes'
        self.assertEqual(self.install()[0], 0)
        self.assertEqual(self.secret_input, ('ICTRL_DEV_P12_B64', base64.b64encode(b'p12-bytes')))
        self.assertFalse(os.path.exists(os.path.join(self.state, 'dev-cert.p12')))
        self.assertFalse(any(b'p12-bytes' in str(c).encode() for c in self.commands))

    def test_unknown_archive_paths_are_ignored(self):
        self.members['../../evil.ipa'] = b'evil'
        self.members['sub/iCTRL-signed.ipa'] = b'nested'
        self.assertEqual(self.install()[0], 0)
        self.assertEqual(len(self.installed), 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'evil.ipa')))

    def test_wrong_password_stops(self):
        with open(self.password, 'w') as handle:
            handle.write('wrong\n')
        self.fake_openssl_failure = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assertEqual(self.installed, [])

    def test_no_successful_run(self):
        self.runs = []
        code, output = self.install()
        self.assertEqual(code, 1)
        self.assertIn('还没有成功', output)

    def test_run_without_signed_artifact_explains_missing_secrets(self):
        self.download_ok = False
        code, output = self.install()
        self.assertEqual(code, 1)
        self.assertIn('IPHONE_UDID', output)
        self.assertEqual(self.installed, [])


class Install(Base):
    def setUp(self):
        super().setUp()
        os.makedirs(self.state, mode=0o700)
        self.profile = os.path.join(self.state, 'phone-pilot.mobileprovision')
        open(self.profile, 'wb').write(b'p')
        self.content = b'ipa-bytes'
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.installed = []
        self.signed = []
        for patch in (mock.patch.object(ictrl_setup, 'connected_udid', lambda: UDID),
                      mock.patch.object(ictrl_setup, 'install_on_phone', self.installed.append),
                      mock.patch.object(ictrl_setup, 'IPAS', {'iCTRLUITests-Runner.ipa': self.digest, 'iCTRL.ipa': self.digest}),
                      mock.patch.object(ictrl_setup.subprocess, 'run', self.fake_sign)):
            patch.start()
            self.addCleanup(patch.stop)
        self.sign_code = 0

    def fake_sign(self, cmd, **kwargs):
        self.signed.append((cmd, kwargs))
        if self.sign_code == 0:
            open(cmd[cmd.index('-o') + 1], 'wb').write(b'signed')
        return subprocess.CompletedProcess(cmd, self.sign_code, b'', 'sign failed'.encode())

    def fake_run(self, cmd, *, input=None, check=True):
        if cmd[0] == 'curl':
            open(cmd[cmd.index('-o') + 1], 'wb').write(self.content)
            return subprocess.CompletedProcess(cmd, 0, b'', b'')
        return super().fake_run(cmd, input=input, check=check)

    def install(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return ictrl_setup.main(['install', *extra]), out.getvalue()

    def test_signs_with_our_profile_and_installs_both(self):
        code, output = self.install()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.installed), 2)
        cmd, kwargs = self.signed[0]
        self.assertEqual(cmd[cmd.index('-p') + 1], self.profile)
        self.assertEqual(kwargs['env']['LC_ALL'], 'C')
        for path in self.installed:
            self.assertTrue(path.startswith(self.state))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_runner_only(self):
        self.assertEqual(self.install('--runner-only')[0], 0)
        self.assertEqual(len(self.installed), 1)

    def test_tampered_download_is_deleted_and_never_signed(self):
        self.content = b'evil'
        code, output = self.install()
        self.assertEqual(code, 1)
        self.assertIn('校验不符', output)
        self.assertEqual(self.signed, [])
        self.assertEqual(self.installed, [])
        self.assertFalse([n for n in os.listdir(self.state) if n.endswith('.ipa')])

    def test_sign_failure_never_installs(self):
        self.sign_code = 1
        code, output = self.install()
        self.assertEqual(code, 1)
        self.assertIn('sign failed', output)
        self.assertEqual(self.installed, [])

    def test_needs_profile_first(self):
        os.remove(self.profile)
        code, output = self.install()
        self.assertEqual(code, 1)
        self.assertIn('provision', output)


class TikpalAppId(unittest.TestCase):
    def asc(self, bundles, capabilities):
        asc = ictrl_setup.Asc('token')
        asc.created = []

        def call(method, path, body=None):
            return {'data': capabilities if 'bundleIdCapabilities' in path else bundles}

        def create(kind, attributes, relationships=None):
            asc.created.append((kind, attributes, relationships))
            return {'id': 'new'}
        asc.call, asc.create = call, create
        return asc

    def test_new_id_gets_the_capability(self):
        asc = self.asc([], [])
        self.assertEqual(asc.ensure_network_extension('io.tikpal.ictrl', 'x'), 'new')
        self.assertEqual([kind for kind, _, _ in asc.created], ['bundleIds', 'bundleIdCapabilities'])
        self.assertEqual(asc.created[1][1], {'capabilityType': 'NETWORK_EXTENSIONS'})

    def test_existing_id_with_the_capability_is_left_alone(self):
        bundles = [{'id': 'b1', 'attributes': {'identifier': 'io.tikpal.ictrl'}}]
        capabilities = [{'attributes': {'capabilityType': 'NETWORK_EXTENSIONS'}}]
        asc = self.asc(bundles, capabilities)
        self.assertEqual(asc.ensure_network_extension('io.tikpal.ictrl', 'x'), 'b1')
        self.assertEqual(asc.created, [])

    def test_a_similar_id_is_not_taken_for_it(self):
        bundles = [{'id': 'b1', 'attributes': {'identifier': 'io.tikpal.ictrl.tunnel'}}]
        asc = self.asc(bundles, [])
        self.assertEqual(asc.ensure_network_extension('io.tikpal.ictrl', 'x'), 'new')


class PinnedRelease(unittest.TestCase):
    def test_sign_script_is_vendored(self):
        self.assertTrue(os.path.isfile(ictrl_setup.SIGN_SCRIPT))


if __name__ == '__main__':
    unittest.main()
