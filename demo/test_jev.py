import contextlib
import http.client
import io
import json
import unittest
from unittest.mock import patch

import jev
import server
from test_server import screenshot


class JevTests(unittest.TestCase):
    def setUp(self):
        self.frame = dict(screenshot(), image=screenshot()['jpeg'])
        self.task = {'text':'打开微信'}
        self.context,self.options = jev.candidates(self.task,{'type':'open_app'})

    def observations(self, options=None):
        return {name:{'visible':'yes','confidence':0.99,'left':0.1,'right':0.2,'top':0.2,'bottom':0.3}
                for name in (options or self.options) if name != 'assist'}

    def prepared(self, task, previous=None):
        _,options=jev.candidates(task,previous)
        return dict(task,perception={'frame':self.frame,'observations':self.observations(options)})

    def response(self, name='tap_app', options=None):
        options = options or self.options
        answers = {'next_action':{'type':'choice','choice':name,'confidence':0.99,
            'probabilities':{key:float(key == name) for key in options}}}
        if name != 'assist':
            if not (name == 'done' and options[name].get('app_completion')):
                answers[name+'_valid'] = {'type':'noul','noul':0.99}
            if name == 'tap_app': answers['tap_app_requested'] = {'type':'noul','noul':0.99}
            if name == 'done' and options[name].get('app_completion'):
                answers['done_requested'] = {'type':'noul','noul':0.99}
            if options[name]['target']:
                answers[name+'_box'] = {'type':'box','confidence':0.99,
                    'box':{'left':0.1,'right':0.2,'top':0.2,'bottom':0.3}}
        return {'model':jev.MODEL,'answers':answers}

    def test_native_request_and_default_provider(self):
        with patch.dict('os.environ',{'TYPESAFE_API_KEY':'test-secret'},clear=True), \
                patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(self.response()).encode())) as api:
            action=server.decide(self.prepared(self.task,{'type':'open_app'}),self.frame,{'type':'open_app'})
        self.assertEqual(action,{'type':'tap','label':'微信','x':58.50000000000001,'y':211.0,'scope':'app_icon'})
        request=api.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.typesafe.ai/v1/systemone')
        headers={k.lower():v for k,v in request.header_items()}
        self.assertEqual(headers['authorization'],'Bearer test-secret')
        self.assertNotIn('typesafe-danger-zone-images',headers)
        self.assertNotIn('x-api-key',headers)
        body=json.loads(request.data)
        self.assertEqual(body['model'],'jev-1.14.0')
        self.assertEqual(body['state']['observations'],self.observations())
        self.assertNotIn('assets',body)
        self.assertNotIn(self.frame['image'],json.dumps(body))
        self.assertEqual(body['questions']['tap_app_valid']['type'],'noul')
        self.assertNotIn('tap_app_box',body['questions'])

    def test_does_not_fall_back_to_anthropic(self):
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'unused'},clear=True), \
                patch('server.urllib.request.urlopen') as api:
            with self.assertRaisesRegex(ValueError,'TYPESAFE_API_KEY'):
                server.decide(self.task,self.frame,{'type':'open_app'})
            api.assert_not_called()
        with patch.dict('os.environ',{'VISION_PROVIDER':'typo'},clear=True):
            with self.assertRaisesRegex(ValueError,'VISION_PROVIDER'):
                server.decide(self.task,self.frame,None)

    def test_jev_decides_even_the_first_settings_action(self):
        task={'text':'打开设置'}
        _,options=jev.candidates(task,None)
        reply=self.response('search_app',options)
        with patch.dict('os.environ',{'TYPESAFE_API_KEY':'test'},clear=True), \
                patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())) as api:
            self.assertEqual(server.decide(self.prepared(task),self.frame,None),{'type':'open_app','text':'settings'})
            api.assert_called_once()

    def test_network_and_invalid_json_stop_without_private_logs(self):
        for error in (http.client.RemoteDisconnected('private'),http.client.IncompleteRead(b'private'),
                      ConnectionResetError('private'),TimeoutError('private')):
            output=io.StringIO()
            with self.subTest(error=type(error).__name__), patch.dict('os.environ',{'TYPESAFE_API_KEY':'test'},clear=True), \
                    patch('server.urllib.request.urlopen',side_effect=error), contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(ValueError,'TypeSafe'): server.decide(self.prepared(self.task),self.frame,None)
                self.assertNotIn('private',output.getvalue())
        with patch.dict('os.environ',{'TYPESAFE_API_KEY':'test'},clear=True), \
                patch('server.urllib.request.urlopen',return_value=io.BytesIO(b'private invalid JSON')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(ValueError,'返回格式'): server.decide(self.prepared(self.task),self.frame,None)
            self.assertNotIn('private',output.getvalue())

    def test_uncertainty_and_missing_target_never_become_clicks(self):
        for field,value in (('confidence',0.4),('presence',0.2),('box_confidence',0.3),('probability',0.6)):
            result=self.response(); answers=result['answers']
            if field == 'confidence': answers['next_action']['confidence']=value
            if field == 'presence': answers['tap_app_valid']['noul']=value
            if field == 'box_confidence': answers['tap_app_box']['confidence']=value
            if field == 'probability': answers['next_action']['probabilities'].update(tap_app=value,assist=1-value)
            with self.subTest(field=field):
                self.assertEqual(jev.parse_answer(result,self.options,self.frame)['type'],'assist')

    def test_app_icon_observed_at_095_and_chosen_at_100_can_reach_confirmation(self):
        # Reproduce the field failure: a clear app icon and unanimous Choice were
        # stopped by a duplicate Noul score of 0.87. Coordinates below are synthetic.
        # The new intent score is a synthetic contract fixture, absent from that recorded run.
        task={'text':'打开微信','home_swipe':True}
        _,options=jev.candidates(task,None)
        result=self.response('tap_app',options)
        result['answers']['next_action']['confidence']=1.0
        result['answers']['tap_app_valid']['noul']=0.87
        evidence=self.observations(options)
        for name,item in evidence.items():
            item.update(visible='yes' if name == 'tap_app' else 'no',confidence=0.95 if name == 'tap_app' else 0.05)
        diagnostic={}
        action=jev.grounded_answer(result,options,self.frame,evidence,diagnostic)
        self.assertEqual(action['type'],'tap')
        self.assertEqual(diagnostic['action_validity_threshold'],0.8)
        state=server.State(); state.add_frame(screenshot()); state.begin('打开微信')
        pending=state.propose(action,state.current_frame())
        self.assertTrue(state.check_pending(pending['id']))
        state.add_frame(screenshot((100,100,150,150)))
        self.assertFalse(state.check_pending(pending['id']))

    def test_navigation_threshold_does_not_relax_input_send_or_visual_evidence(self):
        _,navigation=jev.candidates({'text':'打开微信','home_swipe':True},None)
        for name in ('tap_app','search_app','swipe_left','swipe_right'):
            for score,allowed in ((0.79,False),(0.8,True)):
                result=self.response(name,navigation); result['answers'][name+'_valid']['noul']=score
                with self.subTest(name=name,score=score):
                    action=jev.grounded_answer(result,navigation,self.frame,self.observations(navigation),{})
                    self.assertEqual(action['type'] != 'assist',allowed)
        for text,name in (('输入hello','type_text'),('输入hello','focus'),('给张三发送hello','send')):
            _,options=jev.candidates({'text':text},None)
            result=self.response(name,options); result['answers'][name+'_valid']['noul']=0.87
            with self.subTest(text=text,name=name):
                self.assertEqual(jev.grounded_answer(result,options,self.frame,self.observations(options),{})['type'],'assist')
        evidence=self.observations(); evidence['tap_app']['confidence']=0.89
        result=self.response(); result['answers']['tap_app_valid']['noul']=0.87
        self.assertEqual(jev.grounded_answer(result,self.options,self.frame,evidence,{})['type'],'assist')

    def test_tap_app_request_separates_original_intent_from_current_icon(self):
        task={'text':'不要打开微信','command':'打开微信'}
        context,options=jev.candidates(task,None)
        body=jev.request_body(context,options,self.frame,self.observations(options))
        self.assertEqual(body['state']['user_request'],'不要打开微信')
        self.assertEqual(body['state']['app'],'微信')
        requested=body['questions']['tap_app_requested']
        visual=body['questions']['tap_app_valid']
        self.assertEqual(requested['type'],'noul')
        self.assertEqual(visual['type'],'noul')
        self.assertIn('user_request',requested['instructions'])
        self.assertNotIn('observations',requested['instructions'])
        self.assertNotIn('user_request',json.dumps(visual))
        self.assertIn('observations.tap_app',visual['instructions'])
        self.assertEqual(set(requested['criteria']),{'true','false'})
        for kind in ('negation','quoted','cancellation','additional goals or actions'):
            self.assertIn(kind,requested['criteria']['false'].lower())
        self.assertEqual(body['questions']['next_action']['type'],'choice')
        self.assertEqual(body['questions']['next_action']['criteria'],
                         {name:option['condition'] for name,option in options.items()})
        self.assertIn('user_request',body['questions']['done_requested']['instructions'])
        self.assertNotIn('done_valid',body['questions'])

    def test_app_completion_separates_request_from_foreground_evidence(self):
        task={'text':'不要打开微信','command':'打开微信'}
        for previous in (None,{'type':'tap','label':'微信'}):
            context,options=jev.candidates(task,previous)
            with self.subTest(previous=previous):
                self.assertTrue(options['done']['app_completion'])
                self.assertNotIn('app_completion',options['done']['action'])
                body=jev.request_body(context,options,self.frame,self.observations(options))
                requested=body['questions']['done_requested']
                self.assertEqual(requested['type'],'noul')
                self.assertNotIn('done_valid',body['questions'])
                self.assertEqual(body['state']['user_request'],task['text'])
                self.assertIn('user_request',requested['instructions'])
                self.assertNotIn('observations',requested['instructions'])
                for word in ('negation','quoted','cancellation','additional goals or actions'):
                    self.assertIn(word,requested['criteria']['false'].lower())
                for misleading in ('Home screen','Spotlight','App Store'):
                    self.assertIn(misleading,options['done']['condition'])

    def test_app_completion_requires_intent_choice_and_direct_visual_evidence(self):
        _,options=jev.candidates(self.task,{'type':'tap','label':'微信'})
        # The recorded duplicate 0.55 score is not independent visual evidence.
        for intent,gate in ((.89,'intent_validity'),(.9,'passed'),(.99,'passed')):
            response=self.response('done',options)
            response['answers']['done_requested']['noul']=intent
            self.assertNotIn('done_valid',response['answers'])
            diagnostic={}
            with self.subTest(intent=intent):
                action=jev.grounded_answer(response,options,self.frame,self.observations(options),diagnostic)
                self.assertEqual(action['type'],'done' if gate=='passed' else 'assist')
                self.assertEqual(diagnostic['gate'],gate)
                self.assertEqual(diagnostic['intent_validity'],{'done':intent})
                self.assertEqual(diagnostic['intent_validity_threshold'],.9)
        for field,value,gate in (('confidence',.79,'choice_confidence'),('probability',.89,'choice_probability')):
            response=self.response('done',options)
            if field == 'confidence': response['answers']['next_action']['confidence']=value
            else: response['answers']['next_action']['probabilities'].update(done=value,assist=1-value)
            diagnostic={}
            with self.subTest(field=field):
                self.assertEqual(jev.grounded_answer(response,options,self.frame,self.observations(options),diagnostic)['type'],'assist')
                self.assertEqual(diagnostic['gate'],gate)
        # Extra unrequested fields must not become additional decision gates.
        response=self.response('done',options)
        response['answers']['done_valid']={'type':'noul','noul':.55}
        self.assertEqual(jev.grounded_answer(response,options,self.frame,self.observations(options),{})['type'],'done')

    def test_app_completion_requires_unambiguous_original_request_and_visual_evidence(self):
        for text in ('不要打开微信','他说“打开微信”','取消打开微信',
                     '打开微信并输入hello','打开微信和相机'):
            task={'text':text,'command':'打开微信'}
            context,options=jev.candidates(task,{'type':'tap','label':'微信'})
            response=self.response('done',options)
            response['answers']['done_requested']['noul']=.1
            with self.subTest(text=text):
                self.assertEqual(jev.request_body(context,options,self.frame,self.observations(options))['state']['user_request'],text)
                diagnostic={}
                self.assertEqual(jev.grounded_answer(response,options,self.frame,self.observations(options),diagnostic)['type'],'assist')
                self.assertEqual(diagnostic['gate'],'intent_validity')
        _,options=jev.candidates(self.task,{'type':'tap','label':'微信'})
        response=self.response('done',options)
        for visible,confidence in (('no',.99),('unclear',.99),('yes',.89)):
            evidence=self.observations(options); evidence['done'].update(visible=visible,confidence=confidence)
            diagnostic={}
            with self.subTest(visible=visible,confidence=confidence):
                self.assertEqual(jev.grounded_answer(response,options,self.frame,evidence,diagnostic)['type'],'assist')
                self.assertEqual(diagnostic['gate'],'perception_validity')

    def test_app_completion_parse_answer_alone_cannot_bypass_visual_evidence(self):
        _,options=jev.candidates(self.task,{'type':'tap','label':'微信'})
        response=self.response('done',options)
        def assert_rejected(observations=None):
            try:
                action=jev.parse_answer(response,options,self.frame,observations=observations)
            except ValueError:
                return
            self.assertNotEqual(action['type'],'done')
        assert_rejected()
        for observations in ({},{'done':{}},{'done':{'visible':'yes','confidence':'0.99'}}):
            with self.subTest(observations=observations):
                assert_rejected(observations)
        for visible,confidence in (('no',.99),('unclear',.99),('yes',.89)):
            with self.subTest(visible=visible,confidence=confidence):
                evidence=self.observations(options); evidence['done'].update(visible=visible,confidence=confidence)
                assert_rejected(evidence)
        self.assertEqual(jev.parse_answer(response,options,self.frame,observations=self.observations(options))['type'],'done')

    def test_app_completion_rejects_malformed_intent_noul(self):
        _,options=jev.candidates(self.task,{'type':'tap','label':'微信'})
        for field in ('done_requested',):
            invalid=[None,{}, {'type':'choice','noul':1}, {'type':'noul'}]
            invalid += [{'type':'noul','noul':value} for value in
                        (True,False,'0.9',float('nan'),float('inf'),-.1,1.1)]
            for item in invalid:
                response=self.response('done',options)
                response['answers'][field]=item
                diagnostic={}
                with self.subTest(field=field,item=item),self.assertRaises(ValueError):
                    jev.parse_answer(response,options,self.frame,diagnostic)
                self.assertEqual(diagnostic['gate'],'invalid_response')
            response=self.response('done',options); del response['answers'][field]
            with self.subTest(missing=field),self.assertRaises(ValueError):
                jev.parse_answer(response,options,self.frame)

    def test_input_and_send_completion_keep_combined_condition(self):
        for text,previous in (('输入hello',{'type':'type'}),('给张三发送hello',{'type':'send'})):
            context,options=jev.candidates({'text':text,'history':[previous]},previous)
            with self.subTest(text=text):
                self.assertFalse(options['done'].get('app_completion',False))
                body=jev.request_body(context,options,self.frame,self.observations(options))
                self.assertNotIn('done_requested',body['questions'])
                self.assertIn('user_request',body['questions']['done_valid']['instructions'])
                self.assertIn('observations.done',body['questions']['done_valid']['instructions'])
                response=self.response('done',options)
                self.assertNotIn('done_requested',response['answers'])
                for score,expected in ((.89,'assist'),(.9,'done')):
                    response['answers']['done_valid']['noul']=score
                    self.assertEqual(jev.grounded_answer(response,options,self.frame,self.observations(options),{})['type'],expected)
                del response['answers']['done_valid']
                with self.assertRaises(ValueError):
                    jev.grounded_answer(response,options,self.frame,self.observations(options),{})

    def test_tap_app_requires_both_unchanged_probability_thresholds(self):
        for intent,visual,gate in ((.89,.99,'intent_validity'),(.9,.8,'passed'),
                                   (.99,.78,'action_validity'),(.99,.79,'action_validity')):
            result=self.response(); result['answers']['tap_app_requested']['noul']=intent
            result['answers']['tap_app_valid']['noul']=visual
            diagnostic={}
            with self.subTest(intent=intent,visual=visual):
                action=jev.grounded_answer(result,self.options,self.frame,self.observations(),diagnostic)
                self.assertEqual(action['type'],'tap' if gate == 'passed' else 'assist')
                self.assertEqual(diagnostic['gate'],gate)
                self.assertEqual(diagnostic['intent_validity'],{'tap_app':intent})
                self.assertEqual(diagnostic['intent_validity_threshold'],.9)
                self.assertEqual(diagnostic['action_validity']['tap_app'],visual)
                self.assertEqual(diagnostic['action_validity_threshold'],.8)

    def test_invalid_original_request_cannot_use_visual_yes(self):
        for text in ('不要打开微信','他说“打开微信”','取消打开微信',
                     '打开微信并输入hello','打开微信和相机'):
            task={'text':text,'command':'打开微信'}
            context,options=jev.candidates(task,None)
            result=self.response('tap_app',options)
            result['answers']['tap_app_requested']['noul']=.1
            with self.subTest(text=text):
                body=jev.request_body(context,options,self.frame,self.observations(options))
                self.assertEqual(body['state']['user_request'],text)
                diagnostic={}
                action=jev.grounded_answer(result,options,self.frame,self.observations(options),diagnostic)
                self.assertEqual(action['type'],'assist')
                self.assertEqual(diagnostic['gate'],'intent_validity')

    def test_tap_app_rejects_either_malformed_noul_before_thresholding(self):
        for field in ('tap_app_requested','tap_app_valid'):
            invalid=[None,{}, {'type':'choice','noul':1}, {'type':'noul'}]
            invalid += [{'type':'noul','noul':value} for value in
                        (True,False,'0.9',float('nan'),float('inf'),-.1,1.1)]
            for item in invalid:
                result=self.response()
                other='tap_app_valid' if field == 'tap_app_requested' else 'tap_app_requested'
                result['answers'][other]['noul']=.1
                result['answers'][field]=item
                diagnostic={}
                with self.subTest(field=field,item=item),self.assertRaises(ValueError):
                    jev.parse_answer(result,self.options,self.frame,diagnostic)
                self.assertEqual(diagnostic['gate'],'invalid_response')
            result=self.response(); del result['answers'][field]
            with self.subTest(missing=field),self.assertRaises(ValueError):
                jev.parse_answer(result,self.options,self.frame)

    def test_diagnostics_distinguish_stop_gates_without_private_data(self):
        for gate in ('passed','model_assist','choice_confidence','choice_probability','action_validity','box_confidence'):
            result=self.response('assist' if gate == 'model_assist' else 'tap_app')
            answers=result['answers']
            result['private']='private response body'
            answers['private_valid']={'type':'noul','noul':1}
            if gate == 'choice_confidence': answers['next_action']['confidence']=0.2
            if gate == 'choice_probability': answers['next_action']['probabilities'].update(tap_app=0.8,assist=0.2)
            if gate == 'action_validity': answers['tap_app_valid']['noul']=0.1
            if gate == 'box_confidence': answers['tap_app_box']['confidence']=0.1
            diagnostic={}
            with self.subTest(gate=gate):
                action=jev.parse_answer(result,self.options,self.frame,diagnostic)
                self.assertEqual(diagnostic['gate'],gate)
                self.assertEqual(action['type'],'tap' if gate == 'passed' else 'assist')
                self.assertNotIn('private',json.dumps(diagnostic))
                self.assertNotIn(self.frame['image'],json.dumps(diagnostic))
        bad=self.response(); bad['answers']['next_action']['choice']='private unknown choice'
        diagnostic={}
        with self.assertRaises(ValueError): jev.parse_answer(bad,self.options,self.frame,diagnostic)
        self.assertEqual(diagnostic,{'gate':'invalid_response'})

    def test_diagnostics_survive_stop_but_do_not_leak_into_new_task(self):
        old=server.state; server.state=server.State()
        try:
            server.state.add_frame(screenshot()); task=server.state.begin('打开微信')
            _,options=jev.candidates(task,None)
            body=json.dumps(self.response('assist',options)).encode()
            with patch.dict('os.environ',{'TYPESAFE_API_KEY':'test','VISION_PROVIDER':'jev'}), \
                    patch('server.urllib.request.urlopen',return_value=io.BytesIO(body)):
                action=server.decide(self.prepared(task),server.state.current_frame(),None)
            self.assertEqual(action['type'],'assist')
            self.assertEqual(server.state.vision_diagnostic['gate'],'model_assist')
            self.assertGreaterEqual(server.state.vision_diagnostic['duration_ms'],0)
            server.state.finish('目标不明确')
            self.assertEqual(server.state.vision_diagnostic['gate'],'model_assist')
            task=server.state.begin('打开微信')
            self.assertIsNone(server.state.vision_diagnostic)
            def late_response(*args,**kwargs):
                server.state.finish(); server.state.begin('打开相机')
                return io.BytesIO(body)
            with patch.dict('os.environ',{'TYPESAFE_API_KEY':'test','VISION_PROVIDER':'jev'}), \
                    patch('server.urllib.request.urlopen',side_effect=late_response):
                server.decide(self.prepared(task),server.state.current_frame(),None)
            self.assertIsNone(server.state.vision_diagnostic)
        finally:
            server.state=old

    def test_malformed_choices_presence_and_boxes_fail_closed(self):
        mutations = [lambda a:a['next_action'].update(choice='send'),
            lambda a:a['next_action'].update(choice=[]),
            lambda a:a['next_action'].update(type='text'),
            lambda a:a['next_action'].update(confidence=True),
            lambda a:a['next_action']['probabilities'].update(tap_app=float('nan')),
            lambda a:a['next_action']['probabilities'].update(assist=0.5),
            lambda a:a['next_action']['probabilities'].pop('assist'),
            lambda a:a.pop('tap_app_valid'),
            lambda a:a['tap_app_valid'].update(type='box'),
            lambda a:a['tap_app_valid'].update(noul=float('inf')),
            lambda a:a['tap_app_box'].update(type='noul'),
            lambda a:a['tap_app_box']['box'].update(left=0.3),
            lambda a:a['tap_app_box']['box'].update(bottom=0.1),
            lambda a:a['tap_app_box']['box'].update(top=-0.1),
            lambda a:a['tap_app_box']['box'].update(right=1.1),
            lambda a:a['tap_app_box']['box'].update(left=True)]
        for mutate in mutations:
            result=self.response(); mutate(result['answers'])
            with self.subTest(result=result), self.assertRaises(ValueError):
                jev.parse_answer(result,self.options,self.frame)
        for result in (None,[],{'answers':[]},{'answers':{'next_action':None}}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                jev.parse_answer(result,self.options,self.frame)

    def test_pixel_size_does_not_change_point_coordinates_or_allow_edge_overflow(self):
        larger=dict(self.frame,width=780,height=1568)
        action=jev.parse_answer(self.response(),self.options,larger)
        clean=server.validate_action(action,larger)
        self.assertEqual((clean['x'],clean['y']),(59,211))
        result=self.response()
        result['answers']['tap_app_box']['box'].update(left=0.999,right=1)
        with self.assertRaisesRegex(ValueError,'坐标'):
            server.validate_action(jev.parse_answer(result,self.options,self.frame),self.frame)

    def test_local_commands_are_bounded_and_unknown_commands_do_not_call_api(self):
        for text in ('打开设置并关闭蓝牙','不要打开设置','打开银行','付款十元','删除照片','打开https://example.com',
                     '输入a@example.com','输入'+'a'*81,'给张三发送'+'你'*15):
            with self.subTest(text=text), patch.dict('os.environ',{},clear=True), patch('server.urllib.request.urlopen') as api:
                self.assertEqual(server.decide({'text':text},self.frame,None)['type'],'assist')
                api.assert_not_called()
        for app,query in jev.APPS.items():
            _,options=jev.candidates({'text':'打开'+app},None)
            self.assertEqual(options['search_app']['action']['text'],query)

    def test_app_open_is_verified_after_search_and_click(self):
        self.assertNotIn('search_app',self.options)
        self.assertIn('NOT completion',self.options['done']['condition'])
        _,options=jev.candidates(self.task,{'type':'tap','label':'微信'})
        self.assertEqual(set(options),{'done','assist'})
        response=self.response('done',options)
        response['answers']['done_requested']['noul']=0.1
        self.assertEqual(jev.parse_answer(response,options,self.frame)['type'],'assist')

    def test_input_and_send_never_repeat_after_history_advances(self):
        for command in ('输入hello','给张三发送hello'):
            task={'text':command,'history':[]}
            _,options=jev.candidates(task,None)
            self.assertIn('focus',options); self.assertIn('type_text',options)
            task['history'].append({'type':'tap','label':'输入框'})
            _,options=jev.candidates(task,task['history'][-1])
            self.assertNotIn('focus',options); self.assertIn('type_text',options)
            task['history'].append({'type':'type'})
            _,options=jev.candidates(task,task['history'][-1])
            self.assertNotIn('type_text',options)
            self.assertEqual(set(options),{'send' if command.startswith('给') else 'done','assist'})
        task['history'].append({'type':'send'})
        _,options=jev.candidates(task,task['history'][-1])
        self.assertEqual(set(options),{'done','assist'})

    def test_chinese_requires_unambiguous_pinyin_and_exact_candidate(self):
        task={'text':'输入你好','history':[]}
        _,options=jev.candidates(task,None)
        action=options['type_text']['action']
        self.assertEqual(action,{'type':'type_chinese','target':'你好','pinyin':'nihao'})
        server.validate_action(action,self.frame,task['text'])
        task['history'].append({'type':'type_chinese'})
        _,options=jev.candidates(task,task['history'][-1])
        self.assertEqual(set(options),{'candidate','done','assist'})
        task['history'].append({'type':'tap','label':'中文候选'})
        _,options=jev.candidates(task,task['history'][-1])
        self.assertEqual(set(options),{'done','assist'})
        with patch('jev.pinyin',return_value=[['xing','hang']]):
            self.assertIsNone(jev.input_action('行'))

    def test_send_details_are_local_and_require_visible_draft_gate(self):
        task={'text':'给张三发送hello'}
        context,options=jev.candidates(task,None)
        response=self.response('send',options)
        response['action']={'type':'send','recipient':'李四','text':'tampered'}
        action=jev.parse_answer(response,options,self.frame)
        clean=server.validate_action(action,self.frame,task['text'])
        self.assertEqual((clean['recipient'],clean['text']),('张三','hello'))
        response['answers']['send_valid']['noul']=0.5
        self.assertEqual(jev.parse_answer(response,options,self.frame)['type'],'assist')
        self.assertEqual(context['recipient'],'张三')

    def test_geometry_and_image_limits(self):
        for changes in ({'orientation':'landscape'},{'point_width':393},{'width':279},{'height':1569}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                jev.request_body(self.context,self.options,dict(self.frame,**changes),self.observations())

    def test_execution_history_and_existing_frame_confirmation_guards(self):
        state=server.State(); state.add_frame(screenshot()); state.begin('打开微信')
        frame=state.current_frame()
        action=jev.parse_answer(self.response(),self.options,frame)
        pending=state.propose(action,frame)
        self.assertTrue(state.check_pending(pending['id']))
        state.add_frame(screenshot((90,90,130,130)))
        self.assertFalse(state.check_pending(pending['id']))
        state.result(pending['id'],False)
        self.assertIsNone(state.task)
        state.begin('输入hello')
        pending=state.propose({'type':'type','text':'hello'},state.current_frame())
        state.result(pending['id'],True)
        self.assertEqual(state.task['history'],[{'type':'type'}])
        self.assertIsNone(state.current_frame(after=state.last_action['frame']))

    def test_claude_only_interprets_text_and_jev_still_controls_action(self):
        intent={'type':'open_app','app':'微信','text':'','recipient':'','compound':False}
        reply={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(intent)}]}
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'},clear=True), \
                patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())) as api:
            command=server.understand_intent('能帮忙把微信打开吗')
        self.assertEqual(command,'打开微信')
        payload=json.loads(api.call_args.args[0].data)
        self.assertEqual(payload['messages'],[{'role':'user','content':'能帮忙把微信打开吗'}])
        self.assertNotIn('image',json.dumps(payload))
        self.assertNotIn('tap',payload['output_config']['format']['schema']['properties']['type']['enum'])
        task={'text':'能帮忙把微信打开吗','command':command}
        context,options=jev.candidates(task,None)
        response=self.response('tap_app',options)
        response['answers']['tap_app_valid']['noul']=0.2
        self.assertEqual(jev.parse_answer(response,options,self.frame)['type'],'assist')
        self.assertEqual(context['user_request'],task['text'])

    def test_claude_interpretation_cannot_invent_text_or_drop_multiple_goals(self):
        cases=[('帮我把微信打开',{'type':'open_app','app':'相机','text':'','recipient':'','compound':False}),
               ('打开微信并输入hello',{'type':'open_app','app':'微信','text':'','recipient':'','compound':True}),
               ('帮我打上hello',{'type':'type','app':'','text':'world','recipient':'','compound':False}),
               ('把hello发给张三',{'type':'send','app':'','text':'hello','recipient':'李四','compound':False}),
               ('帮我打上hello!',{'type':'type','app':'','text':'hello!','recipient':'','compound':False})]
        for text,intent in cases:
            reply={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(intent)}]}
            with self.subTest(text=text),patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'},clear=True), \
                    patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())):
                self.assertIsNone(server.understand_intent(text))
        _,options=jev.candidates({'text':'输入hello!'},None)
        self.assertFalse(options)

    def test_text_interpretation_and_vision_use_separate_polls(self):
        import threading
        import urllib.request
        old=server.state; server.state=server.State()
        server.state.add_frame(screenshot()); server.state.begin('把微信打开好吗')
        token=server.state.pair('device',server.state.code)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=http.serve_forever,daemon=True); worker.start()
        request=urllib.request.Request('http://127.0.0.1:%d/next' % http.server_port,
                                       headers={'Authorization':'Bearer '+token})
        try:
            with patch('server.understand_intent',return_value='打开微信') as interpreter, \
                    patch('server.perceive',return_value=self.prepared(self.task)['perception']) as reader, \
                    patch('server.decide',return_value={'type':'assist','label':'target_unclear'}) as vision:
                with urllib.request.urlopen(request,timeout=3) as reply: first=json.load(reply)
                self.assertIsNone(first['action']); interpreter.assert_called_once(); vision.assert_not_called()
                self.assertEqual(server.state.task['command'],'打开微信')
                self.assertEqual(first['transcript'],'把微信打开好吗')
                self.assertEqual(first['command'],'打开微信')
                with urllib.request.urlopen(request,timeout=3) as reply: second=json.load(reply)
                self.assertIsNone(second['action']); reader.assert_called_once(); vision.assert_not_called()
                with urllib.request.urlopen(request,timeout=3) as reply: third=json.load(reply)
                self.assertIsNone(third['action']); self.assertIn('目标不明确',third['error'])
                self.assertEqual(third['transcript'],'把微信打开好吗')
                self.assertEqual(third['command'],'打开微信')
                interpreter.assert_called_once(); vision.assert_called_once()
            server.state.begin('听错的句子')
            with patch('server.understand_intent',return_value=None):
                with urllib.request.urlopen(request,timeout=3) as reply: failed=json.load(reply)
            self.assertEqual(failed['transcript'],'听错的句子')
            self.assertEqual(failed['command'],'未能解析')
            self.assertIn('指令不明确',failed['error'])
            server.state.begin('把微信打开好吗')
            def canceled_interpretation(text):
                server.state.finish(); server.state.begin('打开相机')
                return '打开微信'
            with patch('server.understand_intent',side_effect=canceled_interpretation):
                with urllib.request.urlopen(request,timeout=3) as reply: canceled=json.load(reply)
            self.assertNotIn('transcript',canceled)
            self.assertEqual(server.state.task['text'],'打开相机')
            self.assertNotIn('command',server.state.task)
        finally:
            http.shutdown(); http.server_close(); worker.join(); server.state=old

    def test_speech_feedback_preserves_heard_text_and_separates_interpretation(self):
        self.assertEqual(jev.feedback({'text':'请打开微信吧'}),{'transcript':'请打开微信吧','command':'打开微信'})
        self.assertEqual(jev.feedback({'text':'输入Hello'}),{'transcript':'输入Hello','command':'输入Hello'})
        self.assertEqual(jev.feedback({'text':'给张三发送hello'})['command'],'给张三发送hello')
        self.assertEqual(jev.feedback({'text':'听不懂的句子'})['command'],'等待解析')
        self.assertEqual(jev.feedback({'text':'把微信打开好吗','command':'打开微信'})['transcript'],'把微信打开好吗')
        self.assertEqual(jev.feedback({'text':'啊'*200})['transcript'],'啊'*160+'…')

    def test_screen_reader_only_reports_evidence_and_jev_cannot_invent_coordinates(self):
        _,options=jev.candidates(self.task,None)
        observations=self.observations(options)
        reply={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(observations)}]}
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test'},clear=True), \
                patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())) as api:
            evidence=server.perceive(self.task,self.frame,None)
        payload=json.loads(api.call_args.args[0].data)
        self.assertEqual(payload['messages'][0]['content'][0]['source']['data'],self.frame['image'])
        prompt=payload['system']
        self.assertNotIn('Confidence is confidence that the complete condition is true',prompt)
        self.assertIn('classification',prompt)
        self.assertIn('yes/no/unclear',prompt)
        self.assertIn('A high-confidence no means the condition is clearly not satisfied',prompt)
        schema=payload['output_config']['format']['schema']
        self.assertEqual(set(schema['properties']),set(options)-{'assist'})
        self.assertNotIn('action',json.dumps(schema))
        response=self.response('tap_app',options)
        response['answers']['tap_app_box']['box']={'left':0.8,'right':0.9,'top':0.8,'bottom':0.9}
        diagnostic={}
        action=jev.grounded_answer(response,options,self.frame,evidence['observations'],diagnostic)
        self.assertAlmostEqual(action['x'],58.5)
        self.assertEqual(action['y'],211)
        self.assertEqual(action['scope'],'app_icon')
        _,input_options=jev.candidates({'text':'输入hello'},None)
        injected=self.response('focus',input_options)
        injected['scope']='app_icon'
        injected['answers']['next_action']['scope']='app_icon'
        focus=jev.grounded_answer(injected,input_options,self.frame,self.observations(input_options),{})
        self.assertEqual(focus['type'],'tap')
        self.assertNotIn('scope',focus)
        for visible,confidence in (('no',0.99),('unclear',0.99),('yes',0.89)):
            observations['tap_app'].update(visible=visible,confidence=confidence)
            with self.subTest(visible=visible,confidence=confidence):
                self.assertEqual(jev.grounded_answer(response,options,self.frame,observations,diagnostic)['type'],'assist')
                self.assertEqual(diagnostic['gate'],'perception_validity')

    def test_invalid_screen_evidence_fails_closed(self):
        for changes in ({'confidence':float('nan')},{'confidence':True},{'left':-1},
                        {'right':0},{'visible':'private untrusted text'}):
            evidence=self.observations(); evidence['tap_app'].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError,'画面识别'):
                jev.validate_observations(evidence,self.options)
        with self.assertRaises(ValueError): jev.validate_observations({},self.options)

    def test_home_swipes_require_capability_and_are_bounded(self):
        task={'text':'打开微信','history':[]}
        _,options=jev.candidates(task,None)
        self.assertNotIn('swipe_left',options)
        task['home_swipe']=True
        _,options=jev.candidates(task,None)
        for name in ('swipe_left','swipe_right'):
            self.assertIn('HOME SCREEN',options[name]['condition'])
            self.assertIn('not Spotlight',options[name]['condition'])
        self.assertEqual(jev.MAX_HOME_SWIPES,6)
        state=server.State(); state.add_frame(screenshot()); state.begin('打开微信')
        with self.assertRaisesRegex(ValueError,'固件'):
            state.propose({'type':'swipe','direction':'left'},state.current_frame())
        state.finish(); state.pair('device',state.code,['home_swipe_v1','native_touch_v1','private unknown capability'])
        self.assertEqual(state.device_capabilities,['home_swipe_v1','native_touch_v1'])
        # Synthetic map: this test exercises navigation bounds, not calibration.
        state.pointer_calibration={'gain':[1,1],'offset':[0,0], 'verified':True,
            'geometry':[390,844,'portrait'],'algorithm':server.calibration.ALGORITHM}
        state.begin('打开微信')
        state.task['perception']={'frame':state.current_frame(),'observations':{}}
        action=state.propose({'type':'swipe','direction':'right'},state.current_frame())
        state.add_frame(screenshot((100,100,150,150)))
        self.assertFalse(state.check_pending(action['id']))
        with self.assertRaisesRegex(ValueError,'校准定位尚未检查'): state.result(action['id'],True)
        state.result(action['id'],False)
        self.assertIsNone(state.task)
        for direction in ('up','down','',None):
            with self.assertRaises(ValueError): server.validate_action({'type':'swipe','direction':direction},self.frame)

    def test_home_swipe_candidates_cover_six_attempts_without_reversing_direction(self):
        for direction in ('left','right'):
            for count in (0,1,5,6):
                with self.subTest(direction=direction,count=count):
                    history=[{'type':'swipe','direction':direction} for _ in range(count)]
                    task={'text':'打开微信','home_swipe':True,'history':history}
                    _,options=jev.candidates(task,history[-1] if history else None)
                    expected=({'swipe_left','swipe_right'} if count==0 else
                              {'swipe_'+direction} if count<6 else set())
                    self.assertEqual(set(options)&{'swipe_left','swipe_right'},expected)
                    self.assertIn('tap_app',options)
                    self.assertEqual(options['search_app']['action'],{'type':'open_app','text':'wechat'})

    def test_search_fallback_exists_after_any_swipe_when_no_safe_direction_remains(self):
        for direction in ('left','right'):
            for count in (1,5,6):
                with self.subTest(direction=direction,count=count):
                    history=[{'type':'swipe','direction':direction} for _ in range(count)]
                    task={'text':'打开微信','home_swipe':True,'history':history}
                    _,options=jev.candidates(task,history[-1])
                    self.assertIn('search_app',options)
                    evidence=self.observations(options)
                    for name,item in evidence.items():
                        item.update(visible='yes' if name=='search_app' else 'no',confidence=.99)
                    action=jev.grounded_answer(self.response('search_app',options),options,self.frame,evidence,{})
                    self.assertEqual(action,{'type':'open_app','text':'wechat'})

    def home_navigation_case(self, count=0, direction='left', missing=('no',.9), safe=()):
        history=[{'type':'swipe','direction':direction} for _ in range(count)]
        previous=history[-1] if history else None
        task={'text':'打开微信','home_swipe':True,'history':history}
        _,options=jev.candidates(task,previous)
        evidence=self.observations(options)
        for item in evidence.values(): item.update(visible='no',confidence=.99)
        evidence['tap_app'].update(visible=missing[0],confidence=missing[1])
        evidence['search_app'].update(visible='yes',confidence=.99)
        for name,confidence in safe:
            if name in evidence: evidence[name].update(visible='yes',confidence=confidence)
        return dict(task,perception={'frame':self.frame,'observations':evidence}),previous,options

    def mock_navigation_decision(self, task, previous, response):
        with patch.dict('os.environ',{'TYPESAFE_API_KEY':'test'},clear=True), \
                patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())) as api:
            action=server.decide_jev(task,self.frame,previous)
        api.assert_called_once()
        return action,json.loads(api.call_args.args[0].data)

    def test_safe_home_swipe_removes_search_request_and_preserves_jev_direction_choice(self):
        for count,direction in ((0,'left'),(0,'right'),(1,'right'),(5,'left')):
            with self.subTest(count=count,direction=direction):
                task,previous,options=self.home_navigation_case(count,direction,
                    safe=(('swipe_left',.9),('swipe_right',.9)))
                filtered={name:item for name,item in options.items() if name!='search_app'}
                action,body=self.mock_navigation_decision(task,previous,self.response('swipe_'+direction,filtered))
                self.assertEqual(action,{'type':'swipe','direction':direction})
                self.assertEqual(set(body['questions']['next_action']['criteria']),set(filtered))
                self.assertNotIn('search_app_valid',body['questions'])
                self.assertEqual(set(body['state']['observations']),set(filtered)-{'assist'})
                self.assertIn('search_app',task['perception']['observations']) # Source evidence is not mutated.

    def test_no_safe_swipe_or_exhausted_budget_keeps_search_fallback_in_request(self):
        for count in (0,1,5,6):
            with self.subTest(count=count):
                # Even a requested safe direction cannot survive the six-attempt
                # candidate budget: there is no seventh swipe for filtering to find.
                safe=(('swipe_left',.99),) if count==6 else ()
                task,previous,options=self.home_navigation_case(count,safe=safe)
                action,body=self.mock_navigation_decision(task,previous,self.response('search_app',options))
                self.assertEqual(action,{'type':'open_app','text':'wechat'})
                self.assertIn('search_app',body['questions']['next_action']['criteria'])
                self.assertIn('search_app_valid',body['questions'])
                self.assertIn('search_app',body['state']['observations'])
                if count==6:
                    self.assertFalse(any(name.startswith('swipe_') for name in body['state']['observations']))

    def test_uncertain_absence_or_swipe_does_not_remove_search(self):
        cases=((('no',.89),.99),(('unclear',.99),.99),(('yes',.99),.99),(('no',.99),.89))
        for missing,swipe_confidence in cases:
            with self.subTest(missing=missing,swipe_confidence=swipe_confidence):
                task,previous,options=self.home_navigation_case(missing=missing,
                    safe=(('swipe_left',swipe_confidence),))
                action,body=self.mock_navigation_decision(task,previous,self.response('assist',options))
                self.assertEqual(action['type'],'assist')
                self.assertEqual(set(body['questions']['next_action']['criteria']),set(options))
                self.assertIn('search_app',body['state']['observations'])

    def test_swipe_preference_does_not_synthesize_action_or_accept_unfiltered_response(self):
        task,previous,options=self.home_navigation_case(safe=(('swipe_left',.99),))
        filtered={name:item for name,item in options.items() if name!='search_app'}
        action,_=self.mock_navigation_decision(task,previous,self.response('assist',filtered))
        self.assertEqual(action['type'],'assist')
        # A response carrying the removed search option is stale/invalid, even
        # when its selected swipe would otherwise be safe.
        for selected in ('swipe_left','search_app'):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.mock_navigation_decision(task,previous,self.response(selected,options))

    def test_hybrid_http_flow_keeps_screen_evidence_bound_to_original_frame(self):
        import threading
        import urllib.request
        old=server.state; server.state=server.State()
        server.state.add_frame(screenshot()); server.state.begin('打开微信')
        token=server.state.pair('device',server.state.code,['native_touch_v1'])
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=http.serve_forever,daemon=True); worker.start()
        request=urllib.request.Request('http://127.0.0.1:%d/next' % http.server_port,
                                       headers={'Authorization':'Bearer '+token})
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _,options=jev.candidates({'text':'打开微信'},None)
        evidence={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(self.observations(options))}]}
        decision=self.response('tap_app',options)
        try:
            with patch.dict('os.environ',{'VISION_PROVIDER':'jev','ANTHROPIC_API_KEY':'test','TYPESAFE_API_KEY':'test'}), \
                    patch('server.urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(evidence).encode()),
                          io.BytesIO(json.dumps(decision).encode())]) as api:
                with client.open(request,timeout=3) as reply: self.assertIsNone(json.load(reply)['action'])
                self.assertIn('perception',server.state.task)
                original=server.state.task['perception']['frame']['sequence']
                server.state.add_frame(screenshot((100,100,150,150)))
                self.assertLess(original,server.state.sequence)
                with client.open(request,timeout=3) as reply: rejected=json.load(reply)
                self.assertIsNone(rejected['action']); self.assertIn('画面内容已变化',rejected['error'])
                self.assertEqual(api.call_args_list[0].args[0].full_url,'https://api.anthropic.com/v1/messages')
                self.assertEqual(api.call_args_list[1].args[0].full_url,jev.ENDPOINT)
                self.assertIsNone(server.state.task)
            server.state.begin('打开微信')
            def canceled_perception(*args,**kwargs):
                server.state.finish(); server.state.begin('打开相机')
                return io.BytesIO(json.dumps(evidence).encode())
            with patch.dict('os.environ',{'VISION_PROVIDER':'jev','ANTHROPIC_API_KEY':'test'}), \
                    patch('server.urllib.request.urlopen',side_effect=canceled_perception):
                with client.open(request,timeout=3) as reply: self.assertIsNone(json.load(reply)['action'])
            self.assertEqual(server.state.task['text'],'打开相机')
            self.assertNotIn('perception',server.state.task)
            self.assertIsNone(server.state.perception_diagnostic)
        finally:
            http.shutdown(); http.server_close(); worker.join(); server.state=old


class AppIconRecheckTests(unittest.TestCase):
    def setUp(self):
        self.frame = dict(screenshot(), image=screenshot()['jpeg'])
        self.action = dict(type='tap', scope='app_icon', label='微信', x=242, y=106)
        self.observation = dict(visible='yes', confidence=.95, left=.5, right=.7, top=.08, bottom=.18)

    def test_request_contains_only_selected_icon_and_one_noul(self):
        action = dict(self.action, text='private user request', recipient='private recipient')
        body = jev.app_icon_recheck_body(action, self.frame, self.observation)
        self.assertEqual(set(body), {'model', 'state', 'questions'})
        self.assertEqual(body['model'], 'jev-1.14.0')
        self.assertEqual(set(body['state']), {'requested_app', 'screen_condition', 'observation'})
        self.assertEqual(body['state']['requested_app'], '微信')
        self.assertEqual(body['state']['observation'], self.observation)
        _,options = jev.candidates({'text':'打开微信'}, {'type':'open_app'})
        self.assertEqual(body['state']['screen_condition'],
                         options['tap_app']['condition'].replace('`app`', '`requested_app`'))
        self.assertEqual(set(body['questions']), {'icon_available'})
        question = body['questions']['icon_available']
        self.assertEqual(question['type'], 'noul')
        self.assertEqual(set(question['criteria']), {'true', 'false'})
        self.assertTrue(all(question['criteria'].values()))
        encoded = json.dumps(body)
        for excluded in ('private user request', 'private recipient', 'user_request',
                         'next_action', 'search_app', 'swipe_left', 'choice', self.frame['image']):
            self.assertNotIn(excluded, encoded)
        self.assertEqual(action, dict(self.action, text='private user request', recipient='private recipient'))
        body['state']['observation']['confidence'] = 0
        self.assertEqual(self.observation['confidence'], .95)

    def test_invalid_actions_cannot_request_icon_recheck(self):
        invalid = [None, [], {}, dict(self.action, type='send'), dict(self.action, scope=None),
                   dict(self.action, label='unknown'), dict(self.action, label=[]),
                   dict(self.action, label=True)]
        for action in invalid:
            with self.subTest(action=action), self.assertRaises(ValueError):
                jev.app_icon_recheck_body(action, self.frame, self.observation)

    def test_recheck_keeps_frame_geometry_limits(self):
        for changes in ({'orientation':'landscape'}, {'point_width':844}, {'width':279}, {'height':1569}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                jev.app_icon_recheck_body(self.action, dict(self.frame, **changes), self.observation)

    def test_recheck_validates_the_single_screen_observation(self):
        invalid = [None, {}, dict(self.observation, confidence=True),
                   dict(self.observation, confidence=float('nan')), dict(self.observation, right=.4),
                   dict(self.observation, visible='maybe')]
        for observation in invalid:
            with self.subTest(observation=observation), self.assertRaises(ValueError):
                jev.app_icon_recheck_body(self.action, self.frame, observation)

    def test_noul_threshold_has_no_choice_confidence_requirement(self):
        for score, allowed in ((0, False), (.79, False), (.8, True), (1, True)):
            diagnostic = {}
            result = {'answers':{'icon_available':{'type':'noul', 'noul':score}}}
            with self.subTest(score=score):
                self.assertIs(jev.parse_app_icon_recheck(result, diagnostic), allowed)
                self.assertEqual(diagnostic, dict(gate='passed' if allowed else 'action_validity',
                    action_validity={'icon_available':score}, action_validity_threshold=.8))

    def test_missing_or_invalid_noul_is_rejected(self):
        invalid = [None, [], {}, {'answers':None}, {'answers':{}},
                   {'answers':{'icon_available':None}}, {'answers':{'icon_available':{}}},
                   {'answers':{'icon_available':{'type':'choice', 'noul':1}}},
                   {'answers':{'icon_available':{'type':'noul'}}}]
        invalid += [{'answers':{'icon_available':{'type':'noul', 'noul':value}}}
                    for value in (True, False, None, '0.9', [], float('nan'), float('inf'), -0.1, 1.01, 10**1000)]
        for result in invalid:
            diagnostic = {}
            with self.subTest(result=result), self.assertRaises(ValueError):
                jev.parse_app_icon_recheck(result, diagnostic)
            self.assertEqual(diagnostic['gate'], 'invalid_response')
            self.assertNotIn('action_validity', diagnostic)


if __name__ == '__main__': unittest.main()
