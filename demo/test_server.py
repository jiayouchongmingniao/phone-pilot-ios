import base64
import unittest
from unittest.mock import patch
import server

class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = server.State()
        self.payload = {'jpeg': base64.b64encode(b'\xff\xd8example').decode(),
                        'width':390,'height':844,'point_width':390,'point_height':844,'orientation':'portrait'}
    def test_pair_per_role_and_restart(self):
        token = self.state.pair('phone', self.state.code)
        self.assertTrue(self.state.auth('phone', token))
        with self.assertRaises(ValueError): self.state.pair('phone', self.state.code)
        self.assertFalse(server.State().auth('phone', token))
        with self.assertRaises(ValueError): self.state.pair('device', 'wrong')
    def test_one_action_and_result(self):
        self.state.add_frame(self.payload)
        self.state.begin('打开设置')
        frame = self.state.current_frame()
        action = self.state.propose({'type':'tap','x':100,'y':200},frame)
        with self.assertRaises(ValueError): self.state.propose({'type':'tap','x':1,'y':2},frame)
        self.state.result(action['id'], True)
        with self.assertRaises(ValueError): self.state.result(action['id'], True)
        self.assertIsNone(self.state.current_frame(after=frame['sequence']))
    def test_changed_frame_blocks_action(self):
        self.state.add_frame(self.payload); self.state.begin('tap')
        frame = self.state.current_frame()
        self.payload['jpeg']=base64.b64encode(b'\xff\xd8different').decode()
        self.state.add_frame(self.payload)
        with self.assertRaises(ValueError): self.state.propose({'type':'tap','x':1,'y':2},frame)
    def test_dangerous_and_invalid_actions_blocked(self):
        frame={'point_width':390,'point_height':844}
        for action in ({'type':'tap','x':float('nan'),'y':1}, {'type':'tap','x':400,'y':1},
                       {'type':'key','key':'return'}, {'type':'send'}, {'type':'type','text':'你好'}):
            with self.assertRaises(ValueError): server.validate_action(action,frame)
    def test_no_key(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError,'OPENAI_API_KEY'): server.openai_json('responses',b'{}','application/json')
if __name__ == '__main__': unittest.main()
