"""Offline app-icon recheck contract: synthetic JPEGs and mocked model calls only."""
import base64
import io
import os
import threading
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import calibration
import jev
import server


APP_TAP = dict(type='tap', x=196, y=196, label='微信', scope='app_icon')
CAPABILITIES = ['native_touch_v1', 'hid_delivery_v1', 'pointer_grid7_v1', 'home_swipe_v1']


def screenshot(scene='origin', geometry=(390, 844, 'portrait')):
    image = Image.new('RGB', geometry[:2], 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 170, 222, 222), fill=(30, 180, 80))
    if scene != 'origin':
        # A strong change outside the icon's protected region, not JPEG noise.
        draw.rectangle((20, 500, 100, 580), fill='black')
    if scene == 'changed_again':
        draw.rectangle((280, 500, 350, 580), fill=(150, 20, 20))
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=90)
    return dict(jpeg=base64.b64encode(stream.getvalue()).decode(),
                width=image.width, height=image.height, point_width=geometry[0],
                point_height=geometry[1], orientation=geometry[2])


def review_with_pixel_changes(points=()):
    # Real JPEG round trip: the exact decoded delta and count are asserted below.
    image = Image.new('RGB', (390, 844), (16, 16, 16))
    ImageDraw.Draw(image).rectangle((20, 500, 100, 580), fill=(80, 80, 80))
    for point in points:
        image.putpixel(point, (29, 16, 16))
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=100, subsampling=0, keep_rgb=True)
    return dict(jpeg=base64.b64encode(stream.getvalue()).decode(), width=390, height=844,
                point_width=390, point_height=844, orientation='portrait')


def flat_frame(value):
    image=Image.new('RGB',(390,844),(value,value,value))
    stream=io.BytesIO(); image.save(stream,format='JPEG',quality=100,subsampling=0,keep_rgb=True)
    return dict(jpeg=base64.b64encode(stream.getvalue()).decode(),width=390,height=844,
                point_width=390,point_height=844,orientation='portrait')


def perception(task, frame, previous, box=(176, 176, 216, 216)):
    _, options = jev.candidates(task, previous)
    observations = {name: dict(visible='no', confidence=.99, left=0, right=0, top=0, bottom=0)
                    for name in options if name != 'assist'}
    left, top, right, bottom = box
    observations['tap_app'] = dict(visible='yes', confidence=.99,
        left=left/frame['point_width'], right=right/frame['point_width'],
        top=top/frame['point_height'], bottom=bottom/frame['point_height'])
    return dict(observations=observations, frame=frame)


class NavigationRecheckTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'VISION_PROVIDER': 'jev'}).start()
        patch('server.time.monotonic', side_effect=lambda: self.now).start()
        patch('server.urllib.request.urlopen', side_effect=AssertionError('No network in recheck tests')).start()
        self.reader = patch('server.perceive', side_effect=perception).start()
        # create=True lets the contract fail on the old broad-planner path even
        # before the narrow review wrapper exists in the implementation.
        self.decider = patch('server.recheck_app_icon', return_value=True, create=True).start()
        self.planner = patch('server.decide', side_effect=AssertionError(
            'Post-move review must not call the initial action planner')).start()
        self.new_state()

    def tearDown(self):
        self.assertEqual(self.planner.call_count, 0, 'Post-move review called the initial planner')

    def new_state(self, action=None, propose=True):
        self.state = server.State()
        self.state.pair('phone', self.state.code)
        self.state.pair('device', self.state.code, CAPABILITIES)
        self.upload()
        # A fixture for an already-verified map, not a claim about real HID gain.
        self.state.pointer_calibration = dict(verified=True, algorithm=calibration.ALGORITHM,
            geometry=[390, 844, 'portrait'], gain=[1, 1], offset=[0, 0], rms=[0, 0])
        self.state.begin('打开微信')
        self.initial_frame = self.state.current_frame()
        self.state.task['perception'] = perception(self.state.task, self.initial_frame, None)
        self.action = self.state.propose(action or APP_TAP, self.initial_frame) if propose else None
        self.reader.reset_mock()
        self.decider.reset_mock()
        return self.state

    def upload(self, scene='origin', geometry=(390, 844, 'portrait')):
        self.state.add_frame(screenshot(scene, geometry))
        return self.state.current_frame()

    def issue_move(self):
        self.assertTrue(self.state.check_pending(self.action['id']))
        reply = self.state.pointer_step(self.action['id'], {})
        self.assertEqual(reply['move']['seq'], 1)
        self.assertEqual(reply['move']['report_units'], 7)
        self.assertFalse(reply.get('ready', False))
        return reply

    def ack(self):
        return self.state.pointer_step(self.action['id'], dict(seq=1, ok=True))

    def poll(self):
        return self.state.pointer_step(self.action['id'], dict(poll=True))

    def start_review(self):
        self.issue_move()
        self.assertEqual(self.ack(), {'wait': True})
        return self.upload('review')

    def assert_not_authorized(self):
        self.assertFalse(self.state.check_pending(self.action['id']))
        with self.assertRaises(ValueError):
            self.state.result(self.action['id'], True)

    def test_box_retry_keeps_first_frame_as_baseline_until_final_check(self):
        for stage in ('reader_return','before_decision','decision_return','final_check','post_ready_poll','stable'):
            with self.subTest(stage=stage):
                self.new_state()
                self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,(191,176,216,216))
                self.issue_move(); self.assertEqual(self.ack(),{'wait':True})
                self.state.add_frame(flat_frame(16))
                first=self.state.current_frame()
                self.assertEqual(self.poll(),{'wait':True})
                self.assertEqual(self.poll(),{'wait':True})
                self.state.add_frame(flat_frame(28))
                candidate=self.state.current_frame()
                same,detail=server.compare_frame_pixels(first,candidate,APP_TAP)
                self.assertTrue(same); self.assertEqual(detail['body_max_channel_delta'],12)
                def second_reader(task,frame,previous):
                    if stage == 'reader_return': self.state.add_frame(flat_frame(40))
                    return perception(task,frame,previous)
                self.reader.side_effect=second_reader
                if stage == 'reader_return':
                    with self.assertRaises(ValueError): self.poll()
                    self.assert_not_authorized(); continue
                self.assertEqual(self.poll(),{'wait':True})
                if stage == 'before_decision': self.state.add_frame(flat_frame(40))
                if stage == 'decision_return':
                    def changed_decision(*_):
                        self.state.add_frame(flat_frame(40))
                        return True
                    self.decider.side_effect=changed_decision
                if stage in ('before_decision','decision_return'):
                    with self.assertRaises(ValueError): self.poll()
                    self.assert_not_authorized(); self.decider.side_effect=None; continue
                self.assertTrue(self.poll()['ready'])
                if stage == 'post_ready_poll':
                    # F2 agrees with F0 (delta 12) but not the reviewed F1
                    # (delta 24): both comparisons must still be enforced.
                    self.state.add_frame(flat_frame(4))
                    with self.assertRaises(ValueError): self.poll()
                    self.assert_not_authorized(); continue
                self.state.add_frame(flat_frame(40 if stage == 'final_check' else 27))
                self.assertEqual(self.state.check_pending(self.action['id']),stage == 'stable')
                if stage == 'final_check': self.assert_not_authorized()

    def test_changed_frame_move_ack_is_recorded_without_model_or_replay(self):
        self.issue_move()
        self.upload('review')
        self.assertEqual(self.ack(), {'wait': True})
        self.reader.assert_not_called()
        self.decider.assert_not_called()
        self.assertFalse(self.state.check_pending(self.action['id']))
        # The ACK frame cannot itself satisfy the post-movement frame barrier.
        self.assertEqual(self.poll(), {'wait': True})
        self.reader.assert_not_called()
        with self.assertRaises(ValueError):
            self.ack()
        self.assert_not_authorized()

    def test_changed_frame_does_not_make_invalid_move_receipts_acceptable(self):
        for body in (dict(seq=1, ok=False), dict(seq=2, ok=True), dict(seq=True, ok=True)):
            with self.subTest(body=body):
                self.new_state()
                self.issue_move()
                self.upload('review')
                with self.assertRaises(ValueError):
                    self.state.pointer_step(self.action['id'], body)
                self.reader.assert_not_called()
                self.decider.assert_not_called()
                self.assert_not_authorized()

    def test_two_polls_share_fixed_evidence_preserve_coordinates_and_require_final_check(self):
        review = self.start_review()
        original = dict(self.state.pending)
        self.assertEqual(self.poll(), {'wait': True})
        self.reader.assert_called_once()
        self.decider.assert_not_called()
        self.assertEqual(self.state.pending_frame, self.initial_frame)
        self.assert_not_authorized()
        # A newer, identical frame is allowed; it is not substituted into the
        # second model call after the reader has examined the fixed review frame.
        latest = self.upload('review')
        reply = self.poll()
        self.assertTrue(reply.get('ready'))
        self.assertNotIn('move', reply)
        self.decider.assert_called_once()
        read_task, read_frame, read_previous = self.reader.call_args.args
        review_task, review_frame, review_action = self.decider.call_args.args
        self.assertEqual(read_task['id'], original['task'])
        self.assertEqual(read_frame, review)
        self.assertEqual(review_frame, review)
        self.assertEqual(review_action, original)
        self.assertEqual(review_action['id'], self.action['id'])
        self.assertIsNone(read_previous)
        self.assertEqual(review_task['perception']['frame'], review)
        self.assertEqual(review_task['perception']['observations']['tap_app'],
                         perception(read_task, review, read_previous)['observations']['tap_app'])
        self.assertEqual(self.state.pending, original)
        self.assertEqual(self.state.pending_frame, review)
        self.assertTrue(server.compare_frame_pixels(self.state.pending_frame, latest, original)[0])
        with self.assertRaises(ValueError):
            self.state.result(self.action['id'], True)
        self.assertTrue(self.state.check_pending(self.action['id']))
        self.state.result(self.action['id'], True)

    def test_unchanged_post_move_frame_needs_no_models(self):
        self.issue_move()
        self.assertEqual(self.ack(), {'wait': True})
        self.upload()
        self.assertTrue(self.poll()['ready'])
        self.reader.assert_not_called()
        self.decider.assert_not_called()
        with self.assertRaises(ValueError):
            self.state.result(self.action['id'], True)
        self.assertTrue(self.state.check_pending(self.action['id']))

    def test_first_ready_cannot_start_a_later_model_recheck(self):
        self.issue_move()
        self.assertEqual(self.ack(), {'wait': True})
        self.upload()
        self.assertTrue(self.poll()['ready'])
        self.upload('review')
        self.assert_not_authorized()
        with self.assertRaises(ValueError):
            self.poll()
        self.reader.assert_not_called()
        self.decider.assert_not_called()

    def test_initial_proposal_and_pre_move_pixel_changes_still_fail(self):
        self.new_state(propose=False)
        self.upload('review')
        with self.assertRaises(ValueError):
            self.state.propose(APP_TAP, self.initial_frame)
        self.new_state()
        self.upload('review')
        with self.assertRaises(ValueError):
            self.state.pointer_step(self.action['id'], {})
        self.reader.assert_not_called()
        self.decider.assert_not_called()

    def test_other_actions_and_non_jev_provider_keep_strict_pixel_gate(self):
        actions = [dict(APP_TAP, scope=None),
                   dict(type='send', x=196, y=196, recipient='Alice', text='hello'),
                   dict(type='swipe', direction='left')]
        for action in actions:
            with self.subTest(action=action):
                self.new_state(action)
                self.start_review()
                with self.assertRaises(ValueError):
                    self.poll()
                self.reader.assert_not_called()
                self.decider.assert_not_called()
                self.assert_not_authorized()
        self.new_state()
        self.start_review()
        with patch.dict(os.environ, {'VISION_PROVIDER': 'anthropic'}):
            with self.assertRaises(ValueError):
                self.poll()
        self.reader.assert_not_called()
        self.decider.assert_not_called()

    def test_narrow_recheck_requires_strict_true(self):
        rejected = [False, None, 1, 'true']
        for decision in rejected:
            with self.subTest(decision=decision):
                self.new_state()
                self.decider.return_value = decision
                self.start_review()
                self.assertEqual(self.poll(), {'wait': True})
                with self.assertRaises(ValueError):
                    self.poll()
                self.assert_not_authorized()
                with self.assertRaises(ValueError):
                    self.poll()
                self.reader.assert_called_once()
                self.decider.assert_called_once()

    def test_target_box_must_contain_original_coordinate_and_full_six_point_margin(self):
        for box in ((270, 176, 310, 216), (192, 192, 200, 200), (191, 176, 216, 216)):
            with self.subTest(box=box):
                self.new_state()
                self.reader.side_effect = lambda task, frame, prev: perception(task, frame, prev, box)
                self.start_review()
                self.assertEqual(self.poll(), {'wait': True})
                if box[0] <= 196 <= box[2] and box[1] <= 196 <= box[3]:
                    # A centered but insufficient margin permits only one
                    # complete reread of a later, otherwise unchanged frame.
                    self.assertEqual(self.poll(), {'wait':True})
                    self.upload('review')
                    self.assertEqual(self.poll(), {'wait':True})
                with self.assertRaises(ValueError):
                    self.poll()
                self.assert_not_authorized()
                self.assertEqual((self.state.pending['x'], self.state.pending['y']), (196, 196))

    def test_review_box_diagnostics_record_points_and_containment_without_raw_data(self):
        # Both a matching icon and a shifted icon must leave numeric evidence;
        # the latter must still fail before authorizing the physical click.
        for box, contained in (((176.1234, 176.2344, 216.3456, 216.4567), True),
                               ((270.1234, 176.2344, 310.3456, 216.4567), False)):
            with self.subTest(box=box):
                self.new_state()
                self.reader.side_effect = lambda task, frame, prev: perception(task, frame, prev, box)
                review_frame = self.start_review()
                self.assertEqual(self.poll(), {'wait': True})
                if contained:
                    self.assertTrue(self.poll().get('ready'))
                else:
                    with self.assertRaisesRegex(ValueError, '目标图标定位复核未通过，请在手机上查看'):
                        self.poll()
                    self.assert_not_authorized()
                diagnostic = self.state.pointer_diagnostic
                self.assertEqual(diagnostic['review_box_points'], [round(v, 3) for v in box])
                self.assertEqual(diagnostic['review_target_margin_points'], 6)
                self.assertIs(diagnostic['review_target_contained'], contained)
                distances = {'left':196-box[0], 'top':196-box[1],
                             'right':box[2]-196, 'bottom':box[3]-196}
                self.assertEqual(diagnostic['review_clearance_points'],
                                 {edge:round(value, 3) for edge,value in distances.items()})
                self.assertCountEqual(diagnostic['review_failed_edges'],
                                      [edge for edge,value in distances.items() if value < 6])
                self.assertEqual(diagnostic['review_frame_sequence'], review_frame['sequence'])
                self.assertNotIn('image', diagnostic)
                self.assertNotIn('label', diagnostic)
                self.assertNotIn('observations', diagnostic)

    def test_review_box_diagnostics_never_fabricate_invalid_observation(self):
        for invalid in (None, '0.2', float('nan'), float('inf'), -0.1, 1.1):
            with self.subTest(invalid=invalid):
                self.new_state()
                def read(task, frame, previous):
                    evidence = perception(task, frame, previous)
                    evidence['observations']['tap_app']['left'] = invalid
                    return evidence
                self.reader.side_effect = read
                self.start_review()
                self.assertEqual(self.poll(), {'wait': True})
                with self.assertRaises(ValueError): self.poll()
                diagnostic = self.state.pointer_diagnostic
                self.assertNotIn('review_box_points', diagnostic)
                self.assertNotIn('review_target_margin_points', diagnostic)
                self.assertNotIn('review_target_contained', diagnostic)
                self.assertNotIn('review_clearance_points', diagnostic)
                self.assertNotIn('review_failed_edges', diagnostic)
                self.assertNotIn('review_frame_sequence', diagnostic)
                self.assert_not_authorized()

    def test_review_containment_uses_unrounded_bounds(self):
        # 190.0004 rounds to 190.000 for display, but is still outside the
        # original target's 6-point safety margin and must not be authorized.
        box = (190.0004, 176, 216, 216)
        self.reader.side_effect = lambda task, frame, prev: perception(task, frame, prev, box)
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        self.assertEqual(self.poll(), {'wait': True})
        failed = self.state.pointer_diagnostic['first_failed_review']
        self.assertEqual(failed['clearance_points']['left'], 6.0)
        self.assertIn('left', failed['failed_edges'])
        self.upload('review')
        self.assertEqual(self.poll(), {'wait': True})
        with self.assertRaisesRegex(ValueError, '目标图标定位复核未通过，请在手机上查看'):
            self.poll()
        self.assertEqual(self.state.pointer_diagnostic['review_box_points'][0], 190.0)
        self.assertIs(self.state.pointer_diagnostic['review_target_contained'], False)
        self.assertEqual(self.state.pointer_diagnostic['review_clearance_points']['left'], 6.0)
        self.assertIn('left', self.state.pointer_diagnostic['review_failed_edges'])
        self.assert_not_authorized()

    def test_near_edge_box_rereads_once_only_after_new_unchanged_frame(self):
        marginal=(191,176,216,216)  # Original point is inside, but left margin is only 5.
        self.reader.side_effect = lambda task, frame, prev: perception(task,frame,prev,marginal)
        original_review=self.start_review()
        original_action=dict(self.state.pending)
        self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.poll(), {'wait':True})
        self.assert_not_authorized()
        first=self.state.pointer_diagnostic['first_failed_review']
        self.assertEqual(first['box_points'], list(marginal))
        self.assertEqual(first['frame_sequence'], original_review['sequence'])
        self.assertEqual(first['clearance_points']['left'], 5)
        self.assertEqual(first['failed_edges'], ['left'])
        self.assertNotIn('image', first)
        self.assertNotIn('label', first)
        self.assertEqual(self.poll(), {'wait':True})  # The conflict frame itself cannot be reused.
        self.assertEqual(self.reader.call_count,1)
        self.assertEqual(self.decider.call_count,1)
        new_review=self.upload('review')
        self.assertGreater(new_review['sequence'],original_review['sequence'])
        self.reader.side_effect=perception
        self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.reader.call_count,2)
        self.assertEqual(self.reader.call_args.args[1],new_review)
        final=self.poll()
        self.assertTrue(final.get('ready'))
        self.assertNotIn('move',final)
        self.assertEqual(self.decider.call_count,2)
        self.assertEqual(self.state.pending,original_action)
        self.assertEqual(self.state.pending_frame,new_review)
        self.assertEqual(self.state.pointer_diagnostic['first_failed_review'],first)
        self.assertTrue(self.state.pointer_diagnostic['review_target_contained'])
        self.assertTrue(self.state.check_pending(self.action['id']))
        self.state.result(self.action['id'],True)

    def test_near_edge_box_retry_rejects_second_insufficient_margin(self):
        marginal=(192,192,200,200)
        self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,marginal)
        self.start_review(); self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.poll(), {'wait':True})
        first=self.state.pointer_diagnostic['first_failed_review']
        self.assertCountEqual(first['failed_edges'],['left','top','right','bottom'])
        self.upload('review'); self.assertEqual(self.poll(), {'wait':True})
        with self.assertRaisesRegex(ValueError,'目标图标定位复核未通过'): self.poll()
        self.assert_not_authorized()
        self.assertEqual(self.reader.call_count,2)
        self.assertEqual(self.decider.call_count,2)
        self.assertEqual(self.state.pointer_diagnostic['first_failed_review'],first)
        self.assertFalse(self.state.pointer_diagnostic['review_target_contained'])

    def test_original_point_outside_box_never_starts_margin_retry(self):
        for box in ((196.0004,176,216,216),(270,176,310,216)):
            with self.subTest(box=box):
                self.new_state()
                self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,box)
                self.start_review(); self.assertEqual(self.poll(), {'wait':True})
                with self.assertRaisesRegex(ValueError,'目标图标定位复核未通过'): self.poll()
                self.assert_not_authorized()
                self.assertEqual(self.reader.call_count,1)
                self.assertEqual(self.decider.call_count,1)
                self.assertNotIn('first_failed_review',self.state.pointer_diagnostic)

    def test_near_edge_retry_requires_the_reviewed_baseline_to_remain_unchanged(self):
        marginal=(191,176,216,216)
        self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,marginal)
        review=self.start_review(); self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.state.pointer_diagnostic['first_failed_review']['frame_sequence'],review['sequence'])
        self.upload('changed_again')
        with self.assertRaises(ValueError): self.poll()
        self.assert_not_authorized()
        self.assertEqual(self.reader.call_count,1)
        self.assertEqual(self.decider.call_count,1)

    def test_screen_change_before_near_edge_check_uses_only_existing_change_retry(self):
        marginal=(191,176,216,216)
        self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,marginal)
        self.start_review(); self.assertEqual(self.poll(), {'wait':True})
        def change_during_decision(*_):
            self.upload('changed_again')
            return True
        self.decider.side_effect=change_during_decision
        self.assertEqual(self.poll(), {'wait':True})
        self.assertNotIn('first_failed_review',self.state.pointer_diagnostic)
        self.decider.side_effect=None
        self.upload('changed_again')
        self.assertEqual(self.poll(), {'wait':True})
        with self.assertRaisesRegex(ValueError,'目标图标定位复核未通过'): self.poll()
        self.assert_not_authorized()
        self.assertEqual(self.reader.call_count,2)
        self.assertEqual(self.decider.call_count,2)

    def test_near_edge_retry_cannot_spend_second_budget_on_new_change(self):
        self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,(191,176,216,216))
        self.start_review(); self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.poll(), {'wait':True})
        self.upload('review')
        self.reader.side_effect=perception
        self.assertEqual(self.poll(), {'wait':True})
        def change_during_second_decision(*_):
            self.upload('changed_again')
            return True
        self.decider.side_effect=change_during_second_decision
        with self.assertRaises(ValueError): self.poll()
        self.assert_not_authorized()
        self.assertEqual(self.reader.call_count,2)
        self.assertEqual(self.decider.call_count,2)

    def test_near_edge_retry_is_single_flight_and_cannot_outlive_capture(self):
        self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,(191,176,216,216))
        self.start_review(); self.assertEqual(self.poll(), {'wait':True})
        self.assertEqual(self.poll(), {'wait':True})
        self.upload('review')
        entered,release=threading.Event(),threading.Event()
        outcomes=[]
        def blocked_reader(task,frame,previous):
            entered.set()
            if not release.wait(3): raise AssertionError('Timed out waiting for test')
            return perception(task,frame,previous)
        self.reader.side_effect=blocked_reader
        def request():
            try: outcomes.append(self.poll())
            except ValueError as exc: outcomes.append(exc)
        worker=threading.Thread(target=request,daemon=True); worker.start()
        try:
            self.assertTrue(entered.wait(2))
            concurrent=self.poll()
            self.assertFalse(concurrent.get('ready',False))
            self.assertNotIn('move',concurrent)
            self.state.stop_capture()
        finally:
            release.set(); worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes),1)
        if isinstance(outcomes[0],dict):
            self.assertFalse(outcomes[0].get('ready',False))
            self.assertNotIn('move',outcomes[0])
        self.assert_not_authorized()

    def test_near_edge_retry_cannot_survive_invalidated_state(self):
        for kind in ('task','pairing','stop','geometry','geometry_back','calibration','deadline','stale'):
            with self.subTest(kind=kind):
                self.new_state()
                self.reader.side_effect=lambda task,frame,prev: perception(task,frame,prev,(191,176,216,216))
                self.start_review(); self.assertEqual(self.poll(), {'wait':True})
                self.assertEqual(self.poll(), {'wait':True})
                self.invalidate(kind)
                try: reply=self.poll()
                except ValueError: pass
                else:
                    self.assertFalse(reply.get('ready',False))
                    self.assertNotIn('move',reply)
                self.assert_not_authorized()

    def test_review_invalid_frame_has_no_fabricated_box_diagnostics(self):
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        self.upload('review', (844, 390, 'landscape'))
        with self.assertRaises(ValueError): self.poll()
        diagnostic = self.state.pointer_diagnostic
        self.assertNotIn('review_box_points', diagnostic)
        self.assertNotIn('review_target_margin_points', diagnostic)
        self.assertNotIn('review_target_contained', diagnostic)
        self.assertNotIn('review_clearance_points', diagnostic)
        self.assertNotIn('review_failed_edges', diagnostic)
        self.assertNotIn('review_frame_sequence', diagnostic)
        self.assert_not_authorized()

    def test_review_box_diagnostics_clear_when_changed_frame_requires_retry(self):
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        def change_during_decision(*_):
            self.upload('changed_again')
            return True
        self.decider.side_effect = change_during_decision
        self.assertEqual(self.poll(), {'wait': True})
        diagnostic = self.state.pointer_diagnostic
        self.assertNotIn('review_box_points', diagnostic)
        self.assertNotIn('review_target_margin_points', diagnostic)
        self.assertNotIn('review_target_contained', diagnostic)
        self.assertNotIn('review_clearance_points', diagnostic)
        self.assertNotIn('review_failed_edges', diagnostic)
        self.assertNotIn('review_frame_sequence', diagnostic)
        self.decider.side_effect = None
        self.upload('changed_again')
        self.assertEqual(self.poll(), {'wait': True})
        self.assertTrue(self.poll()['ready'])
        self.assertEqual(self.state.pointer_diagnostic['review_box_points'], [176, 176, 216, 216])
        self.assertTrue(self.state.pointer_diagnostic['review_target_contained'])
        self.assertEqual(self.state.pointer_diagnostic['review_clearance_points'],
                         {'left':20, 'top':20, 'right':20, 'bottom':20})
        self.assertEqual(self.state.pointer_diagnostic['review_failed_edges'], [])
        self.assertEqual(self.state.pointer_diagnostic['review_frame_sequence'],
                         self.state.pointer_job.recheck['frame']['sequence'])

    def test_late_review_box_diagnostics_cannot_contaminate_new_task(self):
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        def cancel_during_decision(*_):
            self.state.finish()
            self.state.begin('打开设置')
            self.state.pointer_diagnostic = {'phase':'new_task_sentinel'}
            return True
        self.decider.side_effect = cancel_during_decision
        with self.assertRaises(ValueError): self.poll()
        self.assertEqual(self.state.pointer_diagnostic, {'phase':'new_task_sentinel'})

    def test_unclear_or_low_confidence_icon_cannot_be_confirmed_by_decider(self):
        for visible, confidence in (('no', .99), ('unclear', .99), ('yes', .89)):
            with self.subTest(visible=visible, confidence=confidence):
                self.new_state()
                def read(task, frame, previous):
                    evidence = perception(task, frame, previous)
                    evidence['observations']['tap_app'].update(visible=visible, confidence=confidence)
                    return evidence
                self.reader.side_effect = read
                self.start_review()
                self.assertEqual(self.poll(), {'wait': True})
                with self.assertRaises(ValueError):
                    self.poll()
                self.assert_not_authorized()

    def test_reader_or_decider_error_stops_without_retry(self):
        for stage in ('reader', 'decider'):
            with self.subTest(stage=stage):
                self.new_state()
                self.reader.side_effect = perception
                self.decider.side_effect = None
                getattr(self, stage).side_effect = ValueError('synthetic provider rejection')
                self.start_review()
                if stage == 'decider':
                    self.assertEqual(self.poll(), {'wait': True})
                with self.assertRaises(ValueError):
                    self.poll()
                self.assert_not_authorized()
                with self.assertRaises(ValueError):
                    self.poll()
                self.reader.assert_called_once()
                self.assertEqual(self.decider.call_count, int(stage == 'decider'))

    def test_changed_frame_restarts_read_once_only_after_new_frame(self):
        for stage in ('between', 'during_decider'):
            with self.subTest(stage=stage):
                self.new_state()
                first = self.start_review()
                original = dict(self.state.pending)
                self.assertEqual(self.poll(), {'wait': True})
                if stage == 'between':
                    conflict = self.upload('changed_again')
                else:
                    def decide(*_):
                        self.upload('changed_again')
                        return True
                    self.decider.side_effect = decide
                    conflict = None
                self.assertEqual(self.poll(), {'wait': True})
                if conflict is None: conflict = self.state.current_frame()
                self.assertEqual(self.state.pending_frame, self.initial_frame)
                self.assert_not_authorized()
                self.reader.assert_called_once()
                self.decider.side_effect = None
                before_decider = self.decider.call_count
                # The conflicting frame itself is never used to restart review.
                self.assertEqual(self.poll(), {'wait': True})
                self.reader.assert_called_once()
                self.assertEqual(self.decider.call_count, before_decider)
                newer = self.upload('changed_again')
                self.assertGreater(newer['sequence'], conflict['sequence'])
                self.assertEqual(self.poll(), {'wait': True})
                self.assertEqual(self.reader.call_count, 2)
                self.assertEqual(self.reader.call_args.args[1], newer)
                self.assertNotEqual(newer['sequence'], first['sequence'])
                reply = self.poll()
                self.assertTrue(reply.get('ready'))
                self.assertNotIn('move', reply)
                self.assertEqual(self.decider.call_count, before_decider+1)
                _, reviewed, action = self.decider.call_args.args
                self.assertEqual(reviewed, newer)
                self.assertEqual(action, original)
                self.assertEqual(self.state.pending_frame, newer)
                self.assertTrue(self.state.check_pending(self.action['id']))
                self.state.result(self.action['id'], True)

    def test_second_review_change_spends_single_budget_and_cannot_authorize(self):
        for stage in ('between','during_decider'):
            with self.subTest(stage=stage):
                self.new_state(); self.reader.side_effect=perception
                self.decider.side_effect=None; self.decider.return_value=True
                self.start_review(); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
                if stage == 'between': self.upload('review')
                else:
                    def decide(*_):
                        self.upload('review')
                        return True
                    self.decider.side_effect=decide
                with self.assertRaises(ValueError): self.poll()
                self.assert_not_authorized()
                self.assertEqual(self.reader.call_count, 2)
                self.assertEqual(self.decider.call_count, int(stage=='during_decider'))

    def test_retry_ready_still_requires_unchanged_frame_at_final_check(self):
        self.start_review(); self.assertEqual(self.poll(), {'wait': True})
        self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
        self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
        reply=self.poll()
        self.assertTrue(reply.get('ready'))
        self.assertNotIn('move',reply)
        self.upload('review')
        self.assert_not_authorized()
        self.assertEqual(self.reader.call_count,2)
        self.assertEqual(self.decider.call_count,1)

    def test_retry_new_identical_invalid_jpeg_cannot_authorize_click(self):
        self.start_review()
        self.assertEqual(self.poll(), {'wait':True})
        self.upload('changed_again')
        self.assertEqual(self.poll(), {'wait':True})
        invalid=screenshot('review')
        invalid['jpeg']=base64.b64encode(b'\xff\xd8not-a-valid-jpeg').decode()
        self.state.add_frame(invalid)
        try:
            self.poll()
            self.poll()
        except ValueError:
            pass
        self.assert_not_authorized()
        self.assertFalse(self.state.pointer_job.ready)

    def test_retried_review_still_rejects_changed_target_and_small_box(self):
        for box in ((270, 176, 310, 216), (192, 192, 200, 200)):
            with self.subTest(box=box):
                self.new_state(); self.reader.side_effect = perception
                self.start_review(); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again')
                self.reader.side_effect = lambda task, frame, prev: perception(task, frame, prev, box)
                self.assertEqual(self.poll(), {'wait': True})
                with self.assertRaises(ValueError): self.poll()
                self.assert_not_authorized()
                self.assertEqual((self.state.pending['x'], self.state.pending['y']), (196,196))

    def test_retried_review_never_accepts_an_absent_or_uncertain_target(self):
        for visible, confidence in (('no', .99), ('unclear', .99), ('yes', .89)):
            with self.subTest(visible=visible, confidence=confidence):
                self.new_state(); self.reader.side_effect = perception
                self.start_review(); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again')
                def read(task, frame, previous):
                    evidence=perception(task,frame,previous)
                    evidence['observations']['tap_app'].update(visible=visible,confidence=confidence)
                    return evidence
                self.reader.side_effect = read
                self.assertEqual(self.poll(), {'wait': True})
                with self.assertRaises(ValueError): self.poll()
                self.assert_not_authorized()

    def test_retry_wait_cannot_survive_invalidated_state(self):
        for kind in ('task','pairing','stop','geometry','geometry_back','calibration','deadline','stale'):
            with self.subTest(kind=kind):
                self.new_state(); self.reader.side_effect = perception
                self.start_review(); self.assertEqual(self.poll(), {'wait': True})
                self.upload('changed_again'); self.assertEqual(self.poll(), {'wait': True})
                self.invalidate(kind)
                try: reply = self.poll()
                except ValueError: pass
                else:
                    self.assertFalse(reply.get('ready', False))
                    self.assertNotIn('move', reply)
                self.assert_not_authorized()

    def test_retry_model_call_is_single_flight_and_stale_result_cannot_authorize(self):
        for stage in ('reader','decider'):
            for kind in ('stop','pairing','geometry','calibration','deadline'):
                with self.subTest(stage=stage,kind=kind):
                    self.new_state(); self.reader.side_effect=perception
                    self.decider.side_effect=None; self.decider.return_value=True
                    self.start_review(); self.assertEqual(self.poll(), {'wait':True})
                    self.upload('changed_again'); self.assertEqual(self.poll(), {'wait':True})
                    self.upload('changed_again')
                    if stage == 'decider': self.assertEqual(self.poll(), {'wait':True})
                    entered,release=threading.Event(),threading.Event()
                    outcomes=[]
                    def model(task,frame,previous_or_action):
                        entered.set()
                        if not release.wait(3): raise AssertionError('Timed out waiting for test')
                        return perception(task,frame,previous_or_action) if stage == 'reader' else True
                    getattr(self,stage).side_effect=model
                    def request():
                        try: outcomes.append(self.poll())
                        except ValueError as exc: outcomes.append(exc)
                    worker=threading.Thread(target=request,daemon=True); worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        concurrent=self.poll()
                        self.assertFalse(concurrent.get('ready',False))
                        self.assertNotIn('move',concurrent)
                        self.invalidate(kind)
                    finally:
                        release.set(); worker.join(4)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(len(outcomes),1)
                    if isinstance(outcomes[0],dict):
                        self.assertFalse(outcomes[0].get('ready',False))
                        self.assertNotIn('move',outcomes[0])
                    self.assert_not_authorized()

    def test_small_refresh_between_reader_and_decider_uses_reviewed_frame(self):
        outside = tuple((280 + 8*n, 600) for n in range(5))
        for points, permitted in ((outside[:2], True), (outside[:4], True),
                                  (outside, False), (((196, 196),), False)):
            with self.subTest(points=points):
                self.new_state()
                self.reader.side_effect = perception
                self.decider.side_effect = None
                self.decider.return_value = True
                issued = self.issue_move()
                self.assertEqual(issued['move']['seq'], 1)
                self.assertEqual(self.ack(), {'wait': True})
                self.state.add_frame(review_with_pixel_changes())
                review = self.state.current_frame()
                first = self.poll()
                self.assertEqual(first, {'wait': True})
                self.reader.assert_called_once()
                self.decider.assert_not_called()
                self.state.add_frame(review_with_pixel_changes(points))
                latest = self.state.current_frame()
                self.assertGreater(latest['sequence'], review['sequence'])
                same, detail = server.compare_frame_pixels(review, latest, APP_TAP)
                self.assertEqual(detail['body_max_channel_delta'], 14)
                self.assertEqual(detail['body_pixels_over_tolerance'], len(points))
                self.assertEqual(detail['protected_region_pixels_over_tolerance'],
                                 int((196, 196) in points))
                self.assertEqual(same, permitted)
                self.assertEqual(detail['small_refresh_exception'], permitted)
                if not permitted:
                    self.assertEqual(self.poll(), {'wait': True})
                    self.assertEqual(self.state.pending_frame, self.initial_frame)
                    self.decider.assert_not_called()
                    self.assert_not_authorized()
                    continue
                reply = self.poll()
                self.assertTrue(reply.get('ready'))
                self.assertNotIn('move', reply)
                self.reader.assert_called_once()
                self.decider.assert_called_once()
                _, reviewed, action = self.decider.call_args.args
                self.assertEqual(reviewed, review)
                self.assertEqual(action['id'], self.action['id'])
                self.assertEqual((action['x'], action['y']), (196, 196))
                self.assertEqual(self.state.pending_frame, review)
                self.assertTrue(self.state.check_pending(self.action['id']))
                self.state.result(self.action['id'], True)

    def test_successful_recheck_is_not_repeated_on_another_change(self):
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        self.assertTrue(self.poll()['ready'])
        self.upload('changed_again')
        with self.assertRaises(ValueError):
            self.poll()
        self.assert_not_authorized()
        self.reader.assert_called_once()
        self.decider.assert_called_once()

    def test_final_check_rejects_fresh_change_after_success_without_another_poll(self):
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        self.assertTrue(self.poll()['ready'])
        self.upload('changed_again')
        self.assert_not_authorized()
        self.reader.assert_called_once()
        self.decider.assert_called_once()

    def invalidate(self, kind):
        if kind == 'task':
            self.state.finish()
            self.state.begin('打开设置')
        elif kind == 'pairing':
            self.state.device_last_seen = self.now-server.DEVICE_REPAIR_TIMEOUT-1
            self.state.pair('device', self.state.code, CAPABILITIES)
        elif kind == 'stop':
            self.state.stop_capture()
        elif kind == 'geometry':
            self.upload('review', (844, 390, 'landscape'))
        elif kind == 'geometry_back':
            self.upload('review', (844, 390, 'landscape'))
            self.upload('review')
        elif kind == 'calibration':
            self.state.pointer_calibration = None
        elif kind == 'deadline':
            self.now += 61
            self.upload('review')  # Fresh capture isolates the expired job deadline.
        elif kind == 'stale':
            self.now += server.MAX_FRAME_AGE+1

    def test_inflight_model_result_cannot_survive_invalidated_state(self):
        for stage in ('reader', 'decider'):
            for kind in ('task', 'pairing', 'stop', 'geometry', 'geometry_back', 'calibration', 'deadline', 'stale'):
                with self.subTest(stage=stage, invalidation=kind):
                    self.new_state()
                    self.reader.side_effect = perception
                    self.decider.side_effect = None
                    self.decider.return_value = True
                    self.start_review()
                    if stage == 'decider':
                        self.assertEqual(self.poll(), {'wait': True})
                    def model(task, frame, previous_or_action):
                        answer = perception(task, frame, previous_or_action) if stage == 'reader' else True
                        self.invalidate(kind)
                        return answer
                    getattr(self, stage).side_effect = model
                    try:
                        reply = self.poll()
                    except ValueError:
                        pass
                    else:
                        self.assertFalse(reply.get('ready', False))
                        self.assertNotIn('move', reply)
                    getattr(self, stage).assert_called_once()
                    self.assert_not_authorized()

    def test_both_model_calls_run_outside_state_lock(self):
        def assert_unlocked():
            acquired = threading.Event()
            def probe():
                with self.state.lock:
                    acquired.set()
            worker = threading.Thread(target=probe, daemon=True)
            worker.start()
            self.assertTrue(acquired.wait(1), 'Model call held State.lock')
            worker.join(1)
        def read(task, frame, previous):
            assert_unlocked()
            return perception(task, frame, previous)
        def decide(*_):
            assert_unlocked()
            return True
        self.reader.side_effect, self.decider.side_effect = read, decide
        self.start_review()
        self.assertEqual(self.poll(), {'wait': True})
        self.assertTrue(self.poll()['ready'])

    def test_concurrent_poll_cannot_duplicate_models_move_or_authorize(self):
        for stage in ('reader', 'decider'):
            with self.subTest(stage=stage):
                self.new_state()
                self.reader.side_effect = perception
                self.decider.side_effect = None
                self.decider.return_value = True
                self.start_review()
                if stage == 'decider':
                    self.assertEqual(self.poll(), {'wait': True})
                entered, release = threading.Event(), threading.Event()
                outcomes = []
                def model(task, frame, previous_or_action):
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError('Test did not release blocked model')
                    return perception(task, frame, previous_or_action) if stage == 'reader' else True
                getattr(self, stage).side_effect = model
                def request():
                    try:
                        outcomes.append(self.poll())
                    except ValueError as exc:
                        outcomes.append(exc)
                first = threading.Thread(target=request, daemon=True)
                first.start()
                try:
                    self.assertTrue(entered.wait(1), 'First poll never reached model')
                    second = threading.Thread(target=request, daemon=True)
                    second.start()
                    second.join(1)
                    self.assertFalse(second.is_alive(), 'Concurrent poll blocked behind a model call')
                    self.assertEqual(len(outcomes), 1)
                    if isinstance(outcomes[0], dict):
                        self.assertFalse(outcomes[0].get('ready', False))
                        self.assertNotIn('move', outcomes[0])
                    self.assert_not_authorized()
                    self.assertEqual(getattr(self, stage).call_count, 1)
                finally:
                    release.set()
                    first.join(2)
                self.assertFalse(first.is_alive())
                self.assertLessEqual(self.reader.call_count, 1)
                self.assertLessEqual(self.decider.call_count, 1)


if __name__ == '__main__':
    unittest.main()
