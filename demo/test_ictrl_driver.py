"""iCTRL 执行层测试。用本地 HTTP 假设备和真实的上游加密客户端，不代表真机结果。"""
import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

os.environ.pop('EXECUTOR', None)
import ictrl_driver
import server
from ictrl_driver import Driver, acceptance, ictrl_rpc

KEY = bytes(range(32))
TOK = hashlib.sha256(KEY).digest()[:4].hex()
DEVICE = 'fake-device-id'
WECHAT = 'com.tencent.xin'


class FakeDevice:
    """按 iCTRL 协议 v1 校验并解密请求；方法行为由测试脚本决定。"""

    def __init__(self):
        self.calls = []
        self.results = {'device_info': {'server_version': '1.98.16', 'protocol_version': 1,
                                        'system': {'screen_locked': False, 'foreground_app': 'com.apple.springboard'}}}
        self.errors = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def send(self, status, data):
                payload = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('X-ICTRL-Device', DEVICE)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self.send(200, {'device_id': DEVICE})

            def do_POST(self):
                envelope = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                nonce = bytes.fromhex(envelope['nonce'])
                cipher = base64.b64decode(envelope['ct'])
                mac = ictrl_rpc._hmac(KEY, ictrl_rpc.LABEL_MAC + nonce)
                signed = b'v1' + bytes.fromhex(envelope['tok']) + envelope['ts'].to_bytes(8, 'big') + nonce + cipher
                if not hmac.compare_digest(ictrl_rpc._hmac(mac, signed), bytes.fromhex(envelope['tag'])):
                    self.send(401, {'error': {'message': 'bad tag'}})
                    return
                request = json.loads(ictrl_rpc._keystream_xor(ictrl_rpc._hmac(KEY, ictrl_rpc.LABEL_ENC_REQ + nonce), nonce, cipher))
                body = owner.answer(request)
                resp = ictrl_rpc._keystream_xor(ictrl_rpc._hmac(KEY, ictrl_rpc.LABEL_ENC_RESP + nonce), nonce, json.dumps(body).encode())
                self.send(200, {'ct': base64.b64encode(resp).decode(),
                                'tag': ictrl_rpc._hmac(mac, b'resp' + nonce + resp).hex()})

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.host = f'127.0.0.1:{self.http.server_port}'
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def answer(self, request):
        method, params = request['method'], request['params']
        self.calls.append((method, params))
        rid = request['id']
        if method in self.errors:
            code, message = self.errors[method]
            return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}
        value = self.results.get(method, {})
        if callable(value):
            value = value(params)
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        return {'jsonrpc': '2.0', 'id': rid, 'result': value}

    def count(self, method):
        return sum(1 for name, _ in self.calls if name == method)

    def params(self, method):
        return [p for name, p in self.calls if name == method]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(ictrl_rpc, 'STORE', os.path.join(self.tmp.name, 'token.json')),
                   mock.patch.object(ictrl_rpc, 'ADDRESS_CACHE', os.path.join(self.tmp.name, 'devices.json'))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.device = FakeDevice()
        self.addCleanup(self.device.http.server_close)
        self.addCleanup(self.device.http.shutdown)
        ictrl_rpc.save_token(ictrl_rpc.device_store_key(DEVICE), {'tok': TOK, 'key': KEY.hex()})
        self.driver = Driver(self.device.host, wait=0.2, sleep=lambda s: time.sleep(0.01))

    def hit(self, **extra):
        return dict({'exists': True, 'hittable': True, 'ref': 'r1', 'label': '搜索', 'visibility': 'in_view',
                     'center_x': 100, 'center_y': 200}, **extra)


class OpenApp(Base):
    def test_opens_by_bundle_and_verifies_foreground(self):
        self.device.results['launch_app'] = {'foreground_app': WECHAT}
        outcome = self.driver.open_app('微信')
        self.assertEqual(outcome.status, 'done')
        self.assertEqual(self.device.params('launch_app'), [{'bundle_id': WECHAT}])
        self.assertEqual(self.device.count('tap'), 0)

    def test_every_voice_app_has_a_bundle(self):
        for name in server.jev.APPS:
            self.assertIn(name, ictrl_driver.APP_BUNDLES)

    def test_waits_for_foreground_without_launching_twice(self):
        self.device.results['launch_app'] = {'foreground_app': 'com.apple.springboard'}
        self.device.results['device_info'] = [
            {'system': {'screen_locked': False}},
            {'system': {'foreground_app': WECHAT}}]
        self.assertEqual(self.driver.open_app('微信').status, 'done')
        self.assertEqual(self.device.count('launch_app'), 1)

    def test_other_foreground_is_not_reported_as_done(self):
        self.device.results['launch_app'] = {'foreground_app': 'com.apple.springboard'}
        outcome = self.driver.open_app('微信')
        self.assertEqual(outcome.status, 'unverified')
        self.assertFalse(outcome.ok)
        self.assertEqual(self.device.count('launch_app'), 1)

    def test_not_installed(self):
        self.device.errors['launch_app'] = (-32000, "Could not launch 'x'")
        self.assertEqual(self.driver.open_app('微信').status, 'not_installed')

    def test_locked_phone_is_not_touched(self):
        self.device.results['device_info'] = {'system': {'screen_locked': True}}
        self.assertEqual(self.driver.open_app('微信').status, 'locked')
        self.assertEqual(self.device.count('launch_app'), 0)

    def test_system_alert_stops(self):
        self.device.results['launch_app'] = {'foreground_app': WECHAT, 'system_alert': True}
        self.assertEqual(self.driver.open_app('微信').status, 'blocked')

    def test_unknown_name_is_rejected_locally(self):
        self.assertEqual(self.driver.open_app('没有这个').status, 'invalid')
        self.assertEqual(self.device.calls, [])

    def test_device_timeout_never_retries(self):
        self.device.errors['launch_app'] = (-32000, 'launch_app exceeded 6s and may still be running on the device.')
        self.assertEqual(self.driver.open_app('微信').status, 'unknown')
        self.assertEqual(self.device.count('launch_app'), 1)

    def test_runner_down_is_unavailable_not_a_crash(self):
        self.device.http.shutdown()
        self.device.http.server_close()
        outcome = self.driver.open_app('微信')
        self.assertEqual(outcome.status, 'unavailable')

    def test_corrupt_reply_is_unknown_and_not_retried(self):
        calls = []
        def corrupt(*args, **kwargs):
            calls.append(args)
            raise SystemExit('response MAC mismatch — discarding')
        with mock.patch.object(ictrl_rpc, 'device_call', corrupt):
            self.assertEqual(self.driver.open_app('微信').status, 'unknown')
        self.assertEqual(len(calls), 1)

    def test_wrong_key_asks_to_pair_again(self):
        ictrl_rpc.save_token(ictrl_rpc.device_store_key(DEVICE), {'tok': TOK, 'key': bytes(32).hex()})
        self.assertEqual(self.driver.open_app('微信').status, 'needs_pairing')


class Tap(Base):
    def test_taps_once_by_ref_never_by_coordinates(self):
        self.device.results['find_element'] = self.hit()
        self.device.results['tap'] = {'settle': {'stable': True, 'screen_changed': True, 'waited_ms': 120}}
        outcome = self.driver.tap('搜索')
        self.assertEqual(outcome.status, 'tapped')
        self.assertEqual(self.device.params('tap'), [{'ref': 'r1'}])

    def assert_no_tap(self, expected, **hit):
        self.device.results['find_element'] = self.hit(**hit)
        self.assertEqual(self.driver.tap('搜索').status, expected)
        self.assertEqual(self.device.count('tap'), 0)

    def test_look_alikes_stop(self):
        self.assert_no_tap('ambiguous', alternatives=[{'label': '搜索'}], match_count=2)

    def test_offscreen_stops(self):
        self.assert_no_tap('offscreen', visibility='offscreen')

    def test_partial_stops(self):
        self.assert_no_tap('offscreen', visibility='partial')

    def test_not_hittable_stops(self):
        self.assert_no_tap('offscreen', hittable=False)

    def test_empty_ref_stops(self):
        self.assert_no_tap('not_found', ref='')

    def test_captcha_stops(self):
        self.assert_no_tap('blocked', blocked_by='captcha')

    def test_not_found(self):
        self.device.errors['find_element'] = (-32602, "No element matching label/id '搜索'")
        self.assertEqual(self.driver.tap('搜索').status, 'not_found')

    def test_alert_reported_on_lookup(self):
        self.device.errors['find_element'] = (-32602, "No element: a system alert covers the screen")
        self.assertEqual(self.driver.tap('搜索').status, 'blocked')

    def test_sensitive_labels_never_reach_the_device(self):
        for label, status in (('确认支付', 'forbidden'), ('删除', 'forbidden'), ('Pay', 'forbidden'),
                              ('发送', 'needs_confirmation'), ('Send', 'needs_confirmation')):
            self.assertEqual(self.driver.tap(label).status, status, label)
        self.assertEqual(self.device.calls, [])

    def test_loose_match_landing_on_send_is_blocked(self):
        self.assert_no_tap('needs_confirmation', label='发送')

    def test_error_from_ref_means_not_performed(self):
        self.device.results['find_element'] = self.hit()
        self.device.errors['tap'] = (-32602, "ref 'r1' is unknown or expired — nothing was tapped.")
        self.assertEqual(self.driver.tap('搜索').status, 'not_performed')
        self.assertEqual(self.device.count('tap'), 1)

    def test_queued_tap_is_never_resent(self):
        self.device.results['find_element'] = self.hit()
        self.device.errors['tap'] = (-32000, 'tap has NOT run yet: it is queued behind x — do not send it again.')
        self.assertEqual(self.driver.tap('搜索').status, 'unknown')
        self.assertEqual(self.device.count('tap'), 1)

    def test_no_visible_change_is_reported(self):
        self.device.results['find_element'] = self.hit()
        self.device.results['tap'] = {'settle': {'stable': True, 'screen_changed': False}}
        outcome = self.driver.tap('搜索')
        self.assertEqual(outcome.status, 'no_visible_change')
        self.assertFalse(outcome.ok)

    def test_ocr_only_when_allowed_and_unique(self):
        self.device.errors['find_element'] = (-32602, 'No element matching')
        self.device.results['screen_text'] = {'items': [{'label': '搜索', 'center_x': 50, 'center_y': 60},
                                                        {'label': '搜索历史', 'center_x': 50, 'center_y': 90}]}
        self.device.results['tap'] = {'settle': {'screen_changed': True}}
        self.assertEqual(self.driver.tap('搜索').status, 'not_found')
        self.assertEqual(self.device.count('screen_text'), 0)
        self.assertEqual(self.driver.tap('搜索', allow_ocr=True).status, 'tapped')
        self.assertEqual(self.device.params('tap'), [{'x': 50, 'y': 60}])

    def test_ocr_duplicates_stop(self):
        self.device.errors['find_element'] = (-32602, 'No element matching')
        self.device.results['screen_text'] = {'items': [{'label': '搜索', 'center_x': 1, 'center_y': 2},
                                                        {'label': '搜索', 'center_x': 3, 'center_y': 4}]}
        self.assertEqual(self.driver.tap('搜索', allow_ocr=True).status, 'ambiguous')
        self.assertEqual(self.device.count('tap'), 0)

    def test_ocr_stale_screen_is_not_success(self):
        self.device.errors['find_element'] = (-32602, 'No element matching')
        self.device.results['screen_text'] = {'items': [{'label': '搜索', 'center_x': 1, 'center_y': 2}]}
        self.device.results['tap'] = {'page_check': {'same_page': False}, 'settle': {'screen_changed': True}}
        self.assertEqual(self.driver.tap('搜索', allow_ocr=True).status, 'unverified')


class TypeText(Base):
    def script(self, shown):
        self.device.results['page_source'] = lambda params: (
            {'total_returned': 0, 'source': ''} if params['filter'].get('type')
            else {'total_returned': 1, 'source': shown})

    def test_chinese_goes_in_verbatim_and_never_submits(self):
        self.script('TextField value="你好 hello"')
        self.device.results['type'] = {'message': 'ok'}
        self.assertEqual(self.driver.type_text('你好 hello').status, 'done')
        self.assertEqual(self.device.params('type'), [{'text': '你好 hello'}])

    def test_unconfirmed_text_is_unverified_and_not_retyped(self):
        self.script('')
        self.device.results['type'] = {'message': 'ok'}
        self.assertEqual(self.driver.type_text('你好').status, 'unverified')
        self.assertEqual(self.device.count('type'), 1)

    def test_warning_is_unverified(self):
        self.script('你好')
        self.device.results['type'] = {'warning': 'no keyboard'}
        self.assertEqual(self.driver.type_text('你好').status, 'unverified')

    def test_password_field_refused(self):
        self.device.results['page_source'] = {'total_returned': 1, 'source': 'SecureTextField'}
        self.assertEqual(self.driver.type_text('abc').status, 'unsafe_field')
        self.assertEqual(self.device.count('type'), 0)

    def test_type_error_is_not_reported_as_not_performed(self):
        self.script('')
        self.device.errors['type'] = (-32000, 'could not type')
        self.assertEqual(self.driver.type_text('你好').status, 'unknown')
        self.assertEqual(self.device.count('type'), 1)

    def test_newline_cannot_submit(self):
        for text in ('a\nb', 'a\r', '', ' ', 'x' * 201):
            self.assertEqual(self.driver.type_text(text).status, 'invalid')
        self.assertEqual(self.device.calls, [])


class PairCommand(Base):
    def test_refused_port_gets_a_plain_message_not_a_traceback(self):
        host = self.device.host
        self.device.http.shutdown()
        self.device.http.server_close()
        err = io.StringIO()
        with mock.patch.object(sys, 'argv', ['ictrl_driver.py', '--host', host, 'pair']), contextlib.redirect_stderr(err):
            self.assertEqual(ictrl_driver.main(), 1)
        self.assertIn('远程访问', err.getvalue())


class Acceptance(Base):
    def test_counts_rounds_and_stops_at_first_failure(self):
        self.device.results['home'] = {'foreground_app': 'com.apple.springboard'}
        self.device.results['launch_app'] = [{'foreground_app': WECHAT}, {'foreground_app': WECHAT},
                                             {'foreground_app': 'com.apple.springboard'}]
        report = acceptance(self.driver, '微信', 5)
        self.assertEqual(report['passed'], 2)
        self.assertEqual(len(report['runs']), 3)
        self.assertEqual(report['runs'][-1]['status'], 'unverified')


class Coordinator(Base):
    """真实 HTTP 走一遍圆屏的 /audio → /next。"""

    def setUp(self):
        super().setUp()
        self.state = server.State()
        patches = [mock.patch.object(server, 'state', self.state),
                   mock.patch.object(server, 'transcribe', lambda wav: self.spoken),
                   mock.patch.object(server, '_ictrl_driver', None),
                   mock.patch.dict(os.environ, {'EXECUTOR': 'ictrl', 'ICTRL_HOST': self.device.host})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.token = self.request('/pair', json.dumps({'role': 'device', 'code': self.state.code}).encode())['token']

    def request(self, path, body=None):
        request = urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}{path}', data=body)
        if getattr(self, 'token', None):
            request.add_header('Authorization', 'Bearer ' + self.token)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    def say(self, words):
        self.spoken = words
        self.request('/audio', b'wav')
        return self.request('/next')

    def test_open_app_runs_on_mac_without_frames_or_hid(self):
        self.device.results['launch_app'] = {'foreground_app': WECHAT}
        reply = self.say('打开微信')
        self.assertIsNone(reply['action'])
        self.assertEqual(reply['status'], '操作完成')
        self.assertEqual(reply['command'], '打开微信')
        self.assertEqual(self.device.count('launch_app'), 1)
        self.assertEqual(self.request('/next'), {'action': None})
        self.assertEqual(self.device.count('launch_app'), 1)
        self.assertEqual(self.state.last_decision, {'type': 'open_app', 'status': 'executed'})

    def test_failure_reaches_the_round_screen(self):
        self.device.results['device_info'] = {'system': {'screen_locked': True}}
        reply = self.say('打开微信')
        self.assertEqual(reply['error'], 'iPhone 已锁屏，请先解锁')
        self.assertIsNone(self.state.task)

    def test_chinese_and_mixed_text_are_typed_directly(self):
        self.device.results['page_source'] = lambda p: (
            {'total_returned': 0, 'source': ''} if p['filter'].get('type') else {'total_returned': 1, 'source': '你好hello'})
        self.assertEqual(self.say('输入你好hello')['status'], '操作完成')
        self.assertEqual(self.device.params('type'), [{'text': '你好hello'}])

    def test_send_does_not_touch_the_phone(self):
        reply = self.say('给张三发送hello')
        self.assertIn('确认', reply['error'])
        self.assertEqual(self.device.calls, [])

    def test_complex_sentence_takes_a_second_poll(self):
        self.device.results['launch_app'] = {'foreground_app': WECHAT}
        with mock.patch.object(server, 'understand_intent', return_value='打开微信') as understand:
            self.assertEqual(self.say('麻烦把微信给我打开')['status'], '指令已解析')
            self.assertEqual(self.device.count('launch_app'), 0)
            self.assertEqual(self.request('/next')['status'], '操作完成')
        understand.assert_called_once()
        self.assertEqual(self.device.count('launch_app'), 1)

    def test_unparsed_sentence_stops(self):
        with mock.patch.object(server, 'understand_intent', return_value=None):
            reply = self.say('今天天气真好')
        self.assertEqual(reply['command'], '未能解析')
        self.assertTrue(reply['error'])
        self.assertEqual(self.device.calls, [])

    def test_missing_host_is_explained(self):
        with mock.patch.dict(os.environ):
            del os.environ['ICTRL_HOST']
            self.assertEqual(self.say('打开微信')['error'], '未配置 ICTRL_HOST')

    def test_status_never_contains_spoken_text(self):
        self.device.results['page_source'] = lambda p: {'total_returned': 0, 'source': ''}
        self.say('输入秘密内容')
        request = urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/status',
                                         headers={'Authorization': 'Pair ' + self.state.code})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
            raw = response.read().decode()
        status = json.loads(raw)
        self.assertNotIn('秘密内容', raw)
        self.assertEqual(status['executor'], 'ictrl')
        self.assertEqual(status['ictrl_diagnostic']['status'], 'unverified')

    def test_default_executor_still_needs_a_shared_frame(self):
        with mock.patch.dict(os.environ):
            del os.environ['EXECUTOR']
            with self.assertRaises(ValueError):
                self.state.begin('打开微信')


if __name__ == '__main__':
    unittest.main()
