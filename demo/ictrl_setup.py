#!/usr/bin/env python3
"""把 iCTRL runner 签名并装到 iPhone（付费开发者账号）。在 Mac 上运行，只用标准库。

    python3 demo/ictrl_setup.py doctor      只读检查
    python3 demo/ictrl_setup.py provision --key-file AuthKey_X.p8 --key-id X --issuer-id Y
                                            在 Apple 账号里准备证书、设备和描述文件
    python3 demo/ictrl_setup.py install     下载、核对、签名并安装
    python3 demo/ictrl_setup.py install-ci  取 GitHub Actions 的构建产物，解密并安装（不需要本机证书）

密钥、签名后的 IPA 和描述文件含设备编号或私钥，只放在 ~/.ictrl/setup，不进仓库。
"""
from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ASC_API = 'https://api.appstoreconnect.apple.com/v1'
OPENSSL = '/usr/bin/openssl'  # 系统自带的；Homebrew 版导出的 p12 可能导入不了钥匙串。
STATE_DIR = os.path.expanduser('~/.ictrl/setup')
CI_REPO = 'jiayouchongmingniao/phone-pilot-ios'
CI_WORKFLOW = 'iCTRL build'
CI_BRANCH = 'ictrl-ci'
CI_ARTIFACT = 'ictrl-signed'
CI_PASSWORD_FILE = os.path.expanduser('~/.ictrl/artifact-pass')
CI_P12_SECRET = 'ICTRL_DEV_P12_B64'
CI_P12_NAME = 'dev-cert.p12'
PROFILE_NAME = 'Phone Pilot iCTRL Dev'
DEVICE_NAME = 'Phone Pilot iPhone'
WILDCARD_NAME = 'Phone Pilot Wildcard'
WWDR_URL = 'https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer'
RELEASE = 'v1.98.16'
RELEASE_URL = f'https://github.com/OpenMinis/iCTRL/releases/download/{RELEASE}/'
IPAS = {
    'iCTRLUITests-Runner.ipa': '67450de1524837b137b1ba0175ed16c8850b951ef0127c7c765aa8f3c4702652',
    'iCTRL.ipa': 'dfbe97c3318ba5f2afa030e4b1f925d5773dbaf52ea911042aaa7cb41f2a9d00',
}
SIGN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'third_party', 'sign-ipa.sh')


class SetupError(Exception):
    pass


def run(cmd, *, input=None, check=True):
    result = subprocess.run(cmd, input=input, capture_output=True)
    if check and result.returncode != 0:
        detail = result.stderr.decode('utf-8', 'replace').strip()[:400]
        raise SetupError(f'{os.path.basename(cmd[0])} 失败：{detail}')
    return result


def state_path(name):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    return os.path.join(STATE_DIR, name)


def mask(udid):
    return udid[:4] + '…' + udid[-4:] if len(udid) > 8 else '…'


# ---------------------------------------------------------------- App Store Connect
def der_to_raw(der):
    """ES256 的 JWT 要 r||s，openssl 输出的是 DER。"""
    if der[0] != 0x30:
        raise SetupError('签名格式无效')
    index = 2 + (der[1] & 0x7f if der[1] & 0x80 else 0)
    parts = []
    for _ in range(2):
        if der[index] != 0x02:
            raise SetupError('签名格式无效')
        size = der[index + 1]
        value = der[index + 2:index + 2 + size]
        parts.append(value.lstrip(b'\0').rjust(32, b'\0'))
        index += 2 + size
    return parts[0] + parts[1]


def make_token(key_file, key_id, issuer_id, now=None):
    def encode(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    now = int(now if now is not None else time.time())
    header = {'alg': 'ES256', 'kid': key_id, 'typ': 'JWT'}
    payload = {'iss': issuer_id, 'iat': now, 'exp': now + 900, 'aud': 'appstoreconnect-v1'}
    signing = encode(json.dumps(header).encode()) + '.' + encode(json.dumps(payload).encode())
    der = run([OPENSSL, 'dgst', '-sha256', '-sign', key_file], input=signing.encode()).stdout
    return signing + '.' + encode(der_to_raw(der))


class Asc:
    def __init__(self, token, base=None):
        self.token, self.base = token, base or ASC_API

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                errors = json.load(exc).get('errors', [])
            except ValueError:
                errors = []
            detail = '；'.join(str(e.get('detail') or e.get('title')) for e in errors) or str(exc.code)
            raise SetupError(f'Apple 拒绝了 {method} {path.split("?")[0]}：{detail}') from None
        except urllib.error.URLError as exc:
            raise SetupError(f'连不上 App Store Connect：{exc.reason}（需要代理的话先设置 HTTPS_PROXY）') from None
        return json.loads(raw) if raw else {}

    def create(self, kind, attributes, relationships=None):
        data = {'type': kind, 'attributes': attributes}
        if relationships:
            data['relationships'] = relationships
        return self.call('POST', '/' + kind, {'data': data})['data']

    def ensure_device(self, udid):
        query = urllib.parse.urlencode({'filter[udid]': udid, 'filter[platform]': 'IOS'})
        for device in self.call('GET', '/devices?' + query)['data']:
            if device['attributes'].get('status') == 'DISABLED':
                raise SetupError('这台 iPhone 在账号里被停用了，请先在开发者网站启用')
            return device['id'], False
        made = self.create('devices', {'name': DEVICE_NAME, 'platform': 'IOS', 'udid': udid})
        return made['id'], True

    def ensure_wildcard(self, identifier='*'):
        query = urllib.parse.urlencode({'filter[identifier]': identifier, 'limit': 200})
        for item in self.call('GET', '/bundleIds?' + query)['data']:
            if item['attributes'].get('identifier') == identifier:
                return item['id'], False
        made = self.create('bundleIds', {'identifier': identifier, 'name': WILDCARD_NAME, 'platform': 'IOS'})
        return made['id'], True

    def development_certs(self):
        query = urllib.parse.urlencode({'filter[certificateType]': 'DEVELOPMENT,IOS_DEVELOPMENT', 'limit': 200})
        return self.call('GET', '/certificates?' + query)['data']

    def replace_profile(self, bundle_id, cert_id, device_id):
        # 描述文件不能改，只替换本工具建的这一个，账号里其他的不碰。
        query = urllib.parse.urlencode({'filter[name]': PROFILE_NAME})
        for old in self.call('GET', '/profiles?' + query)['data']:
            if old['attributes'].get('name') == PROFILE_NAME:
                self.call('DELETE', '/profiles/' + old['id'])
        return self.create('profiles', {'name': PROFILE_NAME, 'profileType': 'IOS_APP_DEVELOPMENT'}, {
            'bundleId': {'data': {'type': 'bundleIds', 'id': bundle_id}},
            'certificates': {'data': [{'type': 'certificates', 'id': cert_id}]},
            'devices': {'data': [{'type': 'devices', 'id': device_id}]}})


# ---------------------------------------------------------------- 钥匙串
def keychain_path():
    # CI 用临时钥匙串。
    return os.environ.get('ICTRL_KEYCHAIN') or os.path.expanduser('~/Library/Keychains/login.keychain-db')


def keychain_identities():
    out = run(['security', 'find-identity', '-v', '-p', 'codesigning']).stdout.decode('utf-8', 'replace')
    return set(re.findall(r'\)\s+([0-9A-F]{40})\s', out))


def cert_sha1(content_b64):
    return hashlib.sha1(base64.b64decode(content_b64)).hexdigest().upper()


def import_wwdr():
    """没装 Xcode 的 Mac 常缺这张中间证书，缺了 codesign 不认签名身份。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'wwdr.cer')
        run(['curl', '-fsSL', '-o', path, WWDR_URL])
        # 已存在时 security 返回非 0，无害。是否有效看后面 find-identity 的结果。
        run(['security', 'import', path, '-k', keychain_path()], check=False)


def create_certificate(asc, export_p12=None):
    """私钥只在临时目录和钥匙串里，导入后不留文件。

    export_p12 只给 CI 用：runner 用完即弃，要把新证书带出去才能下次复用，否则证书会越积越多。
    """
    with tempfile.TemporaryDirectory() as tmp:
        key, csr, cer, pem, p12 = (os.path.join(tmp, n) for n in ('k.pem', 'c.csr', 'c.cer', 'c.pem', 'c.p12'))
        run([OPENSSL, 'genrsa', '-out', key, '2048'])
        run([OPENSSL, 'req', '-new', '-key', key, '-subj', '/CN=Phone Pilot', '-out', csr])
        with open(csr) as handle:
            made = asc.create('certificates', {'csrContent': handle.read(), 'certificateType': 'DEVELOPMENT'})
        content = made['attributes']['certificateContent']
        with open(cer, 'wb') as handle:
            handle.write(base64.b64decode(content))
        run([OPENSSL, 'x509', '-inform', 'DER', '-in', cer, '-out', pem])
        password = os.environ.get('ICTRL_P12_PASSWORD') if export_p12 else None
        if export_p12 and not password:
            raise SetupError('导出证书需要 ICTRL_P12_PASSWORD')
        password = password or secrets.token_hex(16)
        run([OPENSSL, 'pkcs12', '-export', '-inkey', key, '-in', pem, '-out', p12, '-passout', 'pass:' + password])
        run(['security', 'import', p12, '-k', keychain_path(), '-P', password, '-T', '/usr/bin/codesign'])
        if export_p12:
            shutil.copyfile(p12, export_p12)
            os.chmod(export_p12, 0o600)
    return made['id'], cert_sha1(content)


# ---------------------------------------------------------------- 手机
def connected_udid():
    if not shutil.which('ideviceinfo'):
        raise SetupError('缺少 ideviceinfo，请先运行：brew install ideviceinstaller libimobiledevice')
    result = run(['ideviceinfo', '-k', 'UniqueDeviceID'], check=False)
    udid = result.stdout.decode().strip()
    if result.returncode != 0 or not udid:
        raise SetupError('没有读到 iPhone：请用 USB 接 Mac、解锁，并在手机上点“信任”')
    return udid


def install_on_phone(ipa):
    if not shutil.which('ideviceinstaller'):
        raise SetupError('缺少 ideviceinstaller，请先运行：brew install ideviceinstaller')
    # 新版用 install 子命令，旧版用 -i。
    help_text = run(['ideviceinstaller', '--help'], check=False)
    modern = b'install ' in help_text.stdout + help_text.stderr and b'-i, --install' not in help_text.stdout + help_text.stderr
    run(['ideviceinstaller', 'install', ipa] if modern else ['ideviceinstaller', '-i', ipa])


# ---------------------------------------------------------------- 命令
def cmd_provision(args):
    if not os.path.isfile(args.key_file):
        raise SetupError('找不到 API 密钥文件')
    udid = args.udid or connected_udid()
    token = make_token(args.key_file, args.key_id, args.issuer_id)
    asc = Asc(token, args.api_base)
    certs = asc.development_certs()
    local = keychain_identities()
    reusable = next((c for c in certs if cert_sha1(c['attributes']['certificateContent']) in local), None)
    print(f'iPhone：{mask(udid)}')
    print('会在 Apple 账号里做这些事（已有的直接复用）：')
    print('  - 注册这台 iPhone（占用账号的设备名额）')
    print(f'  - 确认通配 App ID（{args.app_id}），它要能覆盖 runner 和 iCTRL App 的 bundle id')
    print('  - ' + ('复用钥匙串里已有的开发证书' if reusable else '新建一张开发证书，私钥放进钥匙串'))
    print(f'  - 创建或替换描述文件“{PROFILE_NAME}”')
    if not args.yes and input('继续？[y/N] ').strip().lower() != 'y':
        print('已取消，账号未改动')
        return 1
    device_id, new_device = asc.ensure_device(udid)
    print('设备：' + ('已注册' if new_device else '已存在'))
    bundle_id, new_bundle = asc.ensure_wildcard(args.app_id)
    print('App ID：' + ('已创建' if new_bundle else '已存在'))
    import_wwdr()
    if reusable:
        cert_id, sha1 = reusable['id'], cert_sha1(reusable['attributes']['certificateContent'])
    else:
        cert_id, sha1 = create_certificate(asc, args.export_p12)
    if sha1 not in keychain_identities():
        raise SetupError('证书导入后 codesign 仍不认，请查看钥匙串里的 Apple Development 证书是否受信任')
    print('证书：可用')
    profile = asc.replace_profile(bundle_id, cert_id, device_id)
    path = state_path('phone-pilot.mobileprovision')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(base64.b64decode(profile['attributes']['profileContent']))
    print('描述文件：已保存到 ' + path)
    print('下一步：python3 demo/ictrl_setup.py install')
    return 0


def fetch_ipa(name):
    path = state_path(RELEASE + '-' + name)
    if not os.path.isfile(path):
        run(['curl', '-fsSL', '--retry', '2', '-o', path + '.part', RELEASE_URL + name])
        os.replace(path + '.part', path)
    with open(path, 'rb') as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest != IPAS[name]:
        os.remove(path)
        raise SetupError(f'{name} 校验不符，已删除，未签名也未安装')
    return path


def cmd_install(args):
    profile = state_path('phone-pilot.mobileprovision')
    if not os.path.isfile(profile):
        raise SetupError('还没有描述文件，请先运行 provision')
    names = list(IPAS) if not args.runner_only else ['iCTRLUITests-Runner.ipa']
    connected_udid()
    for name in names:
        source = fetch_ipa(name)
        print(f'{name}：校验通过')
        signed = state_path(name.replace('.ipa', '-signed.ipa'))
        # LC_ALL=C：这台 Mac 的 bash 5.3 在 UTF-8 下有过 unbound variable 的问题。
        result = subprocess.run(['bash', SIGN_SCRIPT, '-i', source, '-p', profile, '-o', signed],
                                env=dict(os.environ, LC_ALL='C'), capture_output=True)
        if result.returncode != 0:
            raise SetupError('签名失败：' + result.stderr.decode('utf-8', 'replace').strip()[-400:])
        os.chmod(signed, 0o600)
        print(f'{name}：已签名')
        install_on_phone(signed)
        print(f'{name}：已安装')
    print('下一步：手机上信任开发者证书、挂载磁盘镜像、启动 runner，见 demo/ictrl-integration.md')
    return 0


def cmd_install_ci(args):
    if not os.path.isfile(args.password_file):
        raise SetupError('找不到产物密码文件：' + args.password_file)
    connected_udid()
    listed = run(['gh', 'run', 'list', '-R', CI_REPO, '--workflow', CI_WORKFLOW, '--branch', CI_BRANCH,
                  '--status', 'success', '--limit', '1', '--json', 'databaseId']).stdout
    runs = json.loads(listed or b'[]')
    if not runs:
        raise SetupError('还没有成功的构建，请到 GitHub Actions 查看 iCTRL build')
    run_id = str(runs[0]['databaseId'])
    with tempfile.TemporaryDirectory() as tmp:
        fetched = run(['gh', 'run', 'download', run_id, '-R', CI_REPO, '-n', CI_ARTIFACT, '-D', tmp], check=False)
        if fetched.returncode != 0:
            raise SetupError('最新一次构建没有签名产物：可能缺 IPHONE_UDID 或 ICTRL_ARTIFACT_PASSWORD 密钥，或产物已过期')
        bundle = os.path.join(tmp, 'bundle.tar')
        run([OPENSSL, 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', os.path.join(tmp, 'ictrl-signed.tar.enc'),
             '-out', bundle, '-pass', 'file:' + args.password_file])
        installed = []
        with tarfile.open(bundle) as archive:
            # 只取已知文件名，不解压任意路径。tar -C dir . 打包的成员名带 ./ 前缀。
            members = {m.name[2:] if m.name.startswith('./') else m.name: m for m in archive.getmembers() if m.isfile()}
            for name in ('iCTRLUITests-Runner-signed.ipa', 'iCTRL-signed.ipa', CI_P12_NAME):
                if name not in members:
                    continue
                member = archive.extractfile(members[name])
                path = state_path(name)
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, 'wb') as handle:
                    shutil.copyfileobj(member, handle)
                installed.append(path)
    ipas = [p for p in installed if p.endswith('.ipa')]
    if not ipas:
        raise SetupError('产物里没有 IPA')
    p12 = state_path(CI_P12_NAME)
    if p12 in installed:
        # 存回仓库密钥，下次构建复用这张证书。值走标准输入，不进命令行。
        with open(p12, 'rb') as handle:
            run(['gh', 'secret', 'set', CI_P12_SECRET, '-R', CI_REPO], input=base64.b64encode(handle.read()))
        os.remove(p12)
        print('开发证书：已存入仓库密钥')
    for path in ipas:
        install_on_phone(path)
        print(os.path.basename(path) + '：已安装')
    print('下一步：手机上信任开发者证书、挂载磁盘镜像、启动 runner，见 demo/ictrl-integration.md')
    return 0


def cmd_doctor(_args):
    lines = []

    def report(ok, text):
        lines.append(('✅ ' if ok else '❌ ') + text)
        return ok

    for tool in ('codesign', 'security', 'plutil', 'zip', 'unzip', 'curl'):
        report(bool(shutil.which(tool)), tool)
    report(os.path.exists(OPENSSL), 'openssl（系统自带）')
    report(os.path.exists('/usr/libexec/PlistBuddy'), 'PlistBuddy')
    report(os.path.isfile(SIGN_SCRIPT), '签名脚本')
    for tool in ('ideviceinfo', 'ideviceinstaller'):
        report(bool(shutil.which(tool)), tool + '（没有就 brew install ideviceinstaller libimobiledevice）')
    try:
        report(True, 'iPhone 已通过 USB 连接：' + mask(connected_udid()))
    except SetupError as exc:
        report(False, str(exc))
    if shutil.which('security'):
        report(True, f'钥匙串里的签名身份：{len(keychain_identities())} 个')
    found = sorted({os.path.basename(p) for root in ('~/Downloads', '~/下载', '~/.appstoreconnect/private_keys', '~/private_keys')
                    for p in glob.glob(os.path.expanduser(root + '/AuthKey_*.p8'))})
    report(bool(found), 'API 密钥文件：' + ('、'.join(found) if found else '常见位置没有，要在 App Store Connect 新建一把 Admin 团队密钥'))
    report(os.path.isfile(os.path.join(STATE_DIR, 'phone-pilot.mobileprovision')), '描述文件')
    print('\n'.join(lines))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='把 iCTRL runner 签名并装到 iPhone')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('doctor')
    provision = sub.add_parser('provision')
    provision.add_argument('--key-file', required=True)
    provision.add_argument('--key-id', required=True)
    provision.add_argument('--issuer-id', required=True)
    provision.add_argument('--udid', help='默认读取 USB 连接的 iPhone')
    provision.add_argument('--yes', action='store_true')
    provision.add_argument('--app-id', default='*', help='通配 App ID，默认全通配')
    provision.add_argument('--export-p12', help='新建证书时另存 p12，密码取自 ICTRL_P12_PASSWORD')
    provision.add_argument('--api-base', help=argparse.SUPPRESS)
    install = sub.add_parser('install')
    install.add_argument('--runner-only', action='store_true', help='不装 iCTRL App')
    install_ci = sub.add_parser('install-ci')
    install_ci.add_argument('--password-file', default=CI_PASSWORD_FILE)
    args = parser.parse_args(argv)
    try:
        return {'doctor': cmd_doctor, 'provision': cmd_provision, 'install': cmd_install,
                'install-ci': cmd_install_ci}[args.cmd](args)
    except SetupError as exc:
        print('❌ ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
