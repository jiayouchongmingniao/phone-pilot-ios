"""One-shot native touch calibration. No cursor is inferred from screen pixels."""
import math
import secrets
import time
import uuid
import pointer

GEOMETRY = [390, 844, 'portrait']
ALGORITHM = 'home-yx7-grid-v3'
REPORT_UNITS = 7
TOUCH_WINDOW_MS = 5000
ARM_PREPARE_SECONDS = 3
COARSE_PROBES = ((84, 126), (196, 406))
FIT_TARGETS = ((50, 90), (340, 750), (50, 750), (340, 90), (195, 420))
VALIDATION_TARGETS = ((300, 700), (310, 160))
TOTAL_POINTS = len(COARSE_PROBES)+len(FIT_TARGETS)+len(VALIDATION_TARGETS)
USB_ERRORS = ('none', 'ready_timeout', 'submit_failed', 'transfer_failed',
              'transfer_timeout', 'stopped', 'not_ready', 'busy', 'invalid_job', 'job_timeout')


def geometry(frame):
    return [frame[k] for k in ('point_width', 'point_height', 'orientation')]


def units_for(target, fit):
    # A shorter last report changes the movement profile. Use the same full
    # reports for fitting, heldouts and normal positioning; refit the offset.
    units = tuple(REPORT_UNITS*round((target[a]-fit['offset'][a])/
                                    (REPORT_UNITS*fit['gain'][a])) for a in (0, 1))
    if any(not 0 <= u <= pointer.MAX_UNITS for u in units) or sum(units) > 3000:
        raise ValueError('校准后的移动量越界，请重新校准')
    return units


def rough_fit(samples):
    """Two points only locate the wider probes; never authorize normal clicks."""
    first, second = samples
    result = dict(gain=[], offset=[])
    for axis, span in ((0, 40), (1, 80)):
        delta = second[axis+2]-first[axis+2]
        gain = delta/(second[axis]-first[axis])
        offset = first[axis+2]-gain*first[axis]
        if delta < span or not .1 <= gain <= 3 or abs(offset) > 80:
            raise ValueError('粗测范围不足或移动不一致，请重新校准')
        result['gain'].append(gain)
        result['offset'].append(offset)
    return result


def check_coverage(samples, fit):
    # Heldouts must interpolate the actual fitted range, not test an extrapolation.
    for target in VALIDATION_TARGETS:
        units = units_for(target, fit)
        for axis in (0, 1):
            positions = [s[axis+2] for s in samples]
            inputs = [s[axis] for s in samples]
            if (not min(positions)+pointer.TOLERANCE <= target[axis] <= max(positions)-pointer.TOLERANCE or
                not min(inputs) <= units[axis] <= max(inputs)):
                raise ValueError('校准采样未覆盖验证范围，请重新校准')


class Session:
    def __init__(self, page, geometry, now=None, require_delivery=False):
        try: uuid.UUID(page)
        except (ValueError, TypeError, AttributeError): raise ValueError('校准页面标识无效')
        if geometry != GEOMETRY: raise ValueError('校准仅支持 iPhone 16e 竖屏')
        now = time.monotonic() if now is None else now
        self.id = secrets.token_hex(16)
        self.page = page
        self.geometry = list(geometry)
        self.started = self.heartbeat = now
        self.index = 0
        self.coarse_samples = []
        self.samples = []
        self.validations = []
        self.touch_types = set()
        self.require_delivery = require_delivery
        self.usb_reports = []
        self.timing = []
        self.failure_context = self.device_abort = None
        self.last_op = None
        self.rough = self.candidate = self.verified = None
        self.error = None
        self._next(now)

    @property
    def active(self):
        return self.phase not in ('done', 'failed')

    @property
    def stage(self):
        if self.index < len(COARSE_PROBES): return 'coarse'
        if self.index < len(COARSE_PROBES)+len(FIT_TARGETS): return 'fit'
        return 'validate'

    def fail(self, message, now=None):
        if self.phase == 'failed': return self.reply()
        now = time.monotonic() if now is None else now
        self.failure_context = dict(phase_before_failure=self.phase, last_op=self.last_op,
            heartbeat_age_ms=max(0, round((now-self.heartbeat)*1000)),
            permit_age_ms=None if self.permitted is None else max(0, round((now-self.permitted)*1000)),
            point_received=self.point is not None, clicked_received=self.clicked)
        self.phase = 'failed'
        self.error = message
        self.verified = None
        return self.reply()

    def _next(self, now):
        self.probe = secrets.token_hex(16)
        self.phase = 'move'
        self.units = None
        self.sample_started = now
        self.arm_available = None
        self.permitted = None
        self.point = None
        self.clicked = False
        self.timing.append(dict(step=self.index+1, move_command_ms=None, moved_ms=None,
            armed_ms=None, click_command_ms=None, permit_ms=None, point_ms=None,
            clicked_ms=None, cancel_ms=None))
        if self.stage == 'coarse': self.units = COARSE_PROBES[self.index]
        elif self.stage == 'fit':
            self.units = units_for(FIT_TARGETS[self.index-len(COARSE_PROBES)], self.rough)
        else:
            self.units = units_for(VALIDATION_TARGETS[self.index-len(COARSE_PROBES)-len(FIT_TARGETS)], self.candidate)

    def _event(self, op, now):
        # Request timestamps mean arrival after identity checks, not acceptance.
        # Command timestamps mean generated here, not received by the device.
        self.last_op = op if op in ('command', 'poll', 'moved', 'armed', 'permit',
                                    'point', 'clicked', 'cancel', 'abort') else 'invalid'
        if op not in ('moved', 'armed', 'permit', 'point', 'clicked', 'cancel'): return
        key = op+'_ms'
        row = self.timing[-1]
        if key in row and row[key] is None:
            row[key] = max(0, round((now-self.sample_started)*1000))

    def tick(self, now):
        if not self.active: return
        if now-self.started > 120 or now-self.sample_started > 15:
            self.fail('校准超时，请在 iPhone 重新开始', now)
        elif now-self.heartbeat > 2:
            self.fail('校准页面已离线，已停止点击', now)
        elif (self.phase in ('arm', 'click') and self.permitted is None and self.arm_available is not None and
              now-self.arm_available > ARM_PREPARE_SECONDS):
            self.fail('点击准备已过期，请重新校准', now)
        # A timely native touch proves the click already happened. Give its USB
        # receipt a bounded transport allowance, never more time to click or a retry.
        elif self.permitted is not None and now-self.permitted > (5 if self.point is not None else 2):
            self.fail('点击回报超时，已停止校准', now)

    def reply(self):
        result = dict(id=self.id, probe=self.probe, phase=self.phase,
                      step=min(self.index+1, TOTAL_POINTS), total=TOTAL_POINTS)
        if self.phase == 'move': result.update(dx=self.units[0], dy=self.units[1], report_units=REPORT_UNITS)
        if self.error: result['error'] = self.error
        return result

    def diagnostic(self):
        # Numeric evidence only; no image/audio, credential or page identity.
        return dict(phase=self.phase, stage=self.stage, step=min(self.index+1, TOTAL_POINTS), error=self.error,
                    algorithm=ALGORITHM, report_units=REPORT_UNITS,
                    coarse_samples=[list(sample) for sample in self.coarse_samples], rough=self.rough,
                    samples=[list(sample) for sample in self.samples],
                    candidate=self.candidate, validations=list(self.validations),
                    delivery_required=self.require_delivery, usb_reports=list(self.usb_reports),
                    timing=[dict(row) for row in self.timing],
                    failure_context=dict(self.failure_context) if self.failure_context else None,
                    device_abort=dict(self.device_abort) if self.device_abort else None,
                    arm_available_ms=None if self.arm_available is None else round((self.arm_available-self.sample_started)*1000),
                    requested_units=list(self.units) if self.units is not None else None, observed_point=self.point,
                    touch_types=sorted(self.touch_types), verified=self.verified is not None)

    def command(self, now=None):
        now = time.monotonic() if now is None else now
        self._event('command', now)
        self.tick(now)
        result = self.reply()
        if self.phase == 'arm' or (self.phase == 'click' and self.permitted is not None):
            result['phase'] = 'wait'
        if result['phase'] in ('move', 'click'):
            row = self.timing[-1]
            key = result['phase']+'_command_ms'
            if row[key] is None: row[key] = max(0, round((now-self.sample_started)*1000))
        return result

    def _identity(self, body, probe=True):
        if body.get('id') != self.id: raise ValueError('校准会话已失效')
        if probe and body.get('probe') != self.probe: raise ValueError('校准探测已失效')

    def _delivery(self, body, op):
        report = body.get('usb')
        if 'usb' not in body and not self.require_delivery: return
        if not isinstance(report, dict): raise ValueError('缺少 USB 传输回报，校准已停止')
        clean = {}
        for key in ('planned', 'submitted', 'completed', 'duration_ms', 'min_gap_ms', 'max_gap_ms'):
            value = report.get(key)
            limit = 512 if key in ('planned', 'submitted', 'completed') else 10000
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError('USB 传输统计无效，校准已停止')
            clean[key] = value
        error = report.get('error')
        if (error not in USB_ERRORS or not clean['completed'] <= clean['submitted'] <= clean['planned'] or
            clean['min_gap_ms'] > clean['max_gap_ms']):
            raise ValueError('USB 传输统计无效，校准已停止')
        clean.update(step=self.index+1, op=op, error=error)
        self.usb_reports.append(clean)
        expected = 30+sum((u+REPORT_UNITS-1)//REPORT_UNITS for u in self.units) if op == 'moved' else 2
        if body.get('ok') is True and (error != 'none' or
            not clean['planned'] == clean['submitted'] == clean['completed'] == expected or
            clean['min_gap_ms'] < 10 or clean['max_gap_ms'] > clean['duration_ms'] or
            clean['duration_ms'] < (clean['submitted']-1)*clean['min_gap_ms']):
            raise ValueError('USB 报告未全部确认，校准已停止')

    def phone(self, body, now=None):
        now = time.monotonic() if now is None else now
        op = body.get('op')
        self._identity(body, op not in ('poll', 'cancel'))
        if body.get('page') != self.page: raise ValueError('校准页面已失效')
        self._event(op, now)
        self.tick(now)
        if op == 'cancel':
            return self.fail('用户已取消校准', now) if self.active else self.reply()
        if not self.active: return self.reply()
        try:
            if body.get('geometry') != self.geometry: raise ValueError('屏幕尺寸或方向已变化')
            self.heartbeat = now
            if op == 'poll': return self.reply()
            if op == 'armed':
                if self.phase != 'arm': raise ValueError('校准接收窗口重复或失效')
                self.phase = 'click'
            elif op == 'point':
                if self.phase != 'click' or self.permitted is None or self.point is not None:
                    raise ValueError('触控回报重复或未获点击许可')
                x, y = body.get('x'), body.get('y')
                if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)) or
                    not (6 < x < 384 and 60 < y < 838)):
                    raise ValueError('触控坐标无效或靠近边缘')
                kind = body.get('touch_type')
                if kind not in ('direct', 'indirectPointer'): raise ValueError('触控事件类型不支持')
                self.point = (x, y)
                self.touch_types.add(kind)
                self._complete(now)
            else: raise ValueError('校准请求无效')
            return self.reply()
        except ValueError as exc:
            self.fail(str(exc), now)
            raise

    def device(self, body, now=None):
        now = time.monotonic() if now is None else now
        self._identity(body)
        op = body.get('op')
        self._event(op, now)
        self.tick(now)
        if not self.active and (op != 'abort' or self.phase != 'failed'):
            raise ValueError(self.error or '校准已结束')
        try:
            if op == 'abort':
                elapsed = body.get('elapsed_ms')
                if (body.get('reason') != 'permit_expired' or type(elapsed) is not int or
                    not 0 <= elapsed <= 10000 or self.permitted is None):
                    raise ValueError('圆屏停止回报无效或未获点击许可')
                if self.clicked: raise ValueError('圆屏停止回报与已确认点击矛盾')
                if self.device_abort is None:
                    self.device_abort = dict(step=self.index+1, reason='permit_expired', elapsed_ms=elapsed)
                if self.active: self.fail('圆屏点击许可已过期，未执行点击', now)
            elif op == 'moved':
                if self.phase != 'move': raise ValueError('USB 移动回报重复')
                self._delivery(body, op)
                if body.get('ok') is not True:
                    raise ValueError('USB 移动失败或回报重复')
                # This precedes the phone's local arm, so network delay cannot
                # shift the permission deadline later than its receive window.
                self.arm_available = now
                self.phase = 'arm'
            elif op == 'permit':
                if (self.phase != 'click' or self.permitted is not None or now-self.heartbeat > .8 or
                    self.arm_available is None or now-self.arm_available > ARM_PREPARE_SECONDS):
                    raise ValueError('点击许可失效，请保持校准页前台')
                self.permitted = now
            elif op == 'clicked':
                if self.phase != 'click' or self.permitted is None or self.clicked:
                    raise ValueError('USB 点击回报重复或未获许可')
                self._delivery(body, op)
                if body.get('ok') is not True:
                    raise ValueError('USB 点击失败或回报重复')
                self.clicked = True
                self._complete(now)
            else: raise ValueError('校准设备请求无效')
            return {'ok': True}
        except ValueError as exc:
            self.fail(str(exc), now)
            raise

    def _complete(self, now):
        if self.point is None or not self.clicked: return
        if self.stage == 'coarse':
            self.coarse_samples.append((*self.units, *self.point))
            if len(self.coarse_samples) == len(COARSE_PROBES): self.rough = rough_fit(self.coarse_samples)
        elif self.stage == 'fit':
            self.samples.append((*self.units, *self.point))
            if len(self.samples) == len(FIT_TARGETS):
                self.candidate = pointer.fit(self.samples)
                check_coverage(self.samples, self.candidate)
        else:
            target = VALIDATION_TARGETS[self.index-len(COARSE_PROBES)-len(FIT_TARGETS)]
            errors = [round(self.point[a]-target[a], 3) for a in (0, 1)]
            self.validations.append(dict(target=list(target), observed=list(self.point), error_points=errors))
            if max(abs(self.point[a]-target[a]) for a in (0, 1)) > pointer.TOLERANCE:
                raise ValueError('独立验证点偏差过大，校准未生效，请重试')
        self.index += 1
        if self.index == TOTAL_POINTS:
            self.verified = dict(self.candidate, verified=True, geometry=list(self.geometry),
                                 algorithm=ALGORITHM, touch_types=sorted(self.touch_types))
            self.phase = 'done'
        else: self._next(now)


class MappedJob:
    """Use a native-verified map; actual landing is not observable in other apps."""
    def __init__(self, action, frame, fit):
        if not fit or not fit.get('verified') or fit.get('algorithm') != ALGORITHM or fit.get('geometry') != geometry(frame):
            raise ValueError('请先在 iPhone 手机遥控中完成点击校准')
        target = ((300 if action['direction'] == 'left' else 90), 700) if action['type'] == 'swipe' else (action['x'], action['y'])
        if not (6 < target[0] < 384 and 60 < target[1] < 838): raise ValueError('目标靠近屏幕边缘，已停止')
        self.action, self.origin, self.calibration = action, frame, fit
        self.units = units_for(target, fit)
        errors = [self.units[a]*fit['gain'][a]+fit['offset'][a]-target[a] for a in (0, 1)]
        if max(map(abs, errors)) > pointer.TOLERANCE:
            raise ValueError('目标定位精度不足，请在手机上继续')
        self.deadline = time.monotonic()+60
        self.issued = self.ready = self.authorized = False
        self.barrier = None
        self.failure = None
        self.recheck = None
        self.diagnostic = dict(phase='aiming', target=list(target), calibration_verified=True, landing_observed=False,
                               move_issued=False, move_acknowledged=False, post_move_frame_seen=False,
                               quantization_error_points=[round(error, 3) for error in errors])

    def advance(self, frame, body):
        if self.failure: raise ValueError(self.failure)
        if not frame or time.monotonic() > self.deadline: raise ValueError('定位超时或共享中断')
        if geometry(frame) != self.calibration['geometry']: raise ValueError('屏幕尺寸或方向已变化，请重新校准')
        if 'seq' in body:
            if not self.issued or self.barrier is not None or type(body['seq']) is not int or body['seq'] != 1 or body.get('ok') is not True:
                raise ValueError('USB 移动失败或回报重复')
            self.barrier = frame['sequence']
            self.diagnostic['move_acknowledged'] = True
            return {'wait': True}
        if not self.issued:
            if body.get('poll'): raise ValueError('定位尚未开始')
            self.issued = True
            self.diagnostic['move_issued'] = True
            return {'move': dict(seq=1, home=True, dx=self.units[0], dy=self.units[1],
                                 report_units=REPORT_UNITS), 'calibrating': False}
        if self.barrier is None: raise ValueError('移动未回报，禁止重复执行')
        if frame['sequence'] <= self.barrier: return {'wait': True}
        self.diagnostic['post_move_frame_seen'] = True
        result = {'ready': True}
        if self.action['type'] == 'swipe':
            drag = REPORT_UNITS*round(210/(REPORT_UNITS*self.calibration['gain'][0]))*(-1 if self.action['direction'] == 'left' else 1)
            if not 1 <= abs(drag) <= pointer.MAX_UNITS: raise ValueError('翻页移动量越界')
            if abs(abs(drag)*self.calibration['gain'][0]-210) > pointer.TOLERANCE:
                raise ValueError('翻页定位精度不足，请在手机上继续')
            result['drag_units'] = drag
        self.ready = True
        self.diagnostic['phase'] = 'mapped'
        return result

    def check(self, frame):
        self.authorized = bool(self.ready and not self.failure and frame and
            time.monotonic() <= self.deadline and frame['sequence'] > self.barrier and
            geometry(frame) == self.calibration['geometry'])
        return self.authorized
