"""Offline native-touch calibration contract tests; no device or model calls."""
import base64
import io
import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from PIL import Image

import calibration
import pointer
import server


GEOMETRY = [390, 844, 'portrait']
PAGE = 'e847c00d-65a5-42bd-9b9a-c2186c841f2c'


def synthetic_position(ux, uy):
    """Illustrative affine response only; this is not a measured grid-v3 device."""
    return .87*ux+13, .87*uy-2


def legacy_units_for(target, fit):
    """Replay recorded v1 inputs without rewriting history as whole-report data."""
    return tuple(round((target[a]-fit['offset'][a])/fit['gain'][a]) for a in (0, 1))


def capture_payload(size=(390, 844), geometry=GEOMETRY, fill='white'):
    image = Image.new('RGB', size, fill)
    out = io.BytesIO()
    image.save(out, format='JPEG')
    return dict(jpeg=base64.b64encode(out.getvalue()).decode(),
                width=size[0], height=size[1], point_width=geometry[0],
                point_height=geometry[1], orientation=geometry[2])


def phone_body(session, op, **fields):
    return dict(op=op, id=session.id, page=session.page,
                geometry=list(session.geometry), probe=session.probe, **fields)


def device_body(session, op, **fields):
    body = dict(op=op, id=session.id, probe=session.probe, **fields)
    if op in ('moved', 'clicked') and body.get('ok') is True and 'usb' not in body:
        body['usb'] = usb_report(session, op)
    return body


def usb_report(session, op, **changes):
    count = 30+sum(math.ceil(units/calibration.REPORT_UNITS) for units in session.units) if op == 'moved' else 2
    result = dict(planned=count, submitted=count, completed=count, error='none',
                  duration_ms=count*15 if op == 'moved' else 80,
                  min_gap_ms=10 if op == 'moved' else 60,
                  max_gap_ms=15 if op == 'moved' else 60)
    result.update(changes)
    return result


def calibrate_state(state, position=synthetic_position):
    """Complete actual State protocol with synthetic touch events, never HID."""
    state.calibration_phone(dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000))
    session = state.calibration_session
    for index in range(9):
        state.calibration_phone(phone_body(session, 'poll'))
        state.calibration_device(device_body(session, 'moved', ok=True))
        state.calibration_phone(phone_body(session, 'armed'))
        state.calibration_device(device_body(session, 'permit'))
        ux, uy = session.units
        x, y = position(ux, uy)
        point = phone_body(session, 'point', x=x, y=y,
                           touch_type='indirectPointer')
        clicked = device_body(session, 'clicked', ok=True)
        if index % 2:
            state.calibration_device(clicked)
            state.calibration_phone(point)
        else:
            state.calibration_phone(point)
            state.calibration_device(clicked)
    return state.pointer_calibration


class SessionHarness:
    def __init__(self, position=None, require_delivery=False):
        self.now = 100.0
        self.position = position or synthetic_position
        self.session = calibration.Session(PAGE, list(GEOMETRY), now=self.now,
                                           require_delivery=require_delivery)

    def phone(self, op, **fields):
        body = dict(op=op, id=self.session.id, page=PAGE,
                    geometry=list(GEOMETRY), probe=self.session.probe)
        body.update(fields)
        return self.session.phone(body, now=self.now)

    def device(self, op, **fields):
        body = dict(op=op, id=self.session.id, probe=self.session.probe)
        body.update(fields)
        return self.session.device(body, now=self.now)

    def permit(self, delivery=False):
        self.phone('poll')
        self.device('moved', ok=True, **(dict(usb=usb_report(self.session, 'moved')) if delivery else {}))
        self.phone('armed')
        return self.device('permit')

    def point(self, **changes):
        ux, uy = self.session.units
        x, y = self.position(ux, uy)
        fields = dict(x=x, y=y, touch_type='indirectPointer')
        fields.update(changes)
        return self.phone('point', **fields)

    def sample(self, point_first=True, delivery=False, **changes):
        self.permit(delivery=delivery)
        clicked = dict(usb=usb_report(self.session, 'clicked')) if delivery else {}
        if point_first:
            self.point(**changes)
            self.device('clicked', ok=True, **clicked)
        else:
            self.device('clicked', ok=True, **clicked)
            self.point(**changes)
        self.now += .1


class WholeReportGridTests(unittest.TestCase):
    def test_grid_version_and_coarse_points_discard_partial_reports(self):
        self.assertEqual(calibration.ALGORITHM, 'home-yx7-grid-v3')
        self.assertEqual(calibration.REPORT_UNITS, 7)
        self.assertEqual(calibration.COARSE_PROBES, ((84, 126), (196, 406)))

    def test_inverse_rounds_to_whole_reports_without_changing_the_target(self):
        fit = dict(gain=[.87, .87], offset=[13, -2])
        target = [300, 700]
        self.assertEqual(calibration.units_for(target, fit), (329, 805))
        self.assertEqual(target, [300, 700])

    def test_calibration_and_mapped_moves_advertise_seven_unit_reports(self):
        h = SessionHarness()
        self.assertEqual(h.session.command(now=h.now)['report_units'], 7)
        fit = dict(gain=[.87, .87], offset=[13, -2], verified=True,
                   algorithm=calibration.ALGORITHM, geometry=list(GEOMETRY))
        frame = dict(point_width=390, point_height=844, orientation='portrait', sequence=1)
        diagnostic_fields = ('move_issued', 'move_acknowledged', 'post_move_frame_seen')
        for direction, drag in (('left', -238), ('right', 238)):
            with self.subTest(direction=direction):
                job = calibration.MappedJob(dict(type='swipe', direction=direction), frame, fit)
                self.assertIsNone(job.recheck)
                self.assertEqual(tuple(job.diagnostic[key] for key in diagnostic_fields), (False, False, False))
                move = job.advance(frame, {})['move']
                self.assertEqual(move['report_units'], 7)
                self.assertEqual((move['dx'] % 7, move['dy'] % 7), (0, 0))
                self.assertEqual(tuple(job.diagnostic[key] for key in diagnostic_fields), (True, False, False))
                with self.assertRaises(ValueError): job.advance(frame, dict(seq=2, ok=True))
                self.assertFalse(job.diagnostic['move_acknowledged'])
                job.advance(frame, dict(seq=1, ok=True))
                self.assertEqual(tuple(job.diagnostic[key] for key in diagnostic_fields), (True, True, False))
                self.assertEqual(job.advance(frame, dict(poll=True)), {'wait': True})
                self.assertFalse(job.diagnostic['post_move_frame_seen'])
                self.assertEqual(job.advance(dict(frame, sequence=2), dict(poll=True))['drag_units'], drag)
                self.assertEqual(tuple(job.diagnostic[key] for key in diagnostic_fields), (True, True, True))
                self.assertFalse(job.authorized)

    def test_old_fourteen_unit_device_cannot_begin_calibration(self):
        state = server.State()
        state.pair('phone', state.code)
        state.pair('device', state.code, ['native_touch_v1', 'hid_delivery_v1'])
        state.add_frame(capture_payload())
        with self.assertRaisesRegex(ValueError, '7'):
            state.calibration_phone(dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000))
        self.assertIsNone(state.calibration_session)

    def test_drag_distance_preserves_six_point_quantization_boundary(self):
        frame = dict(point_width=390, point_height=844, orientation='portrait', sequence=1)
        for error in (6, 6.001):
            with self.subTest(error=error):
                gain = (210-error)/105
                fit = dict(gain=[gain, 1], offset=[300-154*gain, 0], verified=True,
                           algorithm=calibration.ALGORITHM, geometry=list(GEOMETRY))
                job = calibration.MappedJob(dict(type='swipe', direction='left'), frame, fit)
                job.advance(frame, {})
                job.advance(frame, dict(seq=1, ok=True))
                if error == 6:
                    self.assertEqual(job.advance(dict(frame, sequence=2), dict(poll=True))['drag_units'], -105)
                else:
                    with self.assertRaisesRegex(ValueError, '精度不足'):
                        job.advance(dict(frame, sequence=2), dict(poll=True))
                    self.assertTrue(job.diagnostic['post_move_frame_seen'])
                    self.assertFalse(job.ready)
                    self.assertFalse(job.check(dict(frame, sequence=2)))

    def test_low_gain_drag_still_respects_unit_limit_after_grid_rounding(self):
        frame = dict(point_width=390, point_height=844, orientation='portrait', sequence=1)
        for gain in (210/1995, .105, .1):
            with self.subTest(gain=gain):
                fit = dict(gain=[gain, 1], offset=[0, 0], verified=True,
                           algorithm=calibration.ALGORITHM, geometry=list(GEOMETRY))
                job = calibration.MappedJob(dict(type='swipe', direction='right'), frame, fit)
                job.advance(frame, {})
                job.advance(frame, dict(seq=1, ok=True))
                if gain == 210/1995:
                    self.assertEqual(job.advance(dict(frame, sequence=2), dict(poll=True))['drag_units'], 1995)
                else:
                    with self.assertRaisesRegex(ValueError, '移动量越界'):
                        job.advance(dict(frame, sequence=2), dict(poll=True))
                    self.assertFalse(job.ready)

    def test_high_gain_mapping_rejects_unreachable_target_before_movement(self):
        # Synthetic high gain: the closest whole-report Y position is 700.3, not 707.
        fit = dict(gain=[.9, 2.1], offset=[-12, -20], verified=True,
                   algorithm=calibration.ALGORITHM, geometry=list(GEOMETRY))
        frame = dict(point_width=390, point_height=844, orientation='portrait', sequence=1)
        action = dict(type='tap', x=300, y=707)
        with self.assertRaises(ValueError):
            calibration.MappedJob(action, frame, fit)
        self.assertEqual(action, dict(type='tap', x=300, y=707))

    def test_prediction_keeps_six_point_boundary_on_each_axis(self):
        fit = dict(gain=[2, 2], offset=[0, 0], verified=True,
                   algorithm=calibration.ALGORITHM, geometry=list(GEOMETRY))
        frame = dict(point_width=390, point_height=844, orientation='portrait', sequence=1)
        for axis in ('x', 'y'):
            with self.subTest(axis=axis):
                action = dict(type='tap', x=140, y=140)
                action[axis] = 76
                job = calibration.MappedJob(action, frame, fit)
                self.assertEqual(job.diagnostic['target'], [action['x'], action['y']])
                self.assertFalse(job.issued)
                action[axis] = 76.001
                with self.assertRaises(ValueError):
                    calibration.MappedJob(action, frame, fit)
                self.assertEqual(action[axis], 76.001)

    def test_old_algorithm_cache_cannot_authorize_a_grid_move(self):
        frame = dict(point_width=390, point_height=844, orientation='portrait', sequence=1)
        for algorithm in ('home-yx14-v1', 'home-yx14-grid-v2'):
            with self.subTest(algorithm=algorithm):
                fit = dict(gain=[.87, .87], offset=[13, -2], verified=True,
                           algorithm=algorithm, geometry=list(GEOMETRY))
                with self.assertRaises(ValueError):
                    calibration.MappedJob(dict(type='tap', x=300, y=700), frame, fit)


class NativeCalibrationSessionTests(unittest.TestCase):
    def test_synthetic_seven_unit_response_is_refitted_at_different_gains(self):
        # New step size has no measured response yet; none of these are device observations.
        for gains, offsets in (((.65, .72), (13, -2)), ((1.1, 1.3), (13, -20)),
                               ((.87, 2.1), (13, -20))):
            with self.subTest(gains=gains):
                h = SessionHarness(lambda ux, uy: (gains[0]*ux+offsets[0], gains[1]*uy+offsets[1]),
                                   require_delivery=True)
                for _ in range(9):
                    self.assertTrue(all(units % 7 == 0 for units in h.session.units))
                    h.sample(delivery=True)
                self.assertTrue(h.session.verified['verified'])
                for got, want in zip(h.session.verified['gain'], gains): self.assertAlmostEqual(got, want)
                self.assertEqual(len(h.session.validations), 2)
                self.assertEqual(len(h.session.usb_reports), 18)
                for point in h.session.validations:
                    self.assertLessEqual(max(map(abs, point['error_points'])), 6)

    @patch.object(calibration, 'REPORT_UNITS', 14)
    @patch.object(calibration, 'ALGORITHM', 'home-yx14-grid-v2')
    def test_recorded_fourteen_grid_failure_separates_quantization_and_model_error(self):
        # Actual grid-v2 observations, replayed as 14-unit reports, never relabeled as v3.
        measured = [(84, 126, 87, 111.33333333333333), (196, 406, 184.33333333333331, 355),
                    (42, 98, 50.33333333333333, 87), (378, 854, 343, 745),
                    (42, 854, 50.33333333333333, 745), (378, 98, 343, 87),
                    (210, 476, 196.66666666666666, 416)]
        h = SessionHarness(require_delivery=True)
        for ux, uy, x, y in measured:
            self.assertEqual(h.session.units, (ux, uy))
            h.sample(x=x, y=y, delivery=True)
        self.assertEqual(h.session.units, (322, 798))
        fit = h.session.candidate
        self.assertLess(max(fit['rms']), .000001)
        predicted = [fit['gain'][a]*h.session.units[a]+fit['offset'][a] for a in (0, 1)]
        observed = (294, 693.6666666666666)
        for got, want in zip([predicted[0]-300, predicted[1]-700], (-5.7777778, -3.7407407)):
            self.assertAlmostEqual(got, want, places=5)
        for got, want in zip([observed[a]-predicted[a] for a in (0, 1)], (-.2222222, -2.5925926)):
            self.assertAlmostEqual(got, want, places=5)
        with self.assertRaisesRegex(ValueError, '独立验证'):
            h.sample(x=observed[0], y=observed[1], delivery=True)
        self.assertEqual(h.session.validations[0]['error_points'], [-6, -6.333])
        self.assertIsNone(h.session.verified)
        self.assertEqual(len(h.session.usb_reports), 16)

    def test_two_coarse_five_fit_points_and_both_heldouts_are_required(self):
        h = SessionHarness()
        self.assertTrue(h.session.active)
        self.assertEqual(calibration.TOTAL_POINTS, 9)
        self.assertEqual(calibration.VALIDATION_TARGETS, ((300, 700), (310, 160)))
        for index in range(9):
            self.assertTrue(all(units % 7 == 0 for units in h.session.units))
            self.assertEqual(h.session.reply()['report_units'], 7)
            self.assertEqual(h.session.reply()['total'], 9)
            self.assertEqual(h.session.reply()['step'], index+1)
            self.assertEqual(h.session.stage, 'coarse' if index < 2 else 'fit' if index < 7 else 'validate')
            self.assertIsNone(h.session.verified)
            h.sample(point_first=index % 2 == 0)
            if index < 6: self.assertIsNone(h.session.candidate)
            if index < 8:
                self.assertIsNone(h.session.verified)
            if index == 1:
                self.assertIsNotNone(h.session.rough)
                self.assertEqual(h.session.samples, [])
            if index == 6:
                self.assertIsNotNone(h.session.candidate)
                self.assertEqual(h.session.validations, [])
        self.assertEqual(h.session.phase, 'done')
        self.assertFalse(h.session.active)
        self.assertEqual(len(h.session.coarse_samples), 2)
        self.assertEqual(len(h.session.samples), 5)
        self.assertEqual(len(h.session.validations), 2)
        fit = h.session.verified
        self.assertTrue(fit['verified'])
        self.assertEqual(fit['geometry'], GEOMETRY)
        self.assertEqual(fit['algorithm'], 'home-yx7-grid-v3')
        for got, want in zip(fit['gain'], [.87, .87]):
            self.assertAlmostEqual(got, want)
        for got, want in zip(fit['offset'], [13, -2]):
            self.assertAlmostEqual(got, want)
        self.assertIn('indirectPointer', fit['touch_types'])

    def test_failure_preserves_numeric_measurements_without_identifiers(self):
        h = SessionHarness()
        for _ in range(7): h.sample()
        with self.assertRaisesRegex(ValueError, '独立验证'):
            h.sample(x=320, y=700)
        d = h.session.diagnostic()
        self.assertEqual(d['step'], 8)
        self.assertEqual(d['stage'], 'validate')
        self.assertEqual(len(d['coarse_samples']), 2)
        self.assertIsNotNone(d['rough'])
        self.assertEqual(len(d['samples']), 5)
        self.assertEqual(d['validations'][0]['error_points'], [20, 0])
        self.assertFalse(d['verified'])
        self.assertIsNotNone(d['candidate'])
        serialized = json.dumps(d)
        for private in (PAGE, h.session.id, h.session.probe): self.assertNotIn(private, serialized)

    def test_rough_points_only_schedule_probes_and_do_not_bias_final_fit(self):
        h = SessionHarness()
        for _ in range(2):
            ux, uy = h.session.units
            h.sample(x=.87*ux+16, y=.87*uy-4)
        self.assertEqual(h.session.samples, [])
        self.assertAlmostEqual(h.session.rough['offset'][0], 16)
        self.assertAlmostEqual(h.session.rough['offset'][1], -4)
        for _ in range(7): h.sample()
        self.assertEqual(len(h.session.coarse_samples), 2)
        self.assertEqual(len(h.session.samples), 5)
        for got, want in zip(h.session.verified['gain'], [.87, .87]):
            self.assertAlmostEqual(got, want)
        for got, want in zip(h.session.verified['offset'], [13, -2]):
            self.assertAlmostEqual(got, want)

    def test_recorded_old_fit_extrapolates_and_misses_y_by_eight_points(self):
        # Measured old five-point run; these are not synthetic pointer positions.
        samples = [(80, 128, 84.6667, 111.3333), (200, 400, 188.3333, 344),
                   (80, 400, 82.6667, 344), (200, 128, 190.3333, 111.3333),
                   (140, 264, 137, 229)]
        fit = pointer.fit(samples)
        # The recorded coordinates were rounded to four decimals.
        self.assertAlmostEqual(fit['gain'][0], .8805556, places=5)
        self.assertAlmostEqual(fit['gain'][1], .8553922, places=5)
        units = legacy_units_for((300, 700), fit)
        self.assertEqual(units, (326, 816))
        self.assertGreater(units[1], max(sample[1] for sample in samples))
        observed = (299.3333, 708.3333)
        self.assertAlmostEqual(observed[1]-700, 8.3333)
        self.assertEqual(pointer.TOLERANCE, 6)
        self.assertGreater(abs(observed[1]-700), pointer.TOLERANCE)
        # The wider protocol refuses this candidate before issuing a heldout.
        with self.assertRaisesRegex(ValueError, '覆盖'):
            calibration.check_coverage(samples, fit)

    def test_wide_fit_passes_an_illustrative_weak_curve_not_hardware_validation(self):
        def position(ux, uy):
            return .85*ux+.00002*ux*ux, .85*uy+.000025*uy*uy
        old_samples = [(*units, *position(*units)) for units in pointer.PROBES]
        old_fit = pointer.fit(old_samples)
        old_units = legacy_units_for((300, 700), old_fit)
        self.assertGreater(abs(position(*old_units)[1]-700), pointer.TOLERANCE)
        h = SessionHarness(position)
        for _ in range(9): h.sample()
        self.assertTrue(h.session.verified['verified'])
        self.assertEqual(len(h.session.validations), 2)
        for observed in h.session.validations:
            self.assertLessEqual(max(map(abs, observed['error_points'])), pointer.TOLERANCE)

    def test_recorded_v1_zero_y_fit_error_still_fails_second_independent_check(self):
        # Actual partial-report run, replayed with its original coarse/inverse rules.
        # These observations provide no hardware evidence for the new grid algorithm.
        measured = [(80, 128, 84.6667, 106.3333), (200, 400, 188.3333, 344),
                    (40, 109, 48.3333, 93), (376, 865, 340.6667, 751),
                    (40, 865, 48.3333, 751), (376, 109, 340.6667, 93),
                    (208, 487, 194.3333, 422), (329, 806, 298, 697.3333),
                    (341, 186, 308.6667, 151.6667)]
        with patch.object(calibration, 'COARSE_PROBES', ((80, 128), (200, 400))), \
             patch.object(calibration, 'REPORT_UNITS', 14), \
             patch.object(calibration, 'ALGORITHM', 'home-yx14-v1'), \
             patch.object(calibration, 'units_for', legacy_units_for):
            h = SessionHarness(require_delivery=True)
            for ux, uy, x, y in measured[:8]:
                self.assertEqual(h.session.units, (ux, uy))
                h.sample(x=x, y=y, delivery=True)
            self.assertAlmostEqual(h.session.candidate['rms'][1], 0)
            self.assertLess(h.session.candidate['rms'][0], .1)
            self.assertEqual({s[1] % 14 for s in h.session.samples}, {11})
            self.assertEqual(len(h.session.validations), 1)
            self.assertIsNone(h.session.verified)
            ux, uy, x, y = measured[-1]
            self.assertEqual(h.session.units, (ux, uy))
            self.assertEqual(uy % 14, 4)
            with self.assertRaisesRegex(ValueError, '独立验证'):
                h.sample(x=x, y=y, delivery=True)
            self.assertAlmostEqual(h.session.validations[-1]['error_points'][1], -8.3333, places=3)
            self.assertEqual(h.session.phase, 'failed')
            self.assertIsNone(h.session.verified)
            self.assertEqual(len(h.session.usb_reports), 18)

    @patch.object(calibration, 'REPORT_UNITS', 14)
    @patch.object(calibration, 'ALGORITHM', 'home-yx14-grid-v2')
    def test_synthetic_fourteen_grid_high_gain_does_not_shift_heldout_to_pass(self):
        h = SessionHarness(lambda ux, uy: (.9*ux-12, 1.3*uy-20))
        for _ in range(7): h.sample()
        self.assertEqual(h.session.units, (350, 560))
        with self.assertRaisesRegex(ValueError, '独立验证'):
            h.sample()
        self.assertEqual(h.session.validations[0]['error_points'], [3, 8])
        self.assertEqual(h.session.phase, 'failed')
        self.assertIsNone(h.session.verified)

    def test_observed_coverage_must_include_both_heldouts_with_six_point_margin(self):
        def samples(bounds):
            left, top, right, bottom = bounds
            points = [(left, top), (right, bottom), (left, bottom), (right, top),
                      ((left+right)/2, (top+bottom)/2)]
            return [(*point, *point) for point in points]
        exact = samples((294, 154, 340, 706))
        calibration.check_coverage(exact, pointer.fit(exact))
        for bounds in ((294.001, 90, 340, 750), (50, 90, 315.999, 750),
                       (50, 154.001, 340, 750), (50, 90, 340, 705.999)):
            with self.subTest(bounds=bounds):
                measured = samples(bounds)
                with self.assertRaisesRegex(ValueError, '覆盖'):
                    calibration.check_coverage(measured, pointer.fit(measured))

    def test_coverage_also_requires_inverse_units_inside_measured_input_range(self):
        samples = [(*point, *point) for point in calibration.FIT_TARGETS]
        for gains in ([.8, 1], [1, .8]):
            with self.subTest(gains=gains):
                # Screen bounds alone would pass; the supplied inverse would extrapolate.
                with self.assertRaisesRegex(ValueError, '覆盖'):
                    calibration.check_coverage(samples, dict(gain=gains, offset=[0, 0]))

    def test_insufficient_final_coverage_stops_before_any_heldout_is_issued(self):
        h = SessionHarness()
        h.sample(); h.sample()
        for _ in range(4):
            ux, uy = h.session.units
            h.sample(x=.87*ux+13, y=.6*uy+20)
        ux, uy = h.session.units
        with self.assertRaisesRegex(ValueError, '覆盖'):
            h.sample(x=.87*ux+13, y=.6*uy+20)
        self.assertEqual(h.session.phase, 'failed')
        self.assertEqual(len(h.session.samples), 5)
        self.assertEqual(h.session.validations, [])
        self.assertIsNone(h.session.verified)
        self.assertEqual(h.session.command(now=h.now)['phase'], 'failed')

    def test_bad_second_coarse_point_stops_without_any_final_fit_or_cache(self):
        for point in ((70, 500), (168, 155), (40, 500), (168, 100)):
            with self.subTest(point=point):
                h = SessionHarness()
                h.sample()
                with self.assertRaisesRegex(ValueError, '粗测'):
                    h.sample(x=point[0], y=point[1])
                self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(h.session.reply()['step'], 2)
                self.assertIsNone(h.session.rough)
                self.assertEqual(h.session.samples, [])
                self.assertEqual(h.session.validations, [])
                self.assertIsNone(h.session.candidate)
                self.assertIsNone(h.session.verified)

    def test_heldout_six_point_boundary_is_unchanged(self):
        for error in (6, 6.001):
            with self.subTest(error=error):
                h = SessionHarness()
                for _ in range(7): h.sample()
                if error == 6:
                    h.sample(x=300, y=700+error)
                    self.assertIsNone(h.session.verified)
                    h.sample()
                    self.assertTrue(h.session.verified['verified'])
                else:
                    with self.assertRaisesRegex(ValueError, '独立验证'):
                        h.sample(x=300, y=700+error)
                    self.assertIsNone(h.session.verified)

    def test_point_and_click_ack_can_arrive_in_either_order(self):
        for point_first in (False, True):
            with self.subTest(point_first=point_first):
                h = SessionHarness()
                h.permit()
                if point_first:
                    h.point()
                else:
                    h.device('clicked', ok=True)
                self.assertEqual(h.session.index, 0)
                self.assertIsNone(h.session.verified)
                if point_first:
                    h.device('clicked', ok=True)
                else:
                    h.point()
                self.assertEqual(h.session.index, 1)

    def test_polling_preserves_one_pending_probe(self):
        h = SessionHarness()
        identity = (h.session.id, h.session.probe, h.session.units)
        for _ in range(5):
            h.phone('poll')
            h.session.command(now=h.now)
            self.assertEqual((h.session.id, h.session.probe, h.session.units), identity)
            self.assertEqual(h.session.index, 0)
        self.assertEqual(h.permit()['ok'], True)
        for _ in range(5):
            h.phone('poll')
            h.session.command(now=h.now)
        with self.assertRaises(ValueError):
            h.device('permit')
        self.assertIsNone(h.session.verified)

    def test_touch_and_click_ack_before_permit_cannot_be_samples(self):
        for operation in ('point', 'clicked'):
            with self.subTest(operation=operation):
                h = SessionHarness()
                h.phone('poll')
                h.device('moved', ok=True)
                h.phone('armed')
                with self.assertRaises(ValueError):
                    h.point() if operation == 'point' else h.device('clicked', ok=True)
                self.assertIsNone(h.session.verified)
                self.assertEqual(h.session.index, 0)

    def test_permit_requires_movement_and_armed_phone(self):
        for moved in (False, True):
            with self.subTest(moved=moved):
                h = SessionHarness()
                h.phone('poll')
                if moved:
                    h.device('moved', ok=True)
                with self.assertRaises(ValueError):
                    h.device('permit')
                self.assertIsNone(h.session.verified)

    def test_permit_requires_a_recent_phone_heartbeat(self):
        h = SessionHarness()
        h.phone('poll')
        h.device('moved', ok=True)
        h.phone('armed')
        h.now += .81
        with self.assertRaises(ValueError):
            h.device('permit')
        self.assertIsNone(h.session.verified)

    def test_no_heartbeat_stops_the_session_and_command_reports_error(self):
        h = SessionHarness()
        h.now += 2.1
        reply = h.session.command(now=h.now)
        self.assertEqual(h.session.phase, 'failed')
        self.assertFalse(h.session.active)
        self.assertTrue(reply.get('error'))
        self.assertIsNone(h.session.verified)

    def test_per_probe_and_total_deadlines_apply_despite_heartbeats(self):
        h = SessionHarness()
        for _ in range(15):
            h.now += 1
            self.assertTrue(h.phone('poll').get('phase') != 'failed')
        h.now += .1
        self.assertTrue(h.session.command(now=h.now).get('error'))
        self.assertEqual(h.session.phase, 'failed')
        h = SessionHarness()
        # Advance real protocol stages with a recent heartbeat; isolate total age.
        h.session.started = h.now - 120.1
        self.assertTrue(h.session.command(now=h.now).get('error'))
        self.assertEqual(h.session.phase, 'failed')

    def test_click_observation_timeout_does_not_reissue_a_permit(self):
        h = SessionHarness()
        h.permit()
        h.now += 1.9
        h.phone('poll')
        h.now += .2
        reply = h.session.command(now=h.now)
        self.assertTrue(reply.get('error'))
        self.assertEqual(h.session.phase, 'failed')
        with self.assertRaises(ValueError):
            h.device('permit')
        self.assertIsNone(h.session.verified)

    def test_bad_phone_identity_page_geometry_or_probe_is_rejected(self):
        for changes in ({'id': 'old-session'}, {'page': 'other-page'},
                        {'geometry': [844, 390, 'landscape']},
                        {'geometry': [390, 843, 'portrait']}, {'probe': 'old-probe'}):
            with self.subTest(changes=changes):
                h = SessionHarness()
                h.phone('poll')
                h.device('moved', ok=True)
                with self.assertRaises(ValueError):
                    h.phone('armed', **changes)
                self.assertIsNone(h.session.verified)

    def test_bad_device_identity_or_probe_is_rejected(self):
        for changes in ({'id': 'old-session'}, {'probe': 'old-probe'}):
            with self.subTest(changes=changes):
                h = SessionHarness()
                with self.assertRaises(ValueError):
                    h.device('moved', ok=True, **changes)
                self.assertEqual(h.session.index, 0)
                self.assertIsNone(h.session.verified)

    def test_nonfinite_boolean_and_boundary_coordinates_are_rejected(self):
        for changes in ({'x': math.nan}, {'x': math.inf}, {'y': -math.inf},
                        {'x': True}, {'y': False}, {'x': '84'},
                        {'x': 6}, {'x': 384}, {'y': 60}, {'y': 838},
                        {'x': -1}, {'y': 900}):
            with self.subTest(changes=changes):
                h = SessionHarness()
                h.permit()
                with self.assertRaises(ValueError):
                    h.point(**changes)
                self.assertEqual(h.session.index, 0)
                self.assertIsNone(h.session.verified)

    def test_unknown_touch_type_is_rejected_and_direct_is_recorded(self):
        for kind in ('pencil', 'mouse', '', None):
            with self.subTest(kind=kind):
                h = SessionHarness()
                h.permit()
                with self.assertRaises(ValueError):
                    h.point(touch_type=kind)
                self.assertIsNone(h.session.verified)
        h = SessionHarness()
        for _ in range(9):
            h.sample(touch_type='direct')
        self.assertIn('direct', h.session.verified['touch_types'])

    def test_duplicate_point_or_completion_never_advances_another_probe(self):
        h = SessionHarness()
        old_probe = h.session.probe
        h.sample()
        self.assertNotEqual(h.session.probe, old_probe)
        with self.assertRaises(ValueError):
            h.device('clicked', ok=True, probe=old_probe)
        self.assertEqual(h.session.index, 1)
        self.assertIsNone(h.session.verified)
        h = SessionHarness()
        h.permit()
        h.point()
        with self.assertRaises(ValueError):
            h.point()
        self.assertEqual(h.session.index, 0)

    def test_usb_failure_never_becomes_a_valid_sample(self):
        for operation in ('moved', 'clicked'):
            with self.subTest(operation=operation):
                h = SessionHarness()
                if operation == 'clicked':
                    h.permit()
                    h.point()
                with self.assertRaises(ValueError):
                    h.device(operation, ok=False)
                self.assertIsNone(h.session.verified)

    def test_either_bad_heldout_rejects_the_entire_candidate(self):
        for heldout in (7, 8):
            with self.subTest(heldout=heldout):
                h = SessionHarness()
                for _ in range(heldout):
                    h.sample()
                self.assertIsNone(h.session.verified)
                h.permit()
                target = calibration.VALIDATION_TARGETS[heldout-7]
                with self.assertRaises(ValueError):
                    h.point(x=target[0]+10, y=target[1])
                    h.device('clicked', ok=True)
                self.assertEqual(h.session.phase, 'failed')
                self.assertIsNone(h.session.verified)

    def test_cancel_blocks_all_remaining_calibration_actions(self):
        for stage in ('move', 'arm', 'click', 'permitted'):
            with self.subTest(stage=stage):
                h = SessionHarness()
                if stage != 'move':
                    h.device('moved', ok=True)
                if stage in ('click', 'permitted'):
                    h.phone('armed')
                if stage == 'permitted':
                    h.device('permit')
                h.phone('cancel')
                self.assertFalse(h.session.active)
                self.assertIsNone(h.session.verified)
                self.assertTrue(h.session.command(now=h.now).get('error'))
                with self.assertRaises(ValueError):
                    h.device('permit')


class CalibrationTimingTests(unittest.TestCase):
    def test_recorded_prepare_delays_accept_a_synthetic_touch_inside_the_window(self):
        # Recorded step-3 timing; the touch is a hypothetical valid event, not a new device result.
        h = SessionHarness(require_delivery=True)
        for _ in range(2): h.sample(delivery=True)
        start = h.session.sample_started
        h.now = start+.717; h.device('moved', ok=True, usb=usb_report(h.session, 'moved'))
        h.now = start+.898; h.phone('armed')
        h.now = start+1.856; self.assertEqual(h.session.command(now=h.now)['phase'], 'click')
        for elapsed in (1.9, 2.5, 2.9):
            h.now = start+elapsed; h.phone('poll')
        h.now = start+3.095
        self.assertEqual(h.device('permit'), {'ok': True})
        self.assertEqual(h.session.diagnostic()['arm_available_ms'], 717)
        h.now = start+3.2; h.point()
        h.now = start+3.213; h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked'))
        self.assertEqual(h.session.index, 3)
        self.assertIsNone(h.session.arm_available)
        self.assertIsNone(h.session.verified)

    def test_prepare_deadline_is_anchored_to_moved_not_armed_or_fresh_heartbeat(self):
        for elapsed in (3, 3.001):
            with self.subTest(elapsed=elapsed):
                h = SessionHarness()
                h.device('moved', ok=True)
                h.now = 101; h.phone('poll')
                h.now = 102; h.phone('armed')
                h.now = 102.9; h.phone('poll')
                h.now = 100+elapsed
                if elapsed == 3:
                    self.assertEqual(h.device('permit'), {'ok': True})
                    h.now += .001
                    self.assertEqual(h.session.command(now=h.now)['phase'], 'wait')
                else:
                    with self.assertRaisesRegex(ValueError, '点击准备已过期'): h.device('permit')
                    self.assertIsNone(h.session.permitted)
                    self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(h.session.index, 0)

    def test_unpermitted_arm_and_click_phases_expire_despite_phone_polling(self):
        for armed in (False, True):
            with self.subTest(armed=armed):
                h = SessionHarness(); h.device('moved', ok=True)
                if armed: h.phone('armed')
                for now in (101, 102, 102.9):
                    h.now = now; h.phone('poll')
                h.now = 103.001
                self.assertEqual(h.session.command(now=h.now)['phase'], 'failed')
                self.assertIn('点击准备已过期', h.session.error)
                self.assertIsNone(h.session.permitted)
                self.assertIsNone(h.session.verified)

    def test_late_permit_with_fresh_heartbeats_is_diagnosed_without_changing_permission(self):
        h = SessionHarness()
        h.session.command(now=h.now)
        h.now = 101.11; h.device('moved', ok=True)
        h.now = 101.2; h.phone('armed')
        for now in (101.45, 101.7, 101.95, 102.2, 102.45, 102.7, 102.95):
            h.now = now; h.phone('poll')
            if now in (101.95, 102.2): h.session.command(now=h.now)
        h.now = 103.2
        self.assertEqual(h.device('permit'), {'ok': True})
        h.now = 103.25; h.phone('cancel')
        diagnostic = h.session.diagnostic()
        self.assertEqual(diagnostic['timing'], [dict(step=1, move_command_ms=0, moved_ms=1110,
            armed_ms=1200, click_command_ms=1950, permit_ms=3200,
            point_ms=None, clicked_ms=None, cancel_ms=3250)])
        self.assertEqual(diagnostic['failure_context'], dict(phase_before_failure='click', last_op='cancel',
            heartbeat_age_ms=300, permit_age_ms=50, point_received=False, clicked_received=False))
        self.assertIn('用户已取消', diagnostic['error'])

    def test_late_point_freezes_failure_even_when_usb_receipt_arrived(self):
        for clicked_first in (True, False):
            with self.subTest(clicked_first=clicked_first):
                h = SessionHarness(); h.permit()
                h.now = 101.9; h.phone('poll')
                if clicked_first: h.device('clicked', ok=True)
                h.now = 102.001
                self.assertEqual(h.point()['phase'], 'failed')
                before = h.session.diagnostic()
                context = before['failure_context']
                self.assertEqual(context['phase_before_failure'], 'click')
                self.assertEqual(context['last_op'], 'point')
                self.assertEqual(context['permit_age_ms'], 2001)
                self.assertFalse(context['point_received'])
                self.assertEqual(context['clicked_received'], clicked_first)
                h.now = 102.1; h.phone('cancel')
                h.session.fail('后续状态变化')
                self.assertEqual(h.session.diagnostic()['failure_context'], context)
                self.assertEqual(h.session.error, before['error'])
                self.assertEqual(h.session.index, 0)
                self.assertIsNone(h.session.verified)

    @patch.object(calibration, 'REPORT_UNITS', 14)
    @patch.object(calibration, 'ALGORITHM', 'home-yx14-grid-v2')
    def test_recorded_timely_point_waits_for_delayed_usb_receipt(self):
        h = SessionHarness(require_delivery=True)
        for x, y in ((87, 111.33333333333333), (184.33333333333331, 355),
                     (50.33333333333333, 87)):
            h.sample(x=x, y=y, delivery=True)
        self.assertEqual(h.session.units, (378, 854))
        start = h.now
        h.now = start+.105; h.session.command(now=h.now)
        h.now = start+1.336; h.device('moved', ok=True, usb=usb_report(h.session, 'moved'))
        h.now = start+1.389; h.phone('armed')
        h.now = start+1.407; h.session.command(now=h.now)
        h.now = start+1.439; h.device('permit')
        old_probe = h.session.probe
        h.now = start+1.725; h.point(x=343, y=745)
        h.now = start+3.489
        self.assertEqual(h.phone('poll')['phase'], 'click')
        self.assertEqual(h.session.command(now=h.now)['phase'], 'wait')
        self.assertEqual(h.session.index, 3)
        self.assertEqual(len(h.session.samples), 1)
        h.now = start+4.080
        self.assertEqual(h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked')), {'ok': True})
        self.assertEqual(h.session.index, 4)
        self.assertEqual(h.session.samples[-1], (378, 854, 343, 745))
        self.assertIsNone(h.session.verified)
        row = h.session.diagnostic()['timing'][3]
        self.assertEqual(row['point_ms']-row['permit_ms'], 286)
        self.assertEqual(row['clicked_ms']-row['permit_ms'], 2641)
        with self.assertRaisesRegex(ValueError, '探测已失效'):
            h.device('clicked', ok=True, probe=old_probe, usb=usb_report(h.session, 'clicked'))
        self.assertEqual(h.session.index, 4)

    def test_usb_receipt_has_fixed_five_second_limit_after_timely_point(self):
        for elapsed in (5, 5.001):
            with self.subTest(elapsed=elapsed):
                h = SessionHarness(require_delivery=True); h.permit(delivery=True)
                h.now = 100.2; h.point()
                for now in (101.9, 103.8):
                    h.now = now
                    self.assertEqual(h.phone('poll')['phase'], 'click')
                    self.assertEqual(h.session.command(now=h.now)['phase'], 'wait')
                h.now = 100+elapsed
                if elapsed == 5:
                    h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked'))
                    self.assertEqual(h.session.index, 1)
                else:
                    self.assertEqual(h.phone('poll')['phase'], 'failed')
                    with self.assertRaisesRegex(ValueError, '点击回报超时'):
                        h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked'))
                    self.assertEqual(h.session.index, 0)
                    self.assertTrue(h.session.failure_context['point_received'])
                    self.assertFalse(h.session.failure_context['clicked_received'])
                self.assertIsNone(h.session.verified)

    def test_delayed_receipt_still_requires_valid_complete_usb_statistics(self):
        for changes in ({'usb': None}, {'usb': {}}, {'ok': False},
                        {'usb': dict(planned=2, submitted=2, completed=1, error='none',
                                     duration_ms=80, min_gap_ms=70, max_gap_ms=70)}):
            with self.subTest(changes=changes):
                h = SessionHarness(require_delivery=True); h.permit(delivery=True)
                h.now = 100.2; h.point()
                h.now = 101.9; h.phone('poll')
                h.now = 102.641
                fields = dict(ok=True, usb=usb_report(h.session, 'clicked')); fields.update(changes)
                with self.assertRaises(ValueError): h.device('clicked', **fields)
                self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(h.session.index, 0)
                self.assertIsNone(h.session.verified)

    def test_waiting_for_receipt_preserves_heartbeat_cancel_and_geometry_gates(self):
        for stop in ('heartbeat', 'cancel', 'geometry', 'duplicate_permit', 'duplicate_point'):
            with self.subTest(stop=stop):
                h = SessionHarness(require_delivery=True); h.permit(delivery=True)
                h.now = 100.2; h.point()
                if stop != 'heartbeat':
                    h.now = 101.9; h.phone('poll')
                h.now = 102.201 if stop == 'heartbeat' else 102.3
                if stop == 'heartbeat': h.session.command(now=h.now)
                elif stop == 'cancel': h.phone('cancel')
                else:
                    with self.assertRaises(ValueError):
                        if stop == 'geometry': h.phone('poll', geometry=[844, 390, 'landscape'])
                        elif stop == 'duplicate_point': h.point()
                        else: h.device('permit')
                h.now = 102.641
                with self.assertRaises(ValueError):
                    h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked'))
                self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(h.session.index, 0)
                self.assertIsNone(h.session.verified)

    def test_all_nine_delayed_receipts_need_both_evidence_and_heldouts(self):
        h = SessionHarness(require_delivery=True)
        for index in range(9):
            h.permit(delivery=True)
            start = h.now
            h.now = start+.286; h.point()
            h.now = start+1.9; h.phone('poll')
            h.now = start+2.641
            self.assertEqual(h.session.command(now=h.now)['phase'], 'wait')
            self.assertEqual(h.session.index, index)
            self.assertIsNone(h.session.verified)
            h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked'))
        self.assertEqual(h.session.phase, 'done')
        self.assertEqual(len(h.session.validations), 2)
        self.assertEqual(len(h.session.usb_reports), 18)
        self.assertTrue(h.session.verified['verified'])

    def test_nine_probe_timing_is_bounded_and_diagnostic_contains_no_identity(self):
        h = SessionHarness()
        for _ in range(9):
            h.session.command(now=h.now)
            h.sample()
        diagnostic = h.session.diagnostic()
        self.assertEqual(len(diagnostic['timing']), 9)
        self.assertEqual([row['step'] for row in diagnostic['timing']], list(range(1, 10)))
        self.assertIsNone(diagnostic['failure_context'])
        for row in diagnostic['timing']:
            self.assertEqual(set(row), {'step', 'move_command_ms', 'moved_ms', 'armed_ms',
                'click_command_ms', 'permit_ms', 'point_ms', 'clicked_ms', 'cancel_ms'})
            self.assertTrue(all(value is None or type(value) is int for value in row.values()))
        encoded = json.dumps(diagnostic)
        for private in (PAGE, h.session.id, h.session.probe): self.assertNotIn(private, encoded)
        with self.assertRaises(ValueError):
            h.device('abort', reason='permit_expired', elapsed_ms=1100)
        self.assertEqual(h.session.phase, 'done')
        self.assertIsNone(h.session.diagnostic()['device_abort'])
        h.session.fail('共享已停止', now=h.now)
        self.assertEqual(h.session.phase, 'failed')
        self.assertIsNone(h.session.verified)
        self.assertEqual(h.session.diagnostic()['failure_context']['phase_before_failure'], 'done')

    def test_permit_expired_abort_records_only_whitelisted_fields_and_stops_click(self):
        h = SessionHarness(); h.permit()
        h.now = 101.1
        self.assertEqual(h.device('abort', reason='permit_expired', elapsed_ms=1100,
                                  device_id='private-device', screenshot='private-image'), {'ok': True})
        diagnostic = h.session.diagnostic()
        self.assertEqual(h.session.phase, 'failed')
        self.assertEqual(h.session.error, '圆屏点击许可已过期，未执行点击')
        self.assertEqual(diagnostic['device_abort'], dict(step=1, reason='permit_expired', elapsed_ms=1100))
        self.assertEqual(diagnostic['failure_context']['last_op'], 'abort')
        self.assertFalse(diagnostic['failure_context']['clicked_received'])
        self.assertIsNone(diagnostic['timing'][0]['clicked_ms'])
        with self.assertRaises(ValueError): h.device('clicked', ok=True)
        self.assertEqual(h.session.coarse_samples, [])
        encoded = json.dumps(h.session.diagnostic())
        for private in (PAGE, h.session.id, h.session.probe, 'private-device', 'private-image'):
            self.assertNotIn(private, encoded)

    def test_terminal_abort_keeps_original_failure_and_first_abort_measurement(self):
        h = SessionHarness(); h.permit()
        h.now = 100.5; h.phone('cancel')
        before = h.session.diagnostic()
        h.now = 101.1
        self.assertEqual(h.device('abort', reason='permit_expired', elapsed_ms=1100), {'ok': True})
        h.now = 101.2
        self.assertEqual(h.device('abort', reason='permit_expired', elapsed_ms=1200), {'ok': True})
        after = h.session.diagnostic()
        self.assertEqual(after['error'], before['error'])
        self.assertEqual(after['failure_context'], before['failure_context'])
        self.assertEqual(after['device_abort']['elapsed_ms'], 1100)
        self.assertEqual(h.session.index, 0)
        after['failure_context']['last_op'] = 'changed-by-reader'
        after['timing'][0]['armed_ms'] = -1
        with self.assertRaises(ValueError): h.device('abort', reason='unknown', elapsed_ms=1100)
        self.assertEqual(h.session.diagnostic()['failure_context'], before['failure_context'])
        self.assertEqual(h.session.diagnostic()['timing'][0]['armed_ms'], 0)

    def test_abort_requires_current_identity_and_prior_permit(self):
        for changes in ({'id': 'old-session'}, {'probe': 'old-probe'}, {}):
            with self.subTest(changes=changes):
                h = SessionHarness()
                if changes: h.permit()
                with self.assertRaises(ValueError):
                    h.device('abort', reason='permit_expired', elapsed_ms=1100, **changes)
                self.assertIsNone(h.session.diagnostic()['device_abort'])
                self.assertEqual(h.session.coarse_samples, [])

    def test_abort_elapsed_is_bounded_integer_and_reason_is_exact(self):
        invalid = [dict(elapsed_ms=value) for value in (True, False, 1.0, '1100', None, -1, 10001)]
        invalid += [dict(reason=value) for value in ('unknown', '', None, True, [])]
        for changes in invalid:
            with self.subTest(changes=changes):
                h = SessionHarness(); h.permit()
                body = dict(reason='permit_expired', elapsed_ms=1100)
                body.update(changes)
                with self.assertRaises(ValueError): h.device('abort', **body)
                self.assertIsNone(h.session.diagnostic()['device_abort'])
                self.assertIsNone(h.session.verified)
        for elapsed in (0, 10000):
            with self.subTest(elapsed=elapsed):
                h = SessionHarness(); h.permit()
                self.assertEqual(h.device('abort', reason='permit_expired', elapsed_ms=elapsed), {'ok': True})
                self.assertEqual(h.session.diagnostic()['device_abort']['elapsed_ms'], elapsed)

    def test_abort_cannot_claim_no_click_after_successful_click_ack(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                h = SessionHarness(); h.permit()
                h.device('clicked', ok=True)
                if cancelled: h.phone('cancel')
                before = h.session.diagnostic()
                with self.assertRaisesRegex(ValueError, '已确认点击矛盾'):
                    h.device('abort', reason='permit_expired', elapsed_ms=1100)
                after = h.session.diagnostic()
                self.assertIsNone(after['device_abort'])
                self.assertTrue(after['failure_context']['clicked_received'])
                self.assertNotIn('未执行点击', after['error'])
                if cancelled:
                    self.assertEqual(after['error'], before['error'])
                    self.assertEqual(after['failure_context'], before['failure_context'])
                self.assertEqual(h.session.index, 0)

    def test_preparing_next_probe_failure_does_not_reuse_previous_probe_evidence(self):
        h = SessionHarness(); h.sample()
        h.now = 101.0
        with patch.object(calibration, 'units_for', side_effect=ValueError('校准后的移动量越界')):
            with self.assertRaisesRegex(ValueError, '移动量越界'): h.sample()
        diagnostic = h.session.diagnostic()
        self.assertEqual(diagnostic['step'], 3)
        self.assertEqual(len(diagnostic['timing']), 3)
        self.assertEqual(diagnostic['timing'][-1]['step'], 3)
        self.assertTrue(all(value is None for key, value in diagnostic['timing'][-1].items() if key != 'step'))
        self.assertIsNone(diagnostic['requested_units'])
        self.assertIsNone(diagnostic['observed_point'])
        self.assertEqual(h.session.sample_started, 101.0)
        self.assertIsNone(diagnostic['failure_context']['permit_age_ms'])
        self.assertFalse(diagnostic['failure_context']['point_received'])
        self.assertFalse(diagnostic['failure_context']['clicked_received'])
        self.assertEqual(h.session.command(now=101.2)['phase'], 'failed')

    def test_invalid_request_operation_cannot_forge_command_generation_time(self):
        for op in ('move_command', 'click_command', 'private-operation'):
            with self.subTest(op=op):
                h = SessionHarness()
                with self.assertRaises(ValueError): h.phone(op)
                diagnostic = h.session.diagnostic()
                self.assertIsNone(diagnostic['timing'][0]['move_command_ms'])
                self.assertIsNone(diagnostic['timing'][0]['click_command_ms'])
                self.assertEqual(diagnostic['failure_context']['last_op'], 'invalid')
                self.assertNotIn('private-operation', json.dumps(diagnostic))


class USBDeliverySessionTests(unittest.TestCase):
    def test_seven_unit_motion_rejects_old_fourteen_unit_report_count(self):
        h = SessionHarness(require_delivery=True)
        self.assertEqual(h.session.units, (84, 126))
        self.assertEqual(usb_report(h.session, 'moved')['planned'], 60)
        old_report = usb_report(h.session, 'moved', planned=45, submitted=45, completed=45)
        with self.assertRaisesRegex(ValueError, 'USB 报告未全部确认'):
            h.device('moved', ok=True, usb=old_report)
        self.assertEqual(h.session.phase, 'failed')
        self.assertEqual(h.session.coarse_samples, [])

    def test_nine_points_require_eighteen_completed_delivery_reports(self):
        h = SessionHarness(require_delivery=True)
        expected = []
        for index in range(9):
            move_count = 30+sum(math.ceil(units/calibration.REPORT_UNITS) for units in h.session.units)
            expected.extend([(index+1, 'moved', move_count), (index+1, 'clicked', 2)])
            h.sample(point_first=index % 2 == 0, delivery=True)
        self.assertTrue(h.session.verified['verified'])
        diagnostic = h.session.diagnostic()
        self.assertTrue(diagnostic['delivery_required'])
        self.assertEqual(len(diagnostic['usb_reports']), 18)
        for report, (step, op, count) in zip(diagnostic['usb_reports'], expected):
            self.assertEqual((report['step'], report['op']), (step, op))
            self.assertEqual((report['planned'], report['submitted'], report['completed']), (count, count, count))
            self.assertEqual(report['error'], 'none')

    def test_legacy_session_still_completes_without_delivery_reports(self):
        h = SessionHarness()
        for _ in range(9): h.sample()
        self.assertTrue(h.session.verified['verified'])
        self.assertFalse(h.session.diagnostic()['delivery_required'])
        self.assertEqual(h.session.diagnostic()['usb_reports'], [])

    def test_required_report_cannot_be_omitted_from_move_or_click(self):
        for op in ('moved', 'clicked'):
            with self.subTest(op=op):
                h = SessionHarness(require_delivery=True)
                if op == 'clicked':
                    h.permit(delivery=True)
                    h.point()
                before = len(h.session.usb_reports)
                with self.assertRaises(ValueError): h.device(op, ok=True)
                self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(len(h.session.usb_reports), before)
                self.assertEqual(h.session.coarse_samples, [])
                self.assertIsNone(h.session.verified)

    def test_present_malformed_report_is_rejected_even_for_legacy_device(self):
        for report in (None, [], 'delivered', {}, True):
            with self.subTest(report=report):
                h = SessionHarness()
                with self.assertRaises(ValueError): h.device('moved', ok=True, usb=report)
                self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(h.session.usb_reports, [])

    def test_report_numbers_are_bounded_integers_and_not_booleans(self):
        for key in ('planned', 'submitted', 'completed', 'duration_ms', 'min_gap_ms', 'max_gap_ms'):
            limit = 512 if key in ('planned', 'submitted', 'completed') else 10000
            for invalid in (True, False, 1.0, '1', None, -1, limit+1):
                with self.subTest(key=key, invalid=invalid):
                    h = SessionHarness(require_delivery=True)
                    report = usb_report(h.session, 'moved', **{key: invalid})
                    with self.assertRaises(ValueError): h.device('moved', ok=True, usb=report)
                    self.assertEqual(h.session.phase, 'failed')
                    self.assertEqual(h.session.usb_reports, [])
            h = SessionHarness(require_delivery=True)
            report = usb_report(h.session, 'moved')
            del report[key]
            with self.assertRaises(ValueError): h.device('moved', ok=True, usb=report)
            self.assertEqual(h.session.usb_reports, [])

    def test_invalid_count_order_gap_order_and_error_enum_are_not_saved(self):
        for changes in (dict(submitted=0, completed=1), dict(planned=0, submitted=1, completed=0),
                        dict(min_gap_ms=20, max_gap_ms=10), dict(error='unknown'),
                        dict(error=None), dict(error=True), dict(error=[]), dict(error={})):
            with self.subTest(changes=changes):
                h = SessionHarness(require_delivery=True)
                with self.assertRaises(ValueError):
                    h.device('moved', ok=True, usb=usb_report(h.session, 'moved', **changes))
                self.assertEqual(h.session.phase, 'failed')
                self.assertEqual(h.session.usb_reports, [])

    def test_claimed_success_requires_exact_expected_count_and_no_error(self):
        for op in ('moved', 'clicked'):
            for defect in ('incomplete', 'wrong_count', 'zero_count', 'error'):
                with self.subTest(op=op, defect=defect):
                    h = SessionHarness(require_delivery=True)
                    if op == 'clicked':
                        h.permit(delivery=True)
                        h.point()
                    report = usb_report(h.session, op)
                    if defect == 'incomplete': report['completed'] -= 1
                    if defect == 'wrong_count':
                        report.update(planned=report['planned']+1, submitted=report['submitted']+1,
                                      completed=report['completed']+1)
                    if defect == 'zero_count': report.update(planned=0, submitted=0, completed=0)
                    if defect == 'error': report['error'] = 'transfer_failed'
                    before = len(h.session.usb_reports)
                    with self.assertRaises(ValueError): h.device(op, ok=True, usb=report)
                    self.assertEqual(h.session.phase, 'failed')
                    self.assertEqual(len(h.session.usb_reports), before+1)
                    self.assertEqual(h.session.usb_reports[-1]['completed'], report['completed'])
                    self.assertIsNone(h.session.verified)

    def test_claimed_success_requires_ten_ms_spacing_and_consistent_duration(self):
        for op in ('moved', 'clicked'):
            for defect in ('gap_9', 'duration_short', 'max_gap_exceeds_duration'):
                with self.subTest(op=op, defect=defect):
                    h = SessionHarness(require_delivery=True)
                    if op == 'clicked': h.permit(delivery=True)
                    report = usb_report(h.session, op)
                    if defect == 'gap_9': report['min_gap_ms'] = 9
                    if defect == 'duration_short':
                        report.update(min_gap_ms=10, max_gap_ms=10,
                                      duration_ms=(report['submitted']-1)*10-1)
                    if defect == 'max_gap_exceeds_duration': report['max_gap_ms'] = report['duration_ms']+1
                    before = len(h.session.usb_reports)
                    with self.assertRaises(ValueError): h.device(op, ok=True, usb=report)
                    self.assertEqual(h.session.phase, 'failed')
                    self.assertEqual(len(h.session.usb_reports), before+1)
                    self.assertIsNone(h.session.verified)
        h = SessionHarness(require_delivery=True)
        report = usb_report(h.session, 'moved', min_gap_ms=10, max_gap_ms=10)
        report['duration_ms'] = (report['submitted']-1)*10
        h.device('moved', ok=True, usb=report)
        self.assertEqual(h.session.phase, 'arm')

    def test_valid_failed_delivery_preserves_statistics_and_stops_sampling(self):
        for op in ('moved', 'clicked'):
            for error in ('none', 'ready_timeout', 'submit_failed', 'transfer_failed', 'transfer_timeout', 'job_timeout',
                          'stopped', 'not_ready', 'busy', 'invalid_job'):
                with self.subTest(op=op, error=error):
                    h = SessionHarness(require_delivery=True)
                    if op == 'clicked':
                        h.permit(delivery=True)
                        h.point()
                    before = len(h.session.usb_reports)
                    report = usb_report(h.session, op, submitted=1, completed=0, error=error,
                                        duration_ms=10000, min_gap_ms=0, max_gap_ms=0)
                    with self.assertRaises(ValueError): h.device(op, ok=False, usb=report)
                    self.assertEqual(h.session.phase, 'failed')
                    self.assertEqual(len(h.session.usb_reports), before+1)
                    self.assertEqual(h.session.usb_reports[-1]['error'], error)
                    self.assertEqual(h.session.coarse_samples, [])
                    self.assertIsNone(h.session.verified)

    def test_duplicate_old_probe_and_cancelled_receipts_do_not_add_reports(self):
        for cause in ('duplicate_move', 'duplicate_click', 'old_probe', 'cancelled'):
            with self.subTest(cause=cause):
                h = SessionHarness(require_delivery=True)
                old_probe = h.session.probe
                if cause == 'duplicate_move':
                    h.device('moved', ok=True, usb=usb_report(h.session, 'moved'))
                    op = 'moved'
                else:
                    op = 'clicked'
                    if cause == 'old_probe': h.sample(delivery=True)
                    else:
                        h.permit(delivery=True)
                        if cause == 'duplicate_click':
                            h.device('clicked', ok=True, usb=usb_report(h.session, 'clicked'))
                        else: h.phone('cancel')
                before = list(h.session.usb_reports)
                with self.assertRaises(ValueError):
                    h.device(op, ok=True, probe=old_probe, usb=usb_report(h.session, op))
                self.assertEqual(h.session.usb_reports, before)

    def test_diagnostic_keeps_only_numeric_fields_enum_step_and_operation(self):
        h = SessionHarness(require_delivery=True)
        report = usb_report(h.session, 'moved', id='private-device-id', probe='private-probe',
                            screenshot='private-image', error_detail='private-message')
        h.device('moved', ok=True, usb=report)
        clean = h.session.diagnostic()['usb_reports'][0]
        self.assertEqual(set(clean), {'planned', 'submitted', 'completed', 'error', 'duration_ms',
                                      'min_gap_ms', 'max_gap_ms', 'step', 'op'})
        encoded = json.dumps(h.session.diagnostic())
        for private in ('private-device-id', 'private-probe', 'private-image', 'private-message',
                        h.session.id, h.session.probe, PAGE):
            self.assertNotIn(private, encoded)


class NativeCalibrationStateTests(unittest.TestCase):
    def setUp(self):
        clock = patch('server.time.monotonic', return_value=100.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.state = server.State()
        self.state.pair('phone', self.state.code)
        self.state.pair('device', self.state.code, ['native_touch_v1', 'hid_delivery_v1', 'pointer_grid7_v1'])
        self.state.add_frame(capture_payload())

    def begin(self):
        return self.state.calibration_phone(dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000))

    def test_begin_requires_exact_five_second_phone_window_declaration(self):
        invalid = [{}]+[dict(touch_window_ms=value) for value in (2000, 4999, 5001, 5000.0, True, '5000', None)]
        for fields in invalid:
            with self.subTest(fields=fields):
                self.state.calibration_session = None
                with self.assertRaisesRegex(ValueError, '更新 iPhone'):
                    self.state.calibration_phone(dict(op='begin', page=PAGE, geometry=list(GEOMETRY), **fields))
                self.assertIsNone(self.state.calibration_session)
                self.assertIsNone(self.state.pointer_calibration)

    def test_native_protocol_requires_both_roles_current_firmware_and_fresh_frame(self):
        for missing in ('phone', 'device', 'native_capability', 'delivery_capability', 'grid_capability', 'frame', 'heartbeat'):
            with self.subTest(missing=missing):
                state = server.State()
                if missing != 'phone': state.pair('phone', state.code)
                if missing != 'device':
                    capabilities = ['native_touch_v1', 'hid_delivery_v1', 'pointer_grid7_v1']
                    if missing == 'native_capability': capabilities.remove('native_touch_v1')
                    if missing == 'delivery_capability': capabilities.remove('hid_delivery_v1')
                    if missing == 'grid_capability': capabilities.remove('pointer_grid7_v1')
                    state.pair('device', state.code, capabilities)
                if missing != 'frame': state.add_frame(capture_payload())
                if missing == 'heartbeat': state.device_last_seen = 94
                with self.assertRaises(ValueError):
                    state.calibration_phone(dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000))
                self.assertIsNone(state.calibration_session)

    def test_paired_delivery_capability_requires_statistics_for_the_whole_round(self):
        for delivery in (False, True):
            with self.subTest(delivery=delivery):
                state = server.State()
                state.pair('phone', state.code)
                capabilities = ['native_touch_v1', 'pointer_grid7_v1']+(['hid_delivery_v1'] if delivery else [])
                token = state.pair('device', state.code, capabilities)
                state.add_frame(capture_payload())
                self.assertTrue(state.auth('device', token))
                if not delivery:
                    with self.assertRaisesRegex(ValueError, '更新圆屏'):
                        calibrate_state(state)
                    self.assertIsNone(state.calibration_session)
                    self.assertIsNone(state.pointer_calibration)
                    continue
                self.assertTrue(calibrate_state(state)['verified'])
                diagnostic = state.calibration_session.diagnostic()
                self.assertTrue(diagnostic['delivery_required'])
                self.assertEqual(len(diagnostic['usb_reports']), 18)

    def test_new_calibration_invalidates_cache_and_excludes_normal_tasks(self):
        fit = calibrate_state(self.state)
        self.assertTrue(fit['verified'])
        self.begin()
        self.assertIsNone(self.state.pointer_calibration)
        with self.assertRaises(ValueError): self.state.begin('打开微信')
        with self.assertRaises(ValueError): self.begin()
        self.state.calibration_phone(phone_body(self.state.calibration_session, 'cancel'))
        self.assertIsNone(self.state.pointer_calibration)
        self.state.begin('打开微信')
        with self.assertRaises(ValueError): self.begin()

    def test_only_points_geometry_matters_for_calibration_not_jpeg_size(self):
        self.state.add_frame(capture_payload(size=(384, 832)))
        fit = calibrate_state(self.state)
        self.assertEqual(fit['geometry'], GEOMETRY)
        self.state.add_frame(capture_payload(size=(390, 844)))
        self.state.begin('打开微信')
        action = self.state.propose(dict(type='tap', x=260, y=110, label='微信', scope='app_icon'),
                                    self.state.current_frame())
        self.assertIn('move', self.state.pointer_step(action['id'], {}))

    def test_capture_staleness_geometry_change_and_device_loss_abort_before_click(self):
        for loss in ('frame', 'geometry', 'device'):
            with self.subTest(loss=loss):
                self.state.calibration_session = None
                self.state.add_frame(capture_payload())
                self.state.device_last_seen = 100
                self.begin()
                session = self.state.calibration_session
                self.state.calibration_device(device_body(session, 'moved', ok=True))
                self.state.calibration_phone(phone_body(session, 'armed'))
                if loss == 'frame': self.state.frame['received'] = 94
                if loss == 'geometry': self.state.add_frame(capture_payload(geometry=[844, 390, 'landscape']))
                if loss == 'device': self.state.device_last_seen = 94
                with self.assertRaises(ValueError):
                    self.state.calibration_device(device_body(session, 'permit'))
                self.assertEqual(session.phase, 'failed')
                self.assertIsNone(self.state.pointer_calibration)

    def test_stop_and_repair_invalidate_completed_cache_and_pending_calibration(self):
        for action in ('stop', 'phone_repair', 'device_repair'):
            with self.subTest(action=action):
                state = server.State()
                state.pair('phone', state.code)
                state.pair('device', state.code, ['native_touch_v1', 'hid_delivery_v1', 'pointer_grid7_v1'])
                state.add_frame(capture_payload())
                self.assertTrue(calibrate_state(state)['verified'])
                if action == 'stop': state.stop_capture()
                elif action == 'phone_repair':
                    state.phone_paired_at = state.frame['received'] = 0
                    state.pair('phone', state.code)
                else:
                    state.device_last_seen = 0
                    state.pair('device', state.code, ['native_touch_v1', 'hid_delivery_v1', 'pointer_grid7_v1'])
                self.assertIsNone(state.pointer_calibration)
                self.assertEqual(state.calibration_session.phase, 'failed')

    def test_failure_is_not_reissued_as_a_move_or_click(self):
        self.begin()
        session = self.state.calibration_session
        self.state.calibration_phone(phone_body(session, 'cancel'))
        self.assertEqual(self.state.calibration_command()['phase'], 'failed')
        self.assertIsNone(self.state.calibration_command())

    def test_geometry_change_and_return_cannot_restore_old_calibration(self):
        self.assertTrue(calibrate_state(self.state)['verified'])
        session = self.state.calibration_session
        self.state.add_frame(capture_payload(geometry=[844, 390, 'landscape']))
        self.assertIsNone(self.state.pointer_calibration)
        self.assertEqual(session.phase, 'failed')
        self.state.add_frame(capture_payload())
        self.state.calibration_phone(phone_body(session, 'poll'))
        self.assertIsNone(self.state.pointer_calibration)
        self.state.begin('打开微信')
        with self.assertRaisesRegex(ValueError, '校准'):
            self.state.propose(dict(type='tap', x=260, y=110, label='微信', scope='app_icon'),
                               self.state.current_frame())
        self.assertIsNone(self.state.pending)
        self.assertIsNone(self.state.pointer_job)

    def test_in_progress_geometry_change_cannot_hide_between_protocol_polls(self):
        self.begin()
        session = self.state.calibration_session
        self.state.add_frame(capture_payload(geometry=[844, 390, 'landscape']))
        self.state.add_frame(capture_payload())
        reply = self.state.calibration_phone(phone_body(session, 'poll'))
        self.assertEqual(reply['phase'], 'failed')
        self.assertIsNone(self.state.pointer_calibration)
        with self.assertRaises(ValueError):
            self.state.calibration_device(device_body(session, 'moved', ok=True))

    def test_late_done_session_poll_after_capture_stop_cannot_republish_cache(self):
        self.assertTrue(calibrate_state(self.state)['verified'])
        session = self.state.calibration_session
        self.state.stop_capture()
        reply = self.state.calibration_phone(phone_body(session, 'poll'))
        self.assertEqual(reply['phase'], 'failed')
        self.assertIsNone(session.verified)
        self.assertIsNone(self.state.pointer_calibration)

    def test_wrong_phone_geometry_aborts_even_if_capture_geometry_is_current(self):
        self.begin()
        session = self.state.calibration_session
        self.state.calibration_device(device_body(session, 'moved', ok=True))
        self.state.calibration_phone(phone_body(session, 'armed'))
        self.state.calibration_device(device_body(session, 'permit'))
        body = dict(phone_body(session, 'point', x=60, y=146.4, touch_type='direct'),
                    geometry=[844, 390, 'landscape'])
        with self.assertRaises(ValueError): self.state.calibration_phone(body)
        self.assertEqual(session.phase, 'failed')
        self.assertIsNone(self.state.pointer_calibration)
        with self.assertRaises(ValueError):
            self.state.calibration_device(device_body(session, 'clicked', ok=True))

    def test_mapped_job_needs_one_new_frame_scene_check_and_no_repeat_movement(self):
        calibrate_state(self.state)
        self.state.begin('打开微信')
        action = self.state.propose(dict(type='tap', x=260, y=110, label='微信', scope='app_icon'),
                                    self.state.current_frame())
        first = self.state.pointer_step(action['id'], {})
        self.assertEqual(first['move']['seq'], 1)
        self.assertTrue(first['move']['home'])
        self.assertEqual(first['move']['report_units'], 7)
        self.assertEqual((first['move']['dx'] % 7, first['move']['dy'] % 7), (0, 0))
        self.assertFalse(self.state.check_pending(action['id']))
        self.state.pointer_step(action['id'], dict(seq=1, ok=True))
        for _ in range(4):
            self.assertEqual(self.state.pointer_step(action['id'], dict(poll=True)), {'wait': True})
            self.assertFalse(self.state.check_pending(action['id']))
        self.state.add_frame(capture_payload())
        self.assertEqual(self.state.pointer_step(action['id'], dict(poll=True)), {'ready': True})
        self.assertFalse(self.state.pointer_job.authorized)
        self.assertTrue(self.state.check_pending(action['id']))
        self.assertFalse(self.state.pointer_diagnostic['landing_observed'])
        self.state.add_frame(capture_payload(fill='black'))
        self.assertFalse(self.state.check_pending(action['id']))
        self.assertFalse(self.state.pointer_job.authorized)
        with self.assertRaises(ValueError): self.state.result(action['id'], True)
        with self.assertRaisesRegex(ValueError, '画面内容已变化'):
            self.state.pointer_step(action['id'], dict(poll=True))
        # Returning to the old scene after an explicit failure does not revive it.
        self.state.add_frame(capture_payload())
        self.assertFalse(self.state.check_pending(action['id']))


class NativeCalibrationHTTPTests(unittest.TestCase):
    def setUp(self):
        state_patch = patch.object(server, 'state', server.State())
        state_patch.start()
        self.addCleanup(state_patch.stop)
        for name in ('decide', 'transcribe'):
            guard = patch.object(server, name, side_effect=AssertionError('No model calls in calibration tests'))
            guard.start()
            self.addCleanup(guard.stop)
        clock = patch('server.time.monotonic', return_value=100.0)
        clock.start()
        self.addCleanup(clock.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.phone = self.request('/pair', dict(role='phone', code=server.state.code))['token']
        self.device = self.request('/pair', dict(role='device', code=server.state.code,
                                               capabilities=['native_touch_v1', 'hid_delivery_v1', 'home_swipe_v1', 'pointer_grid7_v1']))['token']
        self.request('/frame', capture_payload(), self.phone)

    def close_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def request(self, path, body=None, token=None):
        headers = {'Authorization': 'Bearer '+token} if token else {}
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request('http://127.0.0.1:%d%s' % (self.http.server_port, path), data, headers)
        with self.client.open(request, timeout=5) as response:
            return json.load(response)

    def rejected(self, code, path, body=None, token=None):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(path, body, token)
        self.assertEqual(caught.exception.code, code)
        caught.exception.close()

    def begin(self):
        return self.request('/calibration', dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000), self.phone)

    def test_old_phone_window_is_rejected_before_any_calibration_command(self):
        for fields in ({}, dict(touch_window_ms=2000)):
            with self.subTest(fields=fields):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request('/calibration', dict(op='begin', page=PAGE, geometry=list(GEOMETRY), **fields), self.phone)
                self.assertEqual(caught.exception.code, 400)
                reply = json.load(caught.exception)
                caught.exception.close()
                self.assertIn('更新 iPhone', reply['error'])
                self.assertIsNone(server.state.calibration_session)
                self.assertEqual(self.request('/next', token=self.device), {'action': None})
        self.begin()
        self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'move')

    def test_timely_touch_and_delayed_usb_receipt_through_http(self):
        self.begin()
        session = server.state.calibration_session
        self.request('/calibration/device', device_body(session, 'moved', ok=True), self.device)
        self.request('/calibration', phone_body(session, 'armed'), self.phone)
        self.request('/calibration/device', device_body(session, 'permit'), self.device)
        clicked = device_body(session, 'clicked', ok=True)
        x, y = synthetic_position(*session.units)
        with patch('server.time.monotonic', return_value=100.286):
            self.request('/calibration', phone_body(session, 'point', x=x, y=y,
                                                   touch_type='direct'), self.phone)
        with patch('server.time.monotonic', return_value=102.050):
            self.assertEqual(self.request('/calibration', phone_body(session, 'poll'), self.phone)['phase'], 'click')
            self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'wait')
            self.assertEqual(session.index, 0)
        with patch('server.time.monotonic', return_value=102.641):
            self.assertTrue(self.request('/calibration/device', clicked, self.device)['ok'])
            self.assertEqual(session.index, 1)
            self.rejected(400, '/calibration/device', clicked, self.device)
            self.assertEqual(session.index, 1)
            self.assertIsNone(server.state.pointer_calibration)

    def test_coordinate_preflight_prepares_without_authorizing_until_final_check(self):
        self.assertTrue(calibrate_state(server.state)['verified'])
        self.assertEqual(len(server.state.calibration_session.usb_reports), 18)
        actions = [dict(type='tap', x=224, y=106, label='微信', scope='app_icon'),
                   dict(type='send', x=300, y=700, recipient='小明', text='你好'),
                   dict(type='swipe', direction='left')]
        for proposal in actions:
            with self.subTest(kind=proposal['type']):
                server.state.finish()
                self.request('/frame', capture_payload(), self.phone)
                server.state.begin('测试校准后的坐标操作')
                action = server.state.propose(proposal, server.state.current_frame())
                check = dict(id=action['id'])
                result = dict(id=action['id'], ok=True)
                self.assertIsNone(server.state.pointer_job)
                self.assertTrue(self.request('/check', check, self.device)['ok'])
                job = server.state.pointer_job
                self.assertIsNotNone(job)
                self.assertFalse(job.issued)
                self.assertFalse(job.authorized)
                self.rejected(400, '/result', result, self.device)
                for _ in range(2):
                    self.request('/check', check, self.device)
                    self.assertIs(server.state.pointer_job, job)
                    self.assertFalse(job.issued)
                    self.assertFalse(job.ready)
                    self.assertFalse(job.authorized)
                self.rejected(400, '/result', result, self.device)
                move = self.request('/pointer', check, self.device)
                self.assertEqual(move['move']['seq'], 1)
                self.assertTrue(move['move']['home'])
                self.assertEqual(move['move']['report_units'], 7)
                self.assertEqual((move['move']['dx'] % 7, move['move']['dy'] % 7), (0, 0))
                self.assertFalse(move['calibrating'])
                self.assertIs(server.state.pointer_job, job)
                self.assertTrue(job.issued)
                self.assertFalse(self.request('/check', check, self.device)['ok'])
                self.assertEqual(self.request('/pointer', dict(check, seq=1, ok=True), self.device), {'wait': True})
                self.assertFalse(self.request('/check', check, self.device)['ok'])
                self.assertEqual(self.request('/pointer', dict(check, poll=True), self.device), {'wait': True})
                self.rejected(400, '/result', result, self.device)
                self.request('/frame', capture_payload(), self.phone)
                self.assertFalse(self.request('/check', check, self.device)['ok'])
                ready = self.request('/pointer', dict(check, poll=True), self.device)
                self.assertTrue(ready['ready'])
                self.assertNotIn('move', ready)
                self.assertFalse(job.authorized)
                self.rejected(400, '/result', result, self.device)
                self.assertTrue(self.request('/check', check, self.device)['ok'])
                self.assertTrue(job.authorized)
                self.assertTrue(self.request('/result', result, self.device)['ok'])
                self.rejected(400, '/result', result, self.device)
                self.assertEqual(len(server.state.task['history']), 1)

    def test_coordinate_preflight_rejects_changed_scene_and_missing_calibration(self):
        fit = calibrate_state(server.state)
        actions = [dict(type='tap', x=224, y=106, label='微信', scope='app_icon'),
                   dict(type='send', x=300, y=700, recipient='小明', text='你好'),
                   dict(type='swipe', direction='left')]
        for proposal in actions:
            for failure in ('changed_scene', 'missing_calibration'):
                with self.subTest(kind=proposal['type'], failure=failure):
                    server.state.finish()
                    self.request('/frame', capture_payload(), self.phone)
                    server.state.pointer_calibration = fit
                    server.state.begin('测试校准后的坐标操作')
                    action = server.state.propose(proposal, server.state.current_frame())
                    if failure == 'missing_calibration': server.state.pointer_calibration = None
                    if failure == 'changed_scene':
                        self.request('/frame', capture_payload(fill='black'), self.phone)
                    self.assertFalse(self.request('/check', dict(id=action['id']), self.device)['ok'])
                    self.assertIsNone(server.state.pointer_job)
                    self.rejected(400, '/result', dict(id=action['id'], ok=True), self.device)

    def test_next_reports_missing_calibration_and_grid_precision_without_issuing_action(self):
        for defect, message in (('missing_calibration', '校准'), ('grid_precision', '精度不足')):
            with self.subTest(defect=defect):
                server.state.finish()
                if defect == 'grid_precision':
                    self.assertTrue(calibrate_state(server.state, lambda ux, uy: (.87*ux+13, 2.1*uy-20))['verified'])
                    self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'done')
                else:
                    server.state.pointer_calibration = None
                server.state.begin('打开微信')
                # At synthetic gain 2.1, y=707 is 6.7 points from its closest grid position.
                proposal = dict(type='tap', x=224, y=707, label='微信', scope='app_icon')
                with patch.dict('os.environ', {'VISION_PROVIDER': 'anthropic'}), \
                     patch.object(server, 'decide', return_value=proposal):
                    reply = self.request('/next', token=self.device)
                self.assertIsNone(reply['action'])
                self.assertIn(message, reply['error'])
                self.assertNotIn('连接中断', reply['error'])
                self.assertIsNone(server.state.pending)
                self.assertIsNone(server.state.pointer_job)
                self.assertEqual(self.request('/next', token=self.device)['action'], None)

    def test_geometry_round_trip_cannot_revive_prepared_or_issued_job(self):
        for stage in ('prepared', 'issued'):
            with self.subTest(stage=stage):
                server.state.finish()
                self.request('/frame', capture_payload(), self.phone)
                self.assertTrue(calibrate_state(server.state)['verified'])
                server.state.begin('打开微信')
                action = server.state.propose(dict(type='tap', x=224, y=106, label='微信', scope='app_icon'),
                                             server.state.current_frame())
                check = dict(id=action['id'])
                self.assertTrue(self.request('/check', check, self.device)['ok'])
                job = server.state.pointer_job
                if stage == 'issued':
                    self.assertEqual(self.request('/pointer', check, self.device)['move']['seq'], 1)
                    self.assertEqual(self.request('/pointer', dict(check, seq=1, ok=True), self.device), {'wait': True})
                self.request('/frame', capture_payload(size=(844, 390), geometry=[844, 390, 'landscape']), self.phone)
                self.request('/frame', capture_payload(), self.phone)
                self.assertIsNone(server.state.pointer_calibration)
                self.assertIs(server.state.pointer_job, job)
                self.assertTrue(job.failure)
                self.assertFalse(self.request('/check', check, self.device)['ok'])
                pointer_request = dict(check, poll=True) if stage == 'issued' else check
                self.rejected(400, '/pointer', pointer_request, self.device)
                self.assertFalse(self.request('/check', check, self.device)['ok'])
                self.assertFalse(job.authorized)
                self.rejected(400, '/result', dict(check, ok=True), self.device)

    def test_legacy_device_can_pair_and_poll_status_but_cannot_calibrate(self):
        # Only the test State clock is changed; pairing still goes through HTTP.
        server.state.device_last_seen = 0
        self.device = self.request('/pair', dict(role='device', code=server.state.code,
                                               capabilities=['native_touch_v1', 'hid_delivery_v1']))['token']
        self.assertEqual(self.request('/next', token=self.device), {'action': None})
        request = urllib.request.Request('http://127.0.0.1:%d/status' % self.http.server_port,
                                         headers={'Authorization': 'Pair '+server.state.code})
        with self.client.open(request, timeout=5) as response: status = json.load(response)
        self.assertEqual(status['paired'], ['device', 'phone'])
        self.assertEqual(status['device_capabilities'], ['native_touch_v1', 'hid_delivery_v1'])
        self.rejected(400, '/calibration', dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000), self.phone)
        self.assertIsNone(server.state.calibration_session)
        self.assertIsNone(server.state.pointer_calibration)

    def test_delivery_capability_completes_nine_points_and_eighteen_http_reports(self):
        self.begin()
        session = server.state.calibration_session
        self.assertTrue(session.diagnostic()['delivery_required'])
        for index in range(9):
            moved = device_body(session, 'moved', ok=True, usb=usb_report(session, 'moved'))
            clicked = device_body(session, 'clicked', ok=True, usb=usb_report(session, 'clicked'))
            ux, uy = session.units
            x, y = synthetic_position(ux, uy)
            point = phone_body(session, 'point', x=x, y=y, touch_type='direct')
            self.request('/calibration/device', moved, self.device)
            self.request('/calibration', phone_body(session, 'armed'), self.phone)
            self.request('/calibration/device', device_body(session, 'permit'), self.device)
            self.request('/calibration', point, self.phone)
            self.request('/calibration/device', clicked, self.device)
            self.assertEqual(len(session.usb_reports), (index+1)*2)
            if index < 8: self.assertIsNone(server.state.pointer_calibration)
        self.assertTrue(server.state.pointer_calibration['verified'])
        self.rejected(400, '/calibration/device', clicked, self.device)
        self.assertEqual(len(session.diagnostic()['usb_reports']), 18)

    def test_delivery_http_rejects_missing_reports_and_incomplete_success(self):
        for defect in ('missing_move', 'missing_click', 'incomplete_click'):
            with self.subTest(defect=defect):
                self.begin()
                session = server.state.calibration_session
                self.assertTrue(session.diagnostic()['delivery_required'])
                if defect == 'missing_move':
                    body = device_body(session, 'moved', ok=True)
                else:
                    moved = device_body(session, 'moved', ok=True, usb=usb_report(session, 'moved'))
                    self.rejected(401, '/calibration/device', moved, self.phone)
                    self.assertEqual(session.usb_reports, [])
                    self.request('/calibration/device', moved, self.device)
                    self.request('/calibration', phone_body(session, 'armed'), self.phone)
                    self.request('/calibration/device', device_body(session, 'permit'), self.device)
                    body = device_body(session, 'clicked', ok=True)
                    if defect == 'incomplete_click': body['usb'] = usb_report(session, 'clicked', completed=1)
                if defect in ('missing_move', 'missing_click'): del body['usb']
                self.rejected(400, '/calibration/device', body, self.device)
                self.assertEqual(session.phase, 'failed')
                self.assertEqual(session.coarse_samples, [])
                self.assertIsNone(server.state.pointer_calibration)

    def test_roles_nine_points_and_polling_never_reissue_permitted_click(self):
        begin = dict(op='begin', page=PAGE, geometry=list(GEOMETRY), touch_window_ms=5000)
        self.rejected(401, '/calibration', begin, self.device)
        self.rejected(401, '/calibration/device', dict(op='permit'), self.phone)
        self.rejected(401, '/next', token=self.phone)
        self.assertIsNone(server.state.calibration_session)
        self.begin()
        session = server.state.calibration_session
        for index in range(9):
            self.assertIsNone(server.state.pointer_calibration)
            command = self.request('/next', token=self.device)['calibration']
            self.assertEqual(command['phase'], 'move')
            self.assertEqual(command['report_units'], 7)
            self.assertEqual(command['total'], 9)
            self.assertEqual(command['step'], index+1)
            self.assertEqual(command, self.request('/next', token=self.device)['calibration'])
            self.request('/calibration/device', device_body(session, 'moved', ok=True), self.device)
            self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'wait')
            self.request('/calibration', phone_body(session, 'poll'), self.phone)
            self.request('/calibration', phone_body(session, 'armed'), self.phone)
            self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'click')
            self.assertTrue(self.request('/calibration/device', device_body(session, 'permit'), self.device)['ok'])
            for _ in range(2):
                self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'wait')
            ux, uy = session.units
            x, y = synthetic_position(ux, uy)
            point = phone_body(session, 'point', x=x, y=y, touch_type='indirectPointer')
            clicked = device_body(session, 'clicked', ok=True)
            if index % 2:
                self.request('/calibration/device', clicked, self.device)
                self.request('/calibration', point, self.phone)
            else:
                self.request('/calibration', point, self.phone)
                self.request('/calibration/device', clicked, self.device)
        self.assertTrue(server.state.pointer_calibration['verified'])
        self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'done')
        self.assertEqual(self.request('/next', token=self.device), {'action': None})
        self.rejected(400, '/calibration/device', clicked, self.device)

    def test_cancel_and_duplicate_permit_block_late_completion(self):
        for cause in ('cancel', 'duplicate_permit'):
            with self.subTest(cause=cause):
                self.begin()
                session = server.state.calibration_session
                self.request('/calibration/device', device_body(session, 'moved', ok=True), self.device)
                self.request('/calibration', phone_body(session, 'armed'), self.phone)
                self.request('/calibration/device', device_body(session, 'permit'), self.device)
                if cause == 'cancel':
                    self.request('/calibration', phone_body(session, 'cancel'), self.phone)
                else:
                    self.rejected(400, '/calibration/device', device_body(session, 'permit'), self.device)
                self.rejected(400, '/calibration/device', device_body(session, 'clicked', ok=True), self.device)
                self.assertEqual(self.request('/next', token=self.device)['calibration']['phase'], 'failed')
                self.assertEqual(self.request('/next', token=self.device), {'action': None})
                self.assertEqual(session.index, 0)
                self.assertIsNone(server.state.pointer_calibration)


if __name__ == '__main__':
    unittest.main()
