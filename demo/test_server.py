import base64
import io
import json
import threading
import urllib.request
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
            with self.assertRaisesRegex(ValueError,'DASHSCOPE_API_KEY'): server.transcribe(b'RIFFtest')
class HTTPTests(unittest.TestCase):
    def test_local_coordinator_flow(self):
        old = server.state
        server.state = server.State()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        root = 'http://127.0.0.1:%d' % http.server_port
        def post(path, value, token=None):
            payload = value if isinstance(value, bytes) else json.dumps(value).encode()
            headers = {'Authorization':'Bearer '+token} if token else {}
            with urllib.request.urlopen(urllib.request.Request(root+path, payload, headers), timeout=3) as response:
                return json.load(response)
        try:
            phone=post('/pair', {'role':'phone','code':server.state.code})['token']
            device=post('/pair', {'role':'device','code':server.state.code})['token']
            jpeg=base64.b64encode(b'\xff\xd8test').decode()
            post('/frame', {'jpeg':jpeg,'width':390,'height':844,'point_width':390,'point_height':844},phone)
            with patch('server.transcribe', return_value='打开设置'):
                post('/audio', b'RIFFtest', device)
            with patch('server.decide', return_value={'type':'tap','x':100,'y':200}):
                req=urllib.request.Request(root+'/next', headers={'Authorization':'Bearer '+device})
                with urllib.request.urlopen(req, timeout=3) as response: action=json.load(response)['action']
            self.assertTrue(post('/check', {'id':action['id']}, device)['ok'])
            post('/result', {'id':action['id'],'ok':True}, device)
            self.assertFalse(post('/check', {'id':action['id']}, device)['ok'])
            post('/stop', {}, phone)
            self.assertIsNone(server.state.current_frame())
        finally:
            http.shutdown(); http.server_close(); thread.join(); server.state=old

class ProviderTests(unittest.TestCase):
    def response(self, value):
        return io.BytesIO(json.dumps(value).encode())
    @patch('server.urllib.request.urlopen')
    def test_qwen_wav_request(self, urlopen):
        urlopen.return_value = self.response({'output':{'text':'打开设置'}})
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY':'test'}, clear=True):
            self.assertEqual(server.transcribe(b'RIFFexample'), '打开设置')
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'qwen-audio-3.1-asr-flash')
        self.assertTrue(payload['input']['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,'))
    @patch('server.urllib.request.urlopen')
    def test_anthropic_vision_request(self, urlopen):
        urlopen.return_value = self.response({'content':[{'type':'text','text':'{"type":"assist"}'}]})
        frame = {'image':'aGVsbG8=', 'point_width':390, 'point_height':844, 'width':390, 'height':844}
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'test'}, clear=True):
            self.assertEqual(server.decide({'text':'hello'}, frame, None)['type'], 'assist')
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)['model'], 'claude-haiku-5-5')

if __name__ == '__main__': unittest.main()
