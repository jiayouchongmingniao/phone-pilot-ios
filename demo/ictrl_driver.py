#!/usr/bin/env python3
"""Phone Pilot 的 iCTRL 执行层：按语义操作 iPhone，并核实结果。

与 HID 指针路径的区别：打开 App 用 bundle id，点击用 iCTRL 的 ref（点击时重新定位活元素），
都不依赖模型坐标、相对移动或画面像素比较。这里只做确定性的规则：
目标不唯一、不在屏内、或结果不明时停止，不重试、不换路径。

不记录也不返回输入内容、屏幕文字和密钥。用法见 ictrl-integration.md。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import statistics
import sys
import threading
import time
import unicodedata
import urllib.error
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'third_party'))
import ictrl_rpc  # noqa: E402  Apache-2.0，来源见 third_party/README.md

RPC_TIMEOUT = 30           # iCTRL 的 UI 调用 6～15 秒后自己回复；客户端要比它长。
FOREGROUND_WAIT = 4.0      # launch_app 回报后 App 可能还在切换。
MAX_TEXT = 200

# 语音指令里的 App 名（jev.APPS）→ bundle id。launch_app 和前台核对会暴露错误或未安装。
APP_BUNDLES = {
    '设置': 'com.apple.Preferences', '相机': 'com.apple.camera',
    '照片': 'com.apple.mobileslideshow', '相册': 'com.apple.mobileslideshow',
    '微信': 'com.tencent.xin', '支付宝': 'com.alipay.iphoneclient',
    '地图': 'com.apple.Maps', '音乐': 'com.apple.Music', '日历': 'com.apple.mobilecal',
    '时钟': 'com.apple.mobiletimer', '闹钟': 'com.apple.mobiletimer',
    '计算器': 'com.apple.calculator', '备忘录': 'com.apple.mobilenotes',
    '天气': 'com.apple.weather', '信息': 'com.apple.MobileSMS', '短信': 'com.apple.MobileSMS',
    '电话': 'com.apple.mobilephone', '邮件': 'com.apple.mobilemail',
    '浏览器': 'com.apple.mobilesafari', '抖音': 'com.ss.iphone.ugc.Aweme',
    '淘宝': 'com.taobao.taobao4iphone', '小红书': 'com.xingin.discover',
}

# 宁可多拦：这些标签不走普通点击。付款、删除不支持；发送必须另有圆屏确认。
FORBIDDEN_LABEL = re.compile(
    r'支付|付款|转账|购买|下单|结算|提现|充值|红包|删除|清空|卸载|注销|'
    r'\b(pay|buy|purchase|checkout|delete|remove|erase)\b', re.I)
SEND_LABEL = re.compile(r'发送|发出|提交|\b(send|submit|post)\b', re.I)

MESSAGES = {
    'done': '操作完成',
    'tapped': '已点击',
    'unverified': '已执行，但未能核实结果，请在手机上查看',
    'no_visible_change': '已点击，但画面没有变化，请在手机上查看',
    'unknown': '结果不明，为避免重复操作已停止，请在手机上查看',
    'not_performed': '没有执行，请重试',
    'locked': 'iPhone 已锁屏，请先解锁',
    'blocked': '手机上有弹窗或验证，请先处理',
    'not_found': '屏幕上没有找到目标',
    'not_installed': '手机上没有这个 App',
    'ambiguous': '目标不唯一，已停止',
    'offscreen': '目标不在当前屏幕内',
    'forbidden': '此操作不在支持范围内',
    'needs_confirmation': '此操作需要确认，请在手机上操作',
    'unsafe_field': '当前是密码输入框，已停止',
    'invalid': '操作内容无效',
    'unavailable': 'iCTRL 未连接，请在手机上启动 runner',
    'needs_pairing': 'iCTRL 需要重新配对',
}


@dataclass
class Outcome:
    """一次操作的结论。`detail` 只放诊断用的短字段，不放用户文字。"""
    status: str
    detail: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.status in ('done', 'tapped')

    @property
    def message(self):
        return MESSAGES.get(self.status, '操作失败，请在手机上查看')


class _Stop(Exception):
    def __init__(self, status, **detail):
        super().__init__(status)
        self.outcome = Outcome(status, detail)


def _same_text(a, b):
    return unicodedata.normalize('NFKC', str(a)).strip().casefold() == \
        unicodedata.normalize('NFKC', str(b)).strip().casefold()


class Driver:
    def __init__(self, host, *, wait=FOREGROUND_WAIT, sleep=time.sleep, clock=time.monotonic):
        self.host, self.wait, self.sleep, self.clock = host, wait, sleep, clock
        # 上游客户端用模块级变量记录应答设备，调用必须串行。
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 传输
    def _rpc(self, method, params=None):
        """返回 result；任何失败都转成 _Stop，绝不让客户端的 SystemExit 杀掉服务线程。"""
        try:
            with self._lock:
                reply = ictrl_rpc.device_call(self.host, method, params or {}, timeout=RPC_TIMEOUT)
        except SystemExit as exc:
            text = str(exc)
            if 'MAC mismatch' in text:
                raise _Stop('unknown', method=method, reason='bad_reply') from None
            if '401' in text or 'no key' in text or 'run `pair`' in text:
                raise _Stop('needs_pairing') from None
            raise _Stop('unavailable', reason='rpc_refused') from None
        except (TimeoutError, socket.timeout) as exc:
            raise _Stop('unknown', method=method, reason='client_timeout') from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise _Stop('unknown', method=method, reason='client_timeout') from exc
            raise _Stop('unavailable', reason=type(exc.reason).__name__) from exc
        except (OSError, http.client.HTTPException, ValueError, KeyError) as exc:
            # 请求可能已送达（如读回复时断开或回复损坏），动作的结果未知。
            raise _Stop('unknown', method=method, reason=type(exc).__name__) from exc
        error = reply.get('error')
        if error:
            raise _RpcError(error.get('code'), str(error.get('message', '')))
        return reply.get('result') or {}

    def _action(self, method, params):
        """动作类调用。超时只代表不知道，不是失败：一律停止，不重发。"""
        try:
            return self._rpc(method, params)
        except _RpcError as exc:
            lowered = exc.message.lower()
            if 'not run yet' in lowered or 'may still be running' in lowered or 'exceeded' in lowered:
                # 排队中的调用稍后仍会执行，重发就是点两次。
                raise _Stop('unknown', method=method, reason='device_timeout') from None
            if 'unresponsive' in lowered:
                raise _Stop('unavailable', reason='ui_unresponsive') from None
            if 'nothing was tapped' in lowered or 'not performed' in lowered or exc.code == -32004:
                raise _Stop('not_performed', method=method) from None
            raise

    def _guard(self, result):
        if result.get('system_alert'):
            raise _Stop('blocked', reason='system_alert')
        if result.get('blocked_by'):
            raise _Stop('blocked', reason=str(result['blocked_by']))

    def _system(self):
        info = self._rpc('device_info')
        system = info.get('system')
        return system if isinstance(system, dict) else {}

    def _preflight(self):
        if self._system().get('screen_locked') is True:
            raise _Stop('locked')

    @staticmethod
    def _run(work):
        try:
            return work()
        except _Stop as stop:
            return stop.outcome
        except _RpcError as exc:
            return Outcome('not_performed', {'code': exc.code})

    # ------------------------------------------------------------ 打开 App
    def open_app(self, app):
        """app 可以是 APP_BUNDLES 里的中文名或 bundle id。前台确认是它才算完成。"""
        return self._run(lambda: self._open_app(app))

    def _open_app(self, app):
        bundle = APP_BUNDLES.get(app) or (app if re.fullmatch(r'[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+', str(app)) else None)
        if not bundle:
            raise _Stop('invalid', reason='unknown_app')
        self._preflight()
        started = self.clock()
        try:
            result = self._action('launch_app', {'bundle_id': bundle})
        except _RpcError:
            raise _Stop('not_installed', bundle_id=bundle) from None
        self._guard(result)
        front = result.get('foreground_app')
        deadline = self.clock() + self.wait
        while front != bundle and self.clock() < deadline:
            self.sleep(0.4)
            front = self._system().get('foreground_app')
        detail = {'bundle_id': bundle, 'foreground_app': front, 'ms': round((self.clock() - started) * 1000)}
        return Outcome('done' if front == bundle else 'unverified', detail)

    def home(self):
        return self._run(lambda: self._home())

    def _home(self):
        result = self._action('home', {})
        return Outcome('done', {'foreground_app': result.get('foreground_app')})

    # ------------------------------------------------------------ 点击
    def tap(self, label, *, element_type=None, match=None, bundle_id=None, allow_ocr=False):
        """按标签点击一次。默认只接受无障碍树里唯一、在屏内的元素，用 ref 点击。

        allow_ocr 才在无障碍树没有目标时用 OCR 坐标，且要求文字完全相等且只有一处。
        """
        return self._run(lambda: self._tap(label, element_type, match, bundle_id, allow_ocr))

    @staticmethod
    def _check_label(label):
        if FORBIDDEN_LABEL.search(str(label)):
            raise _Stop('forbidden')
        if SEND_LABEL.search(str(label)):
            raise _Stop('needs_confirmation')

    def _tap(self, label, element_type, match, bundle_id, allow_ocr):
        if not str(label).strip():
            raise _Stop('invalid')
        self._check_label(label)
        self._preflight()
        params = {'element_id': label}
        for key, value in (('element_type', element_type), ('match', match), ('bundle_id', bundle_id)):
            if value:
                params[key] = value
        try:
            hit = self._rpc('find_element', params)
        except _RpcError as exc:
            if 'system alert' in exc.message:
                raise _Stop('blocked', reason='system_alert') from None
            if not allow_ocr:
                raise _Stop('not_found') from None
            return self._tap_ocr(label)
        self._guard(hit)
        # 模糊匹配可能落到别的控件，实际命中的标签也要过一遍规则。
        self._check_label(hit.get('label', ''))
        if hit.get('alternatives') or (hit.get('match_count') or 1) > 1:
            raise _Stop('ambiguous', match_count=hit.get('match_count'))
        if hit.get('visibility', 'in_view') != 'in_view' or hit.get('hittable') is False:
            raise _Stop('offscreen', visibility=hit.get('visibility'))
        ref = hit.get('ref')
        if not ref:
            # 无障碍树太慢时只有坐标没有 ref，这种结果不够可靠。
            raise _Stop('not_found', reason='no_ref')
        result = self._action('tap', {'ref': ref})
        self._guard(result)
        return self._after_tap(result, via='ref')

    def _tap_ocr(self, label):
        result = self._rpc('screen_text', {'element_id': label})
        self._guard(result)
        items = [i for i in result.get('items', []) if _same_text(i.get('label', ''), label)]
        if not items:
            raise _Stop('not_found', reason='ocr_no_match')
        if len(items) > 1:
            raise _Stop('ambiguous', match_count=len(items))
        tap = self._action('tap', {'x': items[0]['center_x'], 'y': items[0]['center_y']})
        self._guard(tap)
        check = tap.get('page_check')
        if isinstance(check, dict) and check.get('same_page') is False:
            return Outcome('unverified', {'via': 'ocr', 'reason': 'screen_changed_before_tap'})
        return self._after_tap(tap, via='ocr')

    @staticmethod
    def _after_tap(result, via):
        settle = result.get('settle') if isinstance(result.get('settle'), dict) else {}
        detail = {'via': via, 'screen_changed': settle.get('screen_changed'), 'waited_ms': settle.get('waited_ms')}
        if settle.get('screen_changed') is False:
            return Outcome('no_visible_change', detail)
        return Outcome('tapped', detail)

    # ------------------------------------------------------------ 输入
    def type_text(self, text, *, clear=False):
        """把文字原样输入当前焦点框，支持中文。绝不提交：不传 submit，也不按回车。"""
        return self._run(lambda: self._type(text, clear))

    def _type(self, text, clear):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or '\n' in text or '\r' in text:
            raise _Stop('invalid')
        self._preflight()
        secure = self._rpc('page_source', {'visible_only': True, 'max_children': 50,
                                           'filter': {'type': 'securetextfield'}})
        self._guard(secure)
        if secure.get('total_returned') and 'secure' in str(secure.get('source', '')).lower():
            raise _Stop('unsafe_field')
        params = {'text': text}
        if clear:
            params['clear'] = True
        try:
            result = self._action('type', params)
        except _RpcError:
            # 输入可能已进去一部分，不能当作没执行。
            raise _Stop('unknown', method='type', reason='type_error') from None
        detail = {'via': result.get('via', 'keys'), 'warning': bool(result.get('warning'))}
        if result.get('warning'):
            return Outcome('unverified', detail)
        # 核实文字确实出现在屏幕上。部分自绘输入框读不到，这时如实报未核实，不重输。
        read = self._rpc('page_source', {'visible_only': True, 'max_children': 50, 'filter': {'text': text}})
        return Outcome('done' if text in str(read.get('source', '')) else 'unverified', detail)

    # ------------------------------------------------------------ 状态
    def health(self):
        """只读。不返回设备名，它常含机主姓名。"""
        def work():
            info = self._rpc('device_info')
            system = info.get('system') if isinstance(info.get('system'), dict) else {}
            return Outcome('done', {k: v for k, v in {
                'server_version': info.get('server_version'), 'protocol_version': info.get('protocol_version'),
                'skill_version': info.get('skill_version'), 'point_size': [info.get('point_width'), info.get('point_height')],
                'screen_locked': system.get('screen_locked'), 'foreground_app': system.get('foreground_app'),
                'thermal_state': system.get('thermal_state'), 'capabilities': info.get('capabilities')}.items()
                if v is not None})
        return self._run(work)


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def acceptance(driver, app, rounds, clock=time.monotonic):
    """真机验收：回主屏 → 打开 App → 核对前台，重复 rounds 次。遇到第一次失败不继续。"""
    runs = []
    for index in range(rounds):
        home = driver.home()
        if not home.ok:
            runs.append({'round': index + 1, 'step': 'home', 'status': home.status, **home.detail})
            break
        started = clock()
        opened = driver.open_app(app)
        runs.append({'round': index + 1, 'step': 'open', 'status': opened.status,
                     'ms': round((clock() - started) * 1000), 'foreground_app': opened.detail.get('foreground_app')})
        if opened.status != 'done':
            break
    times = [r['ms'] for r in runs if r['status'] == 'done']
    return {'app': app, 'rounds': rounds, 'passed': len(times),
            'median_ms': statistics.median(times) if times else None,
            'max_ms': max(times) if times else None, 'runs': runs}


def main():
    parser = argparse.ArgumentParser(description='Phone Pilot 的 iCTRL 执行层')
    parser.add_argument('--host', default=os.environ.get('ICTRL_HOST'), help='iPhone 地址，如 192.168.1.20:8200')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('pair', help='配对；在手机通知上核对指纹后点 Allow')
    sub.add_parser('health', help='只读检查')
    sub.add_parser('home', help='回主屏')
    sub.add_parser('open', help='打开 App').add_argument('app')
    tap = sub.add_parser('tap', help='按标签点击')
    tap.add_argument('label')
    tap.add_argument('--type', dest='element_type')
    tap.add_argument('--contains', action='store_true')
    tap.add_argument('--ocr', action='store_true')
    sub.add_parser('type', help='在当前输入框输入，不提交').add_argument('text')
    accept = sub.add_parser('accept', help='连续打开 App 验收')
    accept.add_argument('app')
    accept.add_argument('--rounds', type=int, default=10)
    args = parser.parse_args()
    if not args.host:
        parser.error('需要 --host 或环境变量 ICTRL_HOST')
    if args.cmd == 'pair':
        # 按设备保存密钥。客户端会打印指纹，和手机通知上的核对后再点 Allow。
        try:
            ictrl_rpc.device_call(args.host, 'device_info', pair_as='phone-pilot', timeout=RPC_TIMEOUT)
        except (OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, 'reason', exc)
            if isinstance(reason, ConnectionRefusedError):
                print(f'连不上 {args.host}：手机上这个端口没有开放。检查 runner 是否在运行，并且已开启远程访问。', file=sys.stderr)
            else:
                print(f'连不上 {args.host}：{type(reason).__name__}。检查 IP 是否正确，以及手机和 Mac 是否在同一个 Wi-Fi。', file=sys.stderr)
            return 1
        print('配对完成')
        return 0
    driver = Driver(args.host)
    if args.cmd == 'accept':
        report = acceptance(driver, args.app, args.rounds)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['passed'] == args.rounds else 1
    outcome = {'health': driver.health, 'home': driver.home}[args.cmd]() if args.cmd in ('health', 'home') else (
        driver.open_app(args.app) if args.cmd == 'open' else
        driver.type_text(args.text) if args.cmd == 'type' else
        driver.tap(args.label, element_type=args.element_type,
                   match='contains' if args.contains else None, allow_ocr=args.ocr))
    print(json.dumps(dict(asdict(outcome), ok=outcome.ok, message=outcome.message), ensure_ascii=False, indent=2))
    return 0 if outcome.ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
