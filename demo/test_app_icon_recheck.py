"""Offline check of the narrow Jev HTTP request and decision result."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import server


class AppIconRecheckAPITests(unittest.TestCase):
    def setUp(self):
        self.action = dict(type='tap', scope='app_icon', label='微信', x=240, y=114, id='local-only')
        self.frame = dict(width=390, height=844, point_width=390, point_height=844,
                          orientation='portrait', image='must-not-be-sent')
        self.observation = dict(visible='yes', confidence=.95, left=.5, right=.7, top=.1, bottom=.2)
        self.task = dict(id='test-task', text='打开微信',
                         perception={'observations':{'tap_app':self.observation}, 'frame':self.frame})
        state = server.State()
        state.task = self.task
        state_patch = patch.object(server, 'state', state)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_noul_threshold_and_wire_request_keep_selected_action_fixed(self):
        original = dict(self.action)
        for score, expected in ((.79, False), (.8, True)):
            with self.subTest(score=score):
                response = {'answers':{'icon_available':{'type':'noul', 'noul':score}}}
                with patch.dict('os.environ', {'TYPESAFE_API_KEY':'test'}, clear=True), \
                        patch('server.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as api, \
                        patch('server.decide', side_effect=AssertionError('Do not choose another action')):
                    result = server.recheck_app_icon(self.task, self.frame, self.action)
                self.assertIs(result, expected)
                self.assertEqual(self.action, original)
                request = api.call_args.args[0]
                self.assertEqual(request.full_url, server.jev.ENDPOINT)
                body = json.loads(request.data)
                self.assertEqual(set(body['questions']), {'icon_available'})
                self.assertEqual(body['state']['requested_app'], '微信')
                self.assertNotIn('user_request', body['state'])
                self.assertNotIn('must-not-be-sent', request.data.decode())
                self.assertNotIn('local-only', request.data.decode())
                self.assertEqual(server.state.vision_diagnostic['phase'], 'recheck_app_icon')
                self.assertEqual(server.state.vision_diagnostic['gate'], 'passed' if expected else 'action_validity')

    def test_unusable_visual_evidence_stops_without_a_network_request(self):
        for visible, confidence in (('no', .95), ('unclear', .95), ('yes', .89)):
            with self.subTest(visible=visible, confidence=confidence):
                self.observation.update(visible=visible, confidence=confidence)
                with patch('server.urllib.request.urlopen', side_effect=AssertionError('No API needed')) as api:
                    self.assertIs(server.recheck_app_icon(self.task, self.frame, self.action), False)
                api.assert_not_called()
                self.assertEqual(server.state.vision_diagnostic['gate'], 'perception_validity')

    def test_network_failure_does_not_produce_a_decision(self):
        with patch.dict('os.environ', {'TYPESAFE_API_KEY':'test'}, clear=True), \
                patch('server.urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(ValueError):
                server.recheck_app_icon(self.task, self.frame, self.action)
        self.assertEqual(server.state.vision_diagnostic['gate'], 'request_failed')

    def test_late_response_does_not_overwrite_another_tasks_diagnostic(self):
        marker = {'phase':'new_task'}
        def answer(*args, **kwargs):
            server.state.task = {'id':'new-task'}
            server.state.vision_diagnostic = marker
            return io.BytesIO(b'{"answers":{"icon_available":{"type":"noul","noul":0.95}}}')
        with patch.dict('os.environ', {'TYPESAFE_API_KEY':'test'}, clear=True), \
                patch('server.urllib.request.urlopen', side_effect=answer):
            self.assertIs(server.recheck_app_icon(self.task, self.frame, self.action), True)
        # This helper only returns evidence. State.pointer_step independently
        # rejects old task identities before permitting a physical click.
        self.assertIs(server.state.vision_diagnostic, marker)


if __name__ == '__main__':
    unittest.main()
