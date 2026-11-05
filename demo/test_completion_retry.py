"""Offline HTTP contracts for bounded app-completion revalidation."""
import base64
import contextlib
import http.client
import io
import json
import ssl
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from PIL import Image

import calibration
import server


def screenshot(color):
    image = Image.new('RGB', (390, 844), color)
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=95)
    return dict(jpeg=base64.b64encode(stream.getvalue()).decode(), width=390, height=844,
                point_width=390, point_height=844, orientation='portrait')


class CompletionRetryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.previous_state = server.state
        server.state = server.State()
        self.state = server.state
        self.phone = self.state.pair('phone', self.state.code)
        self.device = self.state.pair('device', self.state.code,
            ['native_touch_v1', 'hid_delivery_v1', 'pointer_grid7_v1'])
        self.state.add_frame(screenshot('white'))
        self.state.pointer_calibration = dict(verified=True, algorithm=calibration.ALGORITHM,
            geometry=[390, 844, 'portrait'], gain=[1, 1], offset=[0, 0], rms=[0, 0])
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.base = 'http://127.0.0.1:%d' % self.http.server_port
        self.provider = patch.dict('os.environ', {'VISION_PROVIDER':'jev'})
        self.provider.start()
        self.addCleanup(self.provider.stop)
        self.addCleanup(self.close_server)

    def close_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(timeout=3)
        server.state = self.previous_state

    def poll(self):
        request = urllib.request.Request(self.base+'/next',
            headers={'Authorization':'Bearer '+self.device})
        with self.opener.open(request, timeout=4) as response:
            return json.load(response)

    def post(self, path, payload):
        request = urllib.request.Request(self.base+path, data=json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+self.device})
        with self.opener.open(request, timeout=4) as response:
            return json.load(response)

    def start_after_tap(self):
        self.state.begin('打开微信')
        initial = self.state.current_frame()
        pending = self.state.propose(
            dict(type='tap', scope='app_icon', x=150, y=302, label='微信'), initial)
        self.assertTrue(self.state.check_pending(pending['id']))
        self.assertEqual(self.state.pointer_step(pending['id'], {})['move']['seq'], 1)
        self.assertEqual(self.state.pointer_step(pending['id'], {'seq':1, 'ok':True}),
                         {'wait':True})
        self.state.add_frame(screenshot('white'))
        self.assertTrue(self.state.pointer_step(pending['id'], {'poll':True})['ready'])
        self.assertTrue(self.state.check_pending(pending['id']))
        self.state.result(pending['id'], True)
        self.assertTrue(self.state.last_execution['ok'])
        self.state.add_frame(screenshot('white'))

    def read(self, task, frame, previous):
        self.assertEqual(previous['type'], 'tap')
        return {'frame':frame, 'observations':{}}

    def start_retry(self):
        self.assertIsNone(self.poll().get('action'))
        self.state.add_frame(screenshot('black'))
        rejected = self.poll()
        self.assertIsNone(rejected.get('action'))
        self.assertNotIn('error', rejected)
        self.assertIsNotNone(self.state.task)
        # A newly received image after the failed comparison is required.
        waiting = self.poll()
        self.assertIsNone(waiting.get('action'))
        self.assertNotIn('error', waiting)
        self.assertIsNotNone(self.state.task)

    def test_changed_completion_is_rechecked_once_using_a_strictly_new_frame(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read) as reader, \
                patch('server.decide', return_value={'type':'done'}) as decider:
            self.start_retry()
            self.assertEqual(reader.call_count, 1)
            self.state.add_frame(screenshot('blue'))
            newest = self.state.current_frame()
            self.assertIsNone(self.poll().get('action'))
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(reader.call_args.args[1]['sequence'], newest['sequence'])
            result = self.poll()
            self.assertEqual(result['action']['type'], 'done')
            self.assertEqual(decider.call_count, 2)
            self.assertEqual(decider.call_args.args[1]['sequence'], newest['sequence'])
            self.assertEqual(self.state.last_execution['type'], 'tap')
            self.assertTrue(self.state.last_execution['ok'])

    def test_second_change_stops_without_another_attempt(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read) as reader, \
                patch('server.decide', return_value={'type':'done'}) as decider:
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            self.assertIsNone(self.poll().get('action'))
            self.state.add_frame(screenshot('red'))
            failed = self.poll()
            self.assertIsNone(failed.get('action'))
            self.assertIn('error', failed)
            self.assertIsNone(self.state.task)
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(decider.call_count, 2)
            self.assertTrue(self.state.last_execution['ok'])

    def test_unconfirmed_new_frame_stops_instead_of_announcing_completion(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read) as reader, \
                patch('server.decide', side_effect=[{'type':'done'},
                    {'type':'assist','label':'target_unclear'}]) as decider:
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            self.assertIsNone(self.poll().get('action'))
            failed = self.poll()
            self.assertIsNone(failed.get('action'))
            self.assertIn('error', failed)
            self.assertIsNone(self.state.task)
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(decider.call_count, 2)

    def test_unchanged_app_completion_does_not_recheck(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read) as reader, \
                patch('server.decide', return_value={'type':'done'}) as decider:
            self.assertIsNone(self.poll().get('action'))
            result = self.poll()
            self.assertEqual(result['action']['type'], 'done')
            reader.assert_called_once()
            decider.assert_called_once()

    def test_other_completion_still_stops_on_frame_change(self):
        self.state.begin('输入hello')
        pending = self.state.propose({'type':'type','text':'hello'}, self.state.current_frame())
        self.state.result(pending['id'], True)
        self.state.add_frame(screenshot('white'))
        def read(task, frame, previous): return {'frame':frame, 'observations':{}}
        with patch('server.perceive', side_effect=read) as reader, \
                patch('server.decide', return_value={'type':'done'}):
            self.assertIsNone(self.poll().get('action'))
            self.state.add_frame(screenshot('black'))
            result = self.poll()
            self.assertIsNone(result.get('action'))
            self.assertIn('error', result)
            self.assertIsNone(self.state.task)
            reader.assert_called_once()

    def test_concurrent_retry_poll_does_not_repeat_reader_and_late_cancel_cannot_resume(self):
        self.start_after_tap()
        entered, release = threading.Event(), threading.Event()
        replies = []
        def reader(task, frame, previous):
            if frame['sequence'] > first_sequence:
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return self.read(task, frame, previous)
        with patch('server.perceive', side_effect=reader) as read_mock, \
                patch('server.decide', return_value={'type':'done'}):
            first_sequence = self.state.sequence
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            thread = threading.Thread(target=lambda: replies.append(self.poll()), daemon=True)
            try:
                thread.start()
                self.assertTrue(entered.wait(timeout=2))
                concurrent = self.poll()
                self.assertIsNone(concurrent.get('action'))
                self.assertEqual(read_mock.call_count, 2)
                self.state.stop_capture()
            finally:
                release.set()
                thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(replies), 1)
            self.assertIsNone(replies[0].get('action'))
            self.assertIsNone(self.state.task)
            self.assertIsNone(self.state.pending)

    def test_old_evidence_expires_even_when_fresh_current_frame_matches(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read) as reader, \
                patch('server.decide', return_value={'type':'done'}) as decider:
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            self.assertIsNone(self.poll().get('action'))
            with self.state.lock:
                self.state.task['perception']['frame']['received'] -= 41
            self.state.add_frame(screenshot('blue'))
            result = self.poll()
            self.assertIsNone(result.get('action'))
            self.assertIn('error', result)
            self.assertIsNone(self.state.task)
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(decider.call_count, 1)

    def test_retry_deadline_expires_even_if_frames_and_device_are_fresh(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read), \
                patch('server.decide', return_value={'type':'done'}):
            self.start_retry()
            future = time.monotonic() + 76
            with patch('server.time.monotonic', return_value=future):
                self.state.add_frame(screenshot('blue'))
                self.state.device_last_seen = future
                result = self.poll()
            self.assertIsNone(result.get('action'))
            self.assertIn('error', result)
            self.assertIsNone(self.state.task)

    def test_device_lost_then_check_cannot_revive_retry(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read), \
                patch('server.decide', return_value={'type':'done'}) as decider:
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            with self.state.lock:
                self.state.device_last_seen -= 61
            self.assertFalse(self.post('/check', {'id':'not-an-action'})['ok'])
            self.assertIsNone(self.state.task)
            result = self.poll()
            self.assertIsNone(result.get('action'))
            self.assertIn('error', result)
            self.assertEqual(decider.call_count, 1)

    def test_retry_cancels_when_geometry_changes(self):
        self.start_after_tap()
        with patch('server.perceive', side_effect=self.read), \
                patch('server.decide', return_value={'type':'done'}) as decider:
            self.start_retry()
            frame = screenshot('blue')
            frame['orientation'] = 'landscape'
            self.state.add_frame(frame)
            result = self.poll()
            self.assertIsNone(result.get('action'))
            self.assertIn('error', result)
            self.assertIsNone(self.state.task)
            self.assertEqual(decider.call_count, 1)

    def test_phone_repair_during_retry_reader_cannot_revive_old_task(self):
        self.start_after_tap()
        entered, release = threading.Event(), threading.Event()
        replies = []
        def reader(task, frame, previous):
            if frame['sequence'] > first_sequence:
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return self.read(task, frame, previous)
        with patch('server.perceive', side_effect=reader), \
                patch('server.decide', return_value={'type':'done'}):
            first_sequence = self.state.sequence
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            thread = threading.Thread(target=lambda: replies.append(self.poll()), daemon=True)
            try:
                thread.start()
                self.assertTrue(entered.wait(timeout=2))
                self.state.stop_capture()
                self.state.pair('phone',self.state.code)
                self.state.add_frame(screenshot('white'))
                replacement = self.state.begin('打开相机')
            finally:
                release.set()
                thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(replies), 1)
            self.assertIsNone(replies[0].get('action'))
            self.assertEqual(self.state.task['id'], replacement['id'])
            self.assertNotIn('perception', self.state.task)
            self.assertIsNone(self.state.pending)

    def run_evidence_ages_out_during_model_call(self, stage):
        self.start_after_tap()
        first_sequence = self.state.sequence
        clock = None
        decisions = []
        def expire_evidence(frame):
            nonlocal clock
            now = time.monotonic()
            self.assertLessEqual(now-frame['received'], 40)
            clock = patch('server.time.monotonic', return_value=now+41)
            clock.start()
            # The current capture and the device are fresh, and the capture is
            # pixel-identical to the earlier evidence, which itself has aged out.
            self.state.add_frame(screenshot('blue'))
            self.state.device_last_seen = now+41
        def reader(task, frame, previous):
            if stage == 'reader' and frame['sequence'] > first_sequence:
                expire_evidence(frame)
            return self.read(task, frame, previous)
        def decide(task, frame, previous):
            decisions.append(frame['sequence'])
            if stage == 'decider' and len(decisions) == 2:
                expire_evidence(frame)
            return {'type':'done'}
        try:
            with patch('server.perceive', side_effect=reader) as read_mock, \
                    patch('server.decide', side_effect=decide):
                self.start_retry()
                self.state.add_frame(screenshot('blue'))
                response = self.poll()
                if stage == 'decider':
                    self.assertIsNone(response.get('action'))
                    response = self.poll()
                self.assertIsNone(response.get('action'))
                self.assertIn('error', response)
                self.assertIsNone(self.state.task)
                self.assertIsNone(self.state.pending)
                self.assertEqual(read_mock.call_count, 2)
                self.assertEqual(len(decisions), 1 if stage == 'reader' else 2)
        finally:
            if clock: clock.stop()

    def test_evidence_expires_during_retry_reader_even_with_matching_fresh_frame(self):
        self.run_evidence_ages_out_during_model_call('reader')

    def test_evidence_expires_during_retry_decider_even_with_matching_fresh_frame(self):
        self.run_evidence_ages_out_during_model_call('decider')

    def run_late_decision_after_cancel(self, repair):
        self.start_after_tap()
        entered, release = threading.Event(), threading.Event()
        decisions, replies = [], []
        def decide(task, frame, previous):
            decisions.append(frame['sequence'])
            if len(decisions) == 2:
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return {'type':'done'}
        with patch('server.perceive', side_effect=self.read), \
                patch('server.decide', side_effect=decide):
            self.start_retry()
            self.state.add_frame(screenshot('blue'))
            self.assertIsNone(self.poll().get('action'))
            thread = threading.Thread(target=lambda: replies.append(self.poll()), daemon=True)
            try:
                thread.start()
                self.assertTrue(entered.wait(timeout=2))
                self.state.stop_capture()
                replacement = None
                if repair:
                    self.state.pair('phone', self.state.code)
                    self.state.add_frame(screenshot('white'))
                    replacement = self.state.begin('打开相机')
            finally:
                release.set()
                thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(replies), 1)
            self.assertIsNone(replies[0].get('action'))
            self.assertIsNone(self.state.pending)
            if repair:
                self.assertEqual(self.state.task['id'], replacement['id'])
                self.assertNotIn('perception', self.state.task)
            else:
                self.assertIsNone(self.state.task)

    def test_late_retry_decision_after_stop_does_not_complete(self):
        self.run_late_decision_after_cancel(False)

    def test_late_retry_decision_after_phone_repair_does_not_pollute_new_task(self):
        self.run_late_decision_after_cancel(True)

    def test_send_completion_never_enters_app_retry(self):
        self.state.begin('给张三发送hello')
        initial = self.state.current_frame()
        pending = self.state.propose(dict(type='send', x=150, y=302,
                                           recipient='张三', text='hello'), initial)
        self.assertTrue(self.state.check_pending(pending['id']))
        self.assertEqual(self.state.pointer_step(pending['id'], {})['move']['seq'], 1)
        self.assertEqual(self.state.pointer_step(pending['id'], {'seq':1, 'ok':True}),
                         {'wait':True})
        self.state.add_frame(screenshot('white'))
        self.assertTrue(self.state.pointer_step(pending['id'], {'poll':True})['ready'])
        self.assertTrue(self.state.check_pending(pending['id']))
        self.state.result(pending['id'], True)
        self.state.add_frame(screenshot('white'))
        def reader(task, frame, previous): return {'frame':frame, 'observations':{}}
        with patch('server.perceive', side_effect=reader) as read_mock, \
                patch('server.decide', return_value={'type':'done'}) as decide_mock:
            self.assertIsNone(self.poll().get('action'))
            self.state.add_frame(screenshot('black'))
            result = self.poll()
            self.assertIsNone(result.get('action'))
            self.assertIn('error', result)
            self.assertIsNone(self.state.task)
            read_mock.assert_called_once()
            decide_mock.assert_called_once()


    def reset_retry_fixture(self):
        self.state = server.State(); server.state = self.state
        self.phone = self.state.pair('phone', self.state.code)
        self.device = self.state.pair('device', self.state.code,
            ['native_touch_v1','hid_delivery_v1','pointer_grid7_v1'])
        self.state.add_frame(screenshot('white'))
        self.state.pointer_calibration = dict(verified=True, algorithm=calibration.ALGORITHM,
            geometry=[390,844,'portrait'], gain=[1,1], offset=[0,0], rms=[0,0])

    def observation_response(self, visible='yes', confidence=.95):
        observation={'done':dict(visible=visible,confidence=confidence,left=0,right=0,top=0,bottom=0)}
        body={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(observation)}]}
        return io.BytesIO(json.dumps(body).encode())

    def test_network_disconnect_retries_once_with_new_frame_and_no_new_hid(self):
        errors=(lambda:http.client.RemoteDisconnected('private'),
                lambda:http.client.IncompleteRead(b'private'),
                lambda:ConnectionResetError('private'),
                lambda:urllib.error.URLError(http.client.RemoteDisconnected('private')),
                lambda:urllib.error.URLError(http.client.IncompleteRead(b'private')),
                lambda:urllib.error.URLError(ConnectionResetError('private')))
        for make_error in errors:
            with self.subTest(error=type(make_error()).__name__):
                self.reset_retry_fixture(); self.start_after_tap()
                original=dict(self.state.last_execution)
                effects=[make_error(),self.observation_response()]
                with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                        patch('server.urllib.request.urlopen',side_effect=effects) as network, \
                        patch('server.decide',return_value={'type':'done'}) as decide, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertNotIn('error',self.poll())
                    self.assertIsNotNone(self.state.task)
                    self.assertNotIn('error',self.poll())
                    self.assertEqual(network.call_count,1)
                    self.state.add_frame(screenshot('blue')); newest=self.state.current_frame()
                    self.assertIsNone(self.poll().get('action'))
                    self.assertEqual(network.call_count,2)
                    self.assertEqual(self.state.task['perception']['frame']['sequence'],newest['sequence'])
                    self.assertEqual(self.poll()['action']['type'],'done')
                    self.assertEqual(decide.call_count,1)
                    self.assertEqual(decide.call_args.args[1]['sequence'],newest['sequence'])
                    self.assertEqual(self.state.last_execution,original)
                    self.assertIsNone(self.state.provider_failure)
                    self.assertNotIn('private',output.getvalue())

    def test_network_and_frame_changes_share_one_retry_budget(self):
        for second_disconnect in (True,False):
            with self.subTest(second_disconnect=second_disconnect):
                self.reset_retry_fixture(); self.start_after_tap()
                second=http.client.RemoteDisconnected('again') if second_disconnect else self.observation_response()
                with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                        patch('server.urllib.request.urlopen',side_effect=[http.client.RemoteDisconnected('first'),second]) as network, \
                        patch('server.decide',return_value={'type':'done'}) as decide, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotIn('error',self.poll())
                    self.state.add_frame(screenshot('blue')); result=self.poll()
                    if not second_disconnect:
                        self.assertNotIn('error',result)
                        self.state.add_frame(screenshot('red')); result=self.poll()
                    self.assertIsNone(result.get('action')); self.assertIn('error',result)
                    self.assertIsNone(self.state.task); self.assertEqual(network.call_count,2)
                    self.assertEqual(decide.call_count,0 if second_disconnect else 1)
                    self.assertTrue(self.state.last_execution['ok'])

    def test_completion_network_http_tls_and_bad_json_never_retry(self):
        for case in ('401','429','tls','tls_wrapped','timeout','timeout_wrapped','oserror','urlerror','json'):
            with self.subTest(case=case):
                self.reset_retry_fixture(); self.start_after_tap()
                if case in ('401','429'):
                    effect=urllib.error.HTTPError('https://example.invalid',int(case),'test',{},io.BytesIO(b'{}'))
                elif case=='tls': effect=ssl.SSLError('private')
                elif case=='tls_wrapped': effect=urllib.error.URLError(ssl.SSLError('private'))
                elif case=='timeout': effect=TimeoutError('private')
                elif case=='timeout_wrapped': effect=urllib.error.URLError(TimeoutError('private'))
                elif case=='oserror': effect=OSError('private')
                elif case=='urlerror': effect=urllib.error.URLError('private')
                else: effect=io.BytesIO(b'not JSON')
                with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                        patch('server.urllib.request.urlopen',side_effect=[effect]) as network, \
                        contextlib.redirect_stdout(io.StringIO()):
                    result=self.poll()
                    self.assertIsNone(result.get('action')); self.assertIn('error',result)
                    self.assertIsNone(self.state.task); self.assertEqual(network.call_count,1)
                    self.assertTrue(self.state.last_execution['ok'])

    def test_network_disconnect_before_app_click_or_after_input_never_retries(self):
        for case in ('initial','input'):
            with self.subTest(case=case):
                self.reset_retry_fixture()
                if case=='initial': self.state.begin('打开微信')
                else:
                    self.state.begin('输入hello')
                    pending=self.state.propose({'type':'type','text':'hello'},self.state.current_frame())
                    self.state.result(pending['id'],True); self.state.add_frame(screenshot('white'))
                with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                        patch('server.urllib.request.urlopen',side_effect=http.client.RemoteDisconnected('private')) as network, \
                        contextlib.redirect_stdout(io.StringIO()):
                    result=self.poll()
                    self.assertIsNone(result.get('action')); self.assertIn('error',result)
                    self.assertIsNone(self.state.task); self.assertEqual(network.call_count,1)

    def test_frame_change_then_transport_disconnect_cannot_start_second_retry(self):
        self.start_after_tap()
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                patch('server.urllib.request.urlopen',side_effect=[self.observation_response(),
                    http.client.RemoteDisconnected('private')]) as network, \
                patch('server.decide',return_value={'type':'done'}) as decide, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertNotIn('error',self.poll())
            self.state.add_frame(screenshot('black'))
            self.assertNotIn('error',self.poll())
            self.state.add_frame(screenshot('blue'))
            failed=self.poll()
            self.assertIsNone(failed.get('action')); self.assertIn('error',failed)
            self.assertIsNone(self.state.task); self.assertEqual(network.call_count,2)
            self.assertEqual(decide.call_count,1); self.assertTrue(self.state.last_execution['ok'])

    def test_network_retry_rejects_unconfirmed_app_screen(self):
        self.start_after_tap()
        decision={'model':'jev-1.14.0','answers':{
            'next_action':{'type':'choice','choice':'done','confidence':.99,'probabilities':{'done':1.,'assist':0.}},
            'done_requested':{'type':'noul','noul':.99}}}
        for visible,confidence in (('no',.99),('unclear',.99),('yes',.89)):
            with self.subTest(visible=visible,confidence=confidence):
                self.reset_retry_fixture(); self.start_after_tap()
                effects=[http.client.RemoteDisconnected('private'),self.observation_response(visible,confidence),
                         io.BytesIO(json.dumps(decision).encode())]
                with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','TYPESAFE_API_KEY':'test'}), \
                        patch('server.urllib.request.urlopen',side_effect=effects) as network, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotIn('error',self.poll())
                    self.state.add_frame(screenshot('blue'))
                    self.assertIsNone(self.poll().get('action'))
                    failed=self.poll()
                    self.assertIsNone(failed.get('action')); self.assertIn('error',failed)
                    self.assertIsNone(self.state.task); self.assertEqual(network.call_count,3)

    def test_network_disconnect_after_send_never_retries(self):
        self.state.begin('给张三发送hello')
        pending=self.state.propose(dict(type='send',x=150,y=302,recipient='张三',text='hello'),self.state.current_frame())
        self.assertTrue(self.state.check_pending(pending['id']))
        self.state.pointer_step(pending['id'],{})
        self.state.pointer_step(pending['id'],{'seq':1,'ok':True})
        self.state.add_frame(screenshot('white'))
        self.assertTrue(self.state.pointer_step(pending['id'],{'poll':True})['ready'])
        self.assertTrue(self.state.check_pending(pending['id']))
        self.state.result(pending['id'],True); self.state.add_frame(screenshot('white'))
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                patch('server.urllib.request.urlopen',side_effect=http.client.RemoteDisconnected('private')) as network, \
                contextlib.redirect_stdout(io.StringIO()):
            failed=self.poll()
            self.assertIsNone(failed.get('action')); self.assertIn('error',failed)
            self.assertIsNone(self.state.task); self.assertEqual(network.call_count,1)

    def test_network_retry_expired_device_cannot_resume_from_check(self):
        self.start_after_tap()
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                patch('server.urllib.request.urlopen',side_effect=http.client.RemoteDisconnected('private')) as network, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertNotIn('error',self.poll())
            self.state.add_frame(screenshot('blue'))
            with self.state.lock:self.state.device_last_seen-=61
            self.assertFalse(self.post('/check',{'id':'not-an-action'})['ok'])
            self.assertIsNone(self.state.task)
            failed=self.poll()
            self.assertIsNone(failed.get('action')); self.assertIn('error',failed)
            self.assertEqual(network.call_count,1)

    def test_network_retry_late_failure_cannot_change_repaired_task_diagnostics(self):
        self.start_after_tap(); entered,release=threading.Event(),threading.Event()
        replies=[]; calls=[]
        def open_provider(*args,**kwargs):
            calls.append(1)
            if len(calls)==1: raise http.client.RemoteDisconnected('first')
            entered.set()
            if not release.wait(timeout=3): raise AssertionError('test wait timed out')
            raise http.client.RemoteDisconnected('old request')
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'}), \
                patch('server.urllib.request.urlopen',side_effect=open_provider), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertNotIn('error',self.poll())
            self.state.add_frame(screenshot('blue'))
            thread=threading.Thread(target=lambda:replies.append(self.poll()),daemon=True)
            try:
                thread.start(); self.assertTrue(entered.wait(timeout=2))
                self.assertIsNone(self.poll().get('action')); self.assertEqual(len(calls),2)
                self.state.stop_capture(); self.state.pair('phone',self.state.code)
                self.state.add_frame(screenshot('white')); replacement=self.state.begin('打开相机')
                self.state.provider_failure=None
            finally:
                release.set(); thread.join(timeout=4)
            self.assertFalse(thread.is_alive()); self.assertIsNone(replies[0].get('action'))
            self.assertEqual(self.state.task['id'],replacement['id'])
            self.assertIsNone(self.state.provider_failure); self.assertIsNone(self.state.pending)


if __name__ == '__main__':
    unittest.main()
