import base64
import contextlib
import io
import http.client as http_client
import json
import threading
import urllib.request
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
import server

def screenshot(rectangle=None, comment=b'', fill='black', quality=90, size=(390,844)):
    image=Image.new('RGB',(390,844),'white')
    if rectangle: ImageDraw.Draw(image).rectangle(rectangle,fill=fill)
    if image.size != size: image=image.resize(size)
    data=io.BytesIO(); image.save(data,format='JPEG',quality=quality)
    jpeg=data.getvalue()
    if comment: jpeg=jpeg[:2]+b'\xff\xfe'+(len(comment)+2).to_bytes(2,'big')+comment+jpeg[2:]
    return {'jpeg':base64.b64encode(jpeg).decode(),'width':size[0],'height':size[1],
            'point_width':390,'point_height':844,'orientation':'portrait'}

def pixel_screenshot(points=(), channel=0, delta=0, size=(390,844)):
    image=Image.new('RGB',size,(16,16,16))
    color=[16,16,16]; color[channel]+=delta
    for point in points: image.putpixel(point,tuple(color))
    output=io.BytesIO(); image.save(output,format='JPEG',quality=100,subsampling=0,keep_rgb=True)
    return {'jpeg':base64.b64encode(output.getvalue()).decode(),'width':size[0],'height':size[1],
            'point_width':390,'point_height':844,'orientation':'portrait'}

class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = server.State()
        self.payload = screenshot()
    def test_pair_per_role_and_restart(self):
        token = self.state.pair('phone', self.state.code)
        self.assertTrue(self.state.auth('phone', token))
        with self.assertRaises(ValueError): self.state.pair('phone', self.state.code)
        self.assertFalse(server.State().auth('phone', token))
        with self.assertRaises(ValueError): self.state.pair('device', 'wrong')
    def test_phone_can_pair_again_after_stopping_capture(self):
        old_phone = self.state.pair('phone', self.state.code)
        device = self.state.pair('device', self.state.code)
        self.state.add_frame(self.payload)
        self.state.begin('打开设置')
        self.state.stop_capture()
        self.assertIsNone(self.state.current_frame())
        self.assertIsNone(self.state.task)
        self.assertIn('共享已停止', self.state.task_error)
        with self.assertRaises(ValueError): self.state.add_frame(self.payload)
        with self.assertRaises(ValueError): self.state.pair('phone', 'wrong')
        new_phone = self.state.pair('phone', self.state.code)
        self.assertFalse(self.state.auth('phone', old_phone))
        self.assertTrue(self.state.auth('phone', new_phone))
        self.assertTrue(self.state.auth('device', device))
        self.state.add_frame(self.payload)
        with self.assertRaises(ValueError): self.state.pair('phone', self.state.code)
    def test_phone_can_repair_stale_session_without_stop_notification(self):
        with patch('server.time.monotonic',return_value=100):
            old_phone=self.state.pair('phone',self.state.code)
            device=self.state.pair('device',self.state.code)
            self.state.add_frame(self.payload)
            self.state.begin('打开设置')
            action=self.state.propose({'type':'open_app','text':'settings'},self.state.current_frame())
        with patch('server.time.monotonic',return_value=116):
            with self.assertRaises(ValueError): self.state.pair('phone','wrong')
            self.assertTrue(self.state.auth('phone',old_phone))
            new_phone=self.state.pair('phone',self.state.code)
            self.assertFalse(self.state.auth('phone',old_phone))
            self.assertTrue(self.state.auth('phone',new_phone))
            self.assertTrue(self.state.auth('device',device))
            self.assertIsNone(self.state.frame)
            self.assertIsNone(self.state.task)
            self.assertIsNone(self.state.pending)
            with self.assertRaises(ValueError): self.state.result(action['id'],True)
            with self.assertRaises(ValueError): self.state.pair('phone',self.state.code)
            self.state.add_frame(self.payload)
            self.assertIsNotNone(self.state.current_frame())
    def test_phone_repair_waits_for_initial_picker_and_live_frames(self):
        with patch('server.time.monotonic',return_value=100):
            old_phone=self.state.pair('phone',self.state.code)
        with patch('server.time.monotonic',return_value=110):
            with self.assertRaises(ValueError): self.state.pair('phone',self.state.code)
        with patch('server.time.monotonic',return_value=116):
            new_phone=self.state.pair('phone',self.state.code)
            self.assertFalse(self.state.auth('phone',old_phone))
        with patch('server.time.monotonic',return_value=150):
            self.state.add_frame(self.payload)
        with patch('server.time.monotonic',return_value=151):
            with self.assertRaises(ValueError): self.state.pair('phone',self.state.code)
            self.assertTrue(self.state.auth('phone',new_phone))
    def test_disconnected_device_can_repair_without_replacing_phone(self):
        with patch('server.time.monotonic',return_value=100):
            phone=self.state.pair('phone',self.state.code)
            old_device=self.state.pair('device',self.state.code)
            self.state.add_frame(self.payload); self.state.begin('打开设置')
            action=self.state.propose({'type':'open_app','text':'settings'},self.state.current_frame())
        with patch('server.time.monotonic',return_value=130):
            self.assertTrue(self.state.auth('device',old_device))
            with self.assertRaises(ValueError): self.state.pair('device',self.state.code)
        with patch('server.time.monotonic',return_value=200):
            self.state.add_frame(self.payload)
            with self.assertRaises(ValueError): self.state.pair('device','wrong')
            new_device=self.state.pair('device',self.state.code)
            self.assertFalse(self.state.auth('device',old_device))
            self.assertTrue(self.state.auth('device',new_device))
            self.assertTrue(self.state.auth('phone',phone))
            self.assertIsNotNone(self.state.current_frame())
            self.assertIsNone(self.state.task)
            with self.assertRaises(ValueError): self.state.result(action['id'],True)
    def test_one_action_and_result(self):
        self.state.add_frame(self.payload)
        self.state.begin('打开设置')
        frame = self.state.current_frame()
        action = self.state.propose({'type':'tap','x':100,'y':200,'label':'搜索'},frame)
        with self.assertRaises(ValueError): self.state.propose({'type':'tap','x':1,'y':2,'label':'搜索'},frame)
        self.state.result(action['id'], True)
        with self.assertRaises(ValueError): self.state.result(action['id'], True)
        self.assertIsNone(self.state.current_frame(after=frame['sequence']))
    def test_next_step_waits_for_frame_received_after_execution(self):
        self.state.add_frame(self.payload); self.state.begin('打开微信')
        action=self.state.propose({'type':'open_app','text':'WeChat'},self.state.current_frame())
        # Spotlight uploads intermediate search results while HID is still typing.
        self.state.add_frame(self.payload)
        self.state.add_frame(self.payload)
        sequence_at_completion=self.state.sequence
        self.state.result(action['id'],True)
        self.assertIsNone(self.state.current_frame(after=self.state.last_action['frame']))
        self.state.add_frame(self.payload)
        next_frame=self.state.current_frame(after=self.state.last_action['frame'])
        self.assertGreater(next_frame['sequence'],sequence_at_completion)
    def test_last_execution_keeps_numeric_coordinates_after_final_decision(self):
        self.state.add_frame(screenshot(size=(384,832))); self.state.begin('打开微信')
        action=self.state.propose({'type':'tap','x':100,'y':200,'label':'微信','scope':'app_icon'},self.state.current_frame())
        self.state.result(action['id'],True)
        evidence=dict(self.state.last_execution)
        self.assertEqual(evidence,{'type':'tap','x':100,'y':200,'ok':True,
                                  'image_size':[384,832],'point_size':[390,844]})
        self.state.add_frame(screenshot(size=(384,832)))
        done=self.state.propose({'type':'done'},self.state.current_frame())
        self.state.result(done['id'],True)
        self.assertEqual(self.state.last_execution,evidence)
        self.state.begin('给某人发送private')
        self.assertIsNone(self.state.last_execution)
        send=self.state.propose({'type':'send','x':300,'y':700,'recipient':'某人','text':'private'},self.state.current_frame())
        self.state.result(send['id'],False)
        self.assertFalse(self.state.last_execution['ok'])
        self.assertNotIn('private',json.dumps(self.state.last_execution))
        self.assertNotIn('recipient',self.state.last_execution)
    def test_changed_frame_blocks_action(self):
        self.state.add_frame(self.payload); self.state.begin('tap')
        frame = self.state.current_frame()
        self.payload=screenshot((10,100,30,130))
        self.state.add_frame(self.payload)
        with self.assertRaisesRegex(ValueError,'画面内容已变化') as rejected:
            self.state.propose({'type':'tap','x':1,'y':2,'label':'搜索'},frame)
        self.state.finish(str(rejected.exception))
        self.assertEqual(self.state.last_decision, {'type':'tap','status':'frame_changed'})
        self.assertIn('画面内容已变化',self.state.task_error)
        self.assertIsNone(self.state.task)
        self.state.begin('重试')
        self.assertIsNone(self.state.last_decision)
        self.assertIsNone(self.state.task_error)
    def test_jpeg_metadata_change_is_not_a_screen_change(self):
        self.state.add_frame(screenshot(comment=b'first')); self.state.begin('点击搜索')
        original=self.state.current_frame()
        self.state.add_frame(screenshot(comment=b'second'))
        action=self.state.propose({'type':'tap','x':100,'y':150,'label':'搜索'},original)
        self.assertEqual(action['type'],'tap')
        self.assertEqual(self.state.frame_difference['changed_pixels'],0)
    def test_identical_jpeg_must_be_decodable_and_match_declared_size(self):
        action={'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'}
        valid=screenshot(); valid=dict(valid,image=valid['jpeg'])
        same, detail=server.compare_frame_pixels(valid,dict(valid),action)
        self.assertTrue(same)
        self.assertEqual(detail['changed_pixels'],0)

        cases=[dict(valid,image=base64.b64encode(b'\xff\xd8not-a-valid-jpeg').decode()),
               dict(valid,width=389),
               dict(valid,image=valid['image'][:-4])]
        image=Image.new('RGB',(1000,2001),'white')
        output=io.BytesIO(); image.save(output,format='JPEG')
        cases.append(dict(valid,image=base64.b64encode(output.getvalue()).decode(),width=1000,height=2001))
        output=io.BytesIO(); Image.new('RGB',(390,844),'white').save(output,format='PNG')
        cases.append(dict(valid,image=base64.b64encode(output.getvalue()).decode()))
        for invalid in cases:
            with self.subTest(width=invalid['width'],height=invalid['height'],length=len(invalid['image'])):
                same, detail=server.compare_frame_pixels(invalid,dict(invalid),action)
                self.assertFalse(same)
                self.assertEqual(detail.get('error'),'invalid_image')
    def test_status_bar_change_allows_body_tap_and_confirmation(self):
        self.state.add_frame(screenshot()); self.state.begin('点击微信')
        original=self.state.current_frame()
        self.state.add_frame(screenshot((10,10,30,25)))
        action=self.state.propose({'type':'tap','x':100,'y':150,'label':'微信'},original)
        self.state.add_frame(screenshot((50,10,70,25)))
        self.assertTrue(self.state.check_pending(action['id']))
        self.assertTrue(self.state.frame_difference['ignored_status_bar'])
        self.assertGreater(self.state.frame_difference['changed_pixels'],0)
        self.state.frame['orientation']='landscape'
        self.assertFalse(self.state.check_pending(action['id']))
    def test_small_body_change_still_blocks_tap(self):
        self.state.add_frame(screenshot()); self.state.begin('点击微信')
        original=self.state.current_frame()
        self.state.add_frame(screenshot((100,120,103,135)))
        with self.assertRaisesRegex(ValueError,'画面内容已变化'):
            self.state.propose({'type':'tap','x':300,'y':700,'label':'微信'},original)
        self.assertIsNotNone(self.state.frame_difference['body_bbox_px'])
    def test_app_navigation_ignores_faint_refresh_outside_target(self):
        # Field diagnostic: strong changes only in the status bar, <=8 RGB levels
        # in the bottom area. Reproduce that pattern without storing real frames.
        image=Image.new('RGB',(390,844),'white'); draw=ImageDraw.Draw(image)
        draw.rectangle((32,8,105,39),fill=(201,201,201))
        draw.rectangle((0,720,383,831),fill=(252,252,252))
        for action in ({'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'},
                       {'type':'swipe','direction':'left'}, {'type':'swipe','direction':'right'}):
            for size in ((390,844),(384,832)):
                with self.subTest(action=action,size=size):
                    output=io.BytesIO(); image.resize(size).save(output,format='JPEG',quality=90)
                    refresh=dict(screenshot(size=size),jpeg=base64.b64encode(output.getvalue()).decode())
                    state=server.State(); state.pair('device',state.code,['home_swipe_v1','native_touch_v1'])
                    state.add_frame(screenshot(size=size)); state.begin('打开微信')
                    original=state.current_frame(); state.add_frame(refresh)
                    state.pointer_calibration={'gain':[1,1],'offset':[0,0], 'verified':True,
                        'geometry':[390,844,'portrait'],'algorithm':server.calibration.ALGORITHM}
                    pending=state.propose(action,original)
                    self.assertEqual(state.frame_difference['pixel_tolerance'],12 if action['type']=='tap' else 8)
                    self.assertLessEqual(state.frame_difference['body_max_channel_delta'],8)
                    self.assertEqual(state.frame_difference['protected_region_changed_pixels'],0)
                    state.pointer_step(pending['id'],{})
                    state.pointer_step(pending['id'],{'seq':1,'ok':True})
                    state.add_frame(screenshot((0,720,383,831),fill=(253,253,253),size=size))
                    self.assertTrue(state.pointer_step(pending['id'],{'poll':True})['ready'])
                    self.assertTrue(state.check_pending(pending['id']))
                    state.add_frame(screenshot((300,600,303,615),size=size))
                    self.assertFalse(state.check_pending(pending['id']))
    def test_navigation_still_blocks_strong_app_changes_and_faint_drag_path_changes(self):
        for action,rectangle,fill in (
            ({'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'},(300,600,303,615),'black'),
            ({'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'},(100,150,103,163),(220,220,220)),
            ({'type':'swipe','direction':'left'},(195,699,198,703),(252,252,252))):
            with self.subTest(action=action,rectangle=rectangle):
                state=server.State(); state.pair('device',state.code,['home_swipe_v1','native_touch_v1'])
                state.add_frame(screenshot()); state.begin('打开微信'); original=state.current_frame()
                state.add_frame(screenshot(rectangle,fill=fill))
                with self.assertRaisesRegex(ValueError,'画面内容已变化'): state.propose(action,original)
        with self.assertRaises(ValueError):
            server.validate_action({'type':'tap','x':100,'y':150,'label':'输入框','scope':'app_icon'},original)
    def test_app_icon_faint_refresh_boundary_in_each_channel_and_region(self):
        # Aligned RGB JPEG blocks preserve exact decoded channel differences.
        # Exercise the real decoder, including the resized capture geometry.
        action={'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'}
        for size in ((390,844),(384,832)):
            for xy in ((96,144),(296,600)):
                for channel in range(3):
                    for delta in (9,12,13):
                        with self.subTest(size=size,xy=xy,channel=channel,delta=delta):
                            frames=[]
                            for change in (False,True):
                                image=Image.new('RGB',size,(16,16,16))
                                if change:
                                    color=[16,16,16]; color[channel]+=delta
                                    ImageDraw.Draw(image).rectangle((*xy,xy[0]+7,xy[1]+7),fill=tuple(color))
                                output=io.BytesIO(); image.save(output,format='JPEG',quality=100,subsampling=0,keep_rgb=True)
                                frames.append({'image':base64.b64encode(output.getvalue()).decode(),
                                    'width':size[0],'height':size[1],'point_width':390,'point_height':844,
                                    'orientation':'portrait'})
                            same,detail=server.compare_frame_pixels(*frames,action)
                            self.assertEqual(detail['body_max_channel_delta'],delta)
                            self.assertEqual(same,delta<=12)
                            inside=xy==(96,144)
                            self.assertEqual(detail['protected_region_changed_pixels'],64 if inside else 0)
                            self.assertEqual(detail['protected_region_pixels_over_tolerance'],64 if inside and delta>12 else 0)
                            self.assertEqual(detail['protected_region_max_channel_delta'],delta if inside else 0)
                            self.assertEqual(detail['protected_region_pixel_tolerance'],12)
    def test_app_icon_faint_target_refresh_allows_move_ready_and_final_check(self):
        state=server.State(); state.pair('device',state.code,['native_touch_v1'])
        state.add_frame(screenshot()); state.begin('打开微信')
        original=state.current_frame()
        state.add_frame(screenshot((90,140,110,160),fill=(246,246,246),quality=100))
        state.pointer_calibration={'gain':[1,1],'offset':[0,0], 'verified':True,
            'geometry':[390,844,'portrait'],'algorithm':server.calibration.ALGORITHM}
        pending=state.propose({'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'},original)
        self.assertGreater(state.frame_difference['protected_region_changed_pixels'],0)
        state.pointer_step(pending['id'],{})
        state.pointer_step(pending['id'],{'seq':1,'ok':True})
        state.add_frame(screenshot((90,140,110,160),fill=(245,245,245),quality=100))
        with patch('server.perceive',side_effect=AssertionError('faint refresh must not request recheck')):
            self.assertTrue(state.pointer_step(pending['id'],{'poll':True})['ready'])
        self.assertTrue(state.check_pending(pending['id']))
        state.add_frame(screenshot((90,140,110,160),fill=(220,220,220),quality=100))
        self.assertFalse(state.check_pending(pending['id']))
    def test_app_icon_small_refresh_exception_boundaries_and_other_actions(self):
        app={'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'}
        outside=(296,600); inside=(100,150)
        outside_points=tuple((296+8*n,600) for n in range(5))
        for size in ((390,844),(384,832)):
            for channel in range(3):
                reference=pixel_screenshot(size=size)
                reference=dict(reference,image=reference['jpeg'])
                for points,delta,decoded,over,allowed in (
                    ((outside,),13,14,1,True), ((outside,),16,16,1,True),
                    ((outside,),17,18,1,False),
                    (outside_points[:2],13,14,2,True),
                    (outside_points[:4],13,14,4,True),
                    (outside_points[:4],16,16,4,True),
                    (outside_points,13,14,5,False),
                    ((inside,),13,14,1,False),
                    ((inside,outside),13,14,2,False)):
                    with self.subTest(size=size,channel=channel,points=points,delta=delta):
                        current=pixel_screenshot(points,channel,delta,size)
                        current=dict(current,image=current['jpeg'])
                        same,detail=server.compare_frame_pixels(reference,current,app)
                        # These values come from decoded JPEGs, not encoded input colors.
                        self.assertEqual(detail['body_max_channel_delta'],decoded)
                        self.assertEqual(detail['body_pixels_over_tolerance'],over)
                        self.assertEqual(detail['protected_region_pixels_over_tolerance'],int(inside in points))
                        self.assertEqual(detail['small_refresh_pixel_budget'],4)
                        self.assertEqual(detail['small_refresh_max_delta'],16)
                        self.assertEqual(detail['small_refresh_exception'],allowed)
                        self.assertEqual(same,allowed)
                current=pixel_screenshot((outside,),channel,13,size)
                current=dict(current,image=current['jpeg'])
                for action in ({'type':'swipe','direction':'left'},
                               {'type':'tap','x':100,'y':150,'label':'微信'},
                               {'type':'send','x':300,'y':700,'recipient':'小明','text':'hello'}):
                    with self.subTest(size=size,channel=channel,action=action):
                        same,detail=server.compare_frame_pixels(reference,current,action)
                        self.assertEqual(detail['body_max_channel_delta'],14)
                        self.assertFalse(detail['small_refresh_exception'])
                        self.assertEqual(detail['small_refresh_pixel_budget'],0)
                        self.assertIsNone(detail['small_refresh_max_delta'])
                        self.assertFalse(same)
    def test_app_icon_small_refresh_exception_is_checked_at_each_stage(self):
        state=server.State(); state.pair('device',state.code,['native_touch_v1'])
        state.add_frame(pixel_screenshot()); state.begin('打开微信')
        original=state.current_frame()
        initial_points=tuple((256+8*n,600) for n in range(4))
        state.add_frame(pixel_screenshot(initial_points,0,13))
        state.pointer_calibration={'gain':[1,1],'offset':[0,0], 'verified':True,
            'geometry':[390,844,'portrait'],'algorithm':server.calibration.ALGORITHM}
        pending=state.propose({'type':'tap','x':100,'y':150,'label':'微信','scope':'app_icon'},original)
        self.assertTrue(state.frame_difference['small_refresh_exception'])
        self.assertEqual(state.frame_difference['body_pixels_over_tolerance'],4)
        state.pointer_step(pending['id'],{})
        state.pointer_step(pending['id'],{'seq':1,'ok':True})
        state.add_frame(pixel_screenshot())
        with patch('server.perceive',side_effect=AssertionError('small refresh must not request recheck')):
            self.assertTrue(state.pointer_step(pending['id'],{'poll':True})['ready'])
        self.assertTrue(state.frame_difference['small_refresh_exception'])
        self.assertEqual(state.frame_difference['body_pixels_over_tolerance'],4)
        self.assertTrue(state.check_pending(pending['id']))
        self.assertTrue(state.frame_difference['small_refresh_exception'])
        new_points=tuple((296+8*n,600) for n in range(5))
        state.add_frame(pixel_screenshot(initial_points+new_points,0,13))
        self.assertFalse(state.check_pending(pending['id']))
        self.assertEqual(state.frame_difference['body_pixels_over_tolerance'],5)
    def test_faint_refresh_does_not_bypass_other_action_checks(self):
        for action in ({'type':'send','x':300,'y':700,'recipient':'小明','text':'你好'},
                       {'type':'tap','x':100,'y':150,'label':'微信'}, {'type':'type','text':'hello'},
                       {'type':'key','key':'tab'}, {'type':'type_chinese','target':'你好','pinyin':'nihao'},
                       {'type':'done'}):
            with self.subTest(action=action):
                state=server.State(); state.add_frame(screenshot()); state.begin('输入你好')
                original=state.current_frame(); state.add_frame(screenshot((0,720,383,831),fill=(252,252,252)))
                with self.assertRaisesRegex(ValueError,'画面内容已变化'): state.propose(action,original)
    def test_status_bar_chroma_bleed_does_not_block_body_tap(self):
        self.state.add_frame(screenshot(quality=65)); self.state.begin('点击微信')
        original=self.state.current_frame()
        self.state.add_frame(screenshot((32,8,103,38),fill=(255,40,20),quality=65))
        action=self.state.propose({'type':'tap','x':100,'y':150,'label':'微信'},original)
        self.assertTrue(self.state.check_pending(action['id']))
        self.assertIsNone(self.state.frame_difference.get('body_bbox_px'))
    def test_assist_is_a_safe_stop_even_when_screen_changes(self):
        self.state.add_frame(self.payload); self.state.begin('打开微信')
        original=self.state.current_frame()
        self.state.add_frame(screenshot())
        action=self.state.propose({'type':'assist','label':'target_not_visible'},original)
        self.assertEqual(action['reason'],'target_not_visible')
        self.assertIn('未找到目标',action['message'])
        self.assertEqual(self.state.steps,0)
    def test_status_bar_change_does_not_bypass_send_or_top_tap(self):
        for action in ({'type':'send','x':300,'y':700,'recipient':'小明','text':'你好'},
                       {'type':'tap','x':20,'y':20,'label':'返回'}, {'type':'type','text':'hello'}):
            self.state=server.State()
            self.state.add_frame(screenshot()); self.state.begin('test')
            original=self.state.current_frame()
            self.state.add_frame(screenshot((10,10,30,25)))
            with self.assertRaisesRegex(ValueError,'画面内容已变化'):
                self.state.propose(action,original)
    def test_confirmation_blocks_changed_body_and_stale_frame(self):
        self.state.add_frame(screenshot()); self.state.begin('点击微信')
        action=self.state.propose({'type':'tap','x':100,'y':150,'label':'微信'},self.state.current_frame())
        self.assertFalse(self.state.check_pending('wrong'))
        self.state.add_frame(screenshot((300,600,303,615)))
        self.assertFalse(self.state.check_pending(action['id']))
        self.state.add_frame(screenshot())
        self.state.frame['received']-=6
        self.assertFalse(self.state.check_pending(action['id']))
    def test_open_app_uses_fresh_frame_without_coordinate_context(self):
        self.state.add_frame(self.payload); self.state.begin('打开设置')
        original=self.state.current_frame()
        original['received']-=30
        self.payload=screenshot((10,10,30,25))
        self.state.add_frame(self.payload)
        action=self.state.propose({'type':'open_app','text':'settings'},original)
        self.assertEqual(action['frame'],self.state.sequence)
        self.assertEqual(action['type'],'open_app')
    def test_model_latency_does_not_expire_unchanged_live_capture(self):
        self.state.add_frame(self.payload); self.state.begin('点击搜索')
        original=self.state.current_frame()
        original['received']-=30
        self.state.add_frame(self.payload)
        action=self.state.propose({'type':'tap','x':100,'y':200,'label':'搜索'},original)
        self.assertEqual(action['frame'],self.state.sequence)
    def test_simple_open_command_survives_clock_change_and_requires_visual_followup(self):
        self.state.add_frame(self.payload); task=self.state.begin('打开设置。')
        original=self.state.current_frame()
        with patch.dict('os.environ',{'VISION_PROVIDER':'anthropic'}), patch('server.urllib.request.urlopen') as api:
            decision=server.decide(task,original,None)
            api.assert_not_called()
        self.payload=screenshot((10,10,30,25))
        self.state.add_frame(self.payload)
        action=self.state.propose(decision,original)
        self.assertEqual((action['type'],action['text']),('open_app','settings'))
        self.state.result(action['id'],True)
        self.assertIsNotNone(self.state.task)
        self.assertIsNone(self.state.current_frame(after=action['frame']))
        self.assertEqual(self.state.last_decision,{'type':'open_app','status':'executed'})
    def test_open_app_cannot_use_stale_capture(self):
        self.state.add_frame(self.payload); self.state.begin('打开设置')
        original=self.state.current_frame()
        self.state.frame['received']-=server.MAX_FRAME_AGE+1
        with self.assertRaisesRegex(ValueError,'画面'):
            self.state.propose({'type':'open_app','text':'settings'},original)
    def test_dangerous_and_invalid_actions_blocked(self):
        frame={'point_width':390,'point_height':844}
        for action in ({'type':'tap','x':float('nan'),'y':1}, {'type':'tap','x':400,'y':1},
                       {'type':'key','key':'return'}, {'type':'send'}, {'type':'type','text':'你好'},
                       {'type':'tap','x':1,'y':2,'label':'发送'}):
            with self.assertRaises(ValueError): server.validate_action(action,frame)
    def test_send_requires_complete_bounded_details(self):
        frame={'point_width':390,'point_height':844}
        valid={'type':'send','x':300,'y':700,'recipient':'小明','text':'你好'}
        self.assertEqual(server.validate_action(valid,frame)['recipient'],'小明')
        for changes in ({'recipient':''}, {'text':'超'*20}, {'text':'一\n二'}, {'x':390}):
            with self.assertRaises(ValueError): server.validate_action(dict(valid,**changes),frame)
    def test_chinese_pinyin_is_limited_to_requested_text(self):
        frame={'point_width':390,'point_height':844}
        action={'type':'type_chinese','target':'你好','pinyin':'nihao'}
        self.assertEqual(server.validate_action(action,frame,'输入你好')['text'],'nihao')
        with self.assertRaises(ValueError): server.validate_action(action,frame,'输入别的')
        with self.assertRaises(ValueError): server.validate_action(dict(action,pinyin='ni hao'),frame,'输入你好')
    def test_task_can_finish_at_step_limit(self):
        self.state.add_frame(self.payload); self.state.begin('test')
        self.assertEqual(server.MAX_STEPS,10)
        for index in range(10):
            action=self.state.propose({'type':'open_app','text':'settings'},self.state.current_frame())
            self.assertEqual(self.state.steps,index+1)
            self.state.result(action['id'],True)
            self.state.add_frame(self.payload)
        frame = self.state.current_frame()
        with self.assertRaises(ValueError): self.state.propose({'type':'tap','x':1,'y':2,'label':'搜索'},frame)
        self.assertEqual(self.state.propose({'type':'done'},frame)['type'],'done')
    def test_no_key(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError,'DASHSCOPE_API_KEY'): server.transcribe(b'RIFFtest')
    def test_task_cannot_start_without_current_capture(self):
        with self.assertRaisesRegex(ValueError,'共享'): self.state.begin('打开设置')
        self.state.add_frame(self.payload)
        self.state.stop_capture()
        with self.assertRaisesRegex(ValueError,'共享'): self.state.begin('打开设置')
class HTTPTests(unittest.TestCase):
    def test_pointer_http_requires_fresh_frame_and_check_before_result(self):
        from test_calibration import calibrate_state, capture_payload
        old=server.state; server.state=server.State()
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True); thread.start()
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def post(path,data,token=None):
            headers={'Authorization':'Bearer '+token} if token else {}
            request=urllib.request.Request('http://127.0.0.1:%d%s' % (http.server_port,path),json.dumps(data).encode(),headers)
            with client.open(request,timeout=5) as response: return json.load(response)
        def upload():
            post('/frame',capture_payload(),phone)
        try:
            phone=post('/pair',{'role':'phone','code':server.state.code})['token']
            device=post('/pair',{'role':'device','code':server.state.code,'capabilities':['native_touch_v1','hid_delivery_v1','pointer_grid7_v1']})['token']
            upload(); calibrate_state(server.state); server.state.begin('打开微信')
            action=server.state.propose({'type':'tap','x':260,'y':110,'label':'微信','scope':'app_icon'},server.state.current_frame())
            with self.assertRaises(urllib.error.HTTPError) as rejected: post('/pointer',{'id':action['id']},phone)
            self.assertEqual(rejected.exception.code,401); rejected.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as rejected: post('/result',{'id':action['id'],'ok':True},device)
            self.assertEqual(rejected.exception.code,400); rejected.exception.close()
            reply=post('/pointer',{'id':action['id']},device)
            self.assertFalse(reply['calibrating']); self.assertEqual(reply['move']['seq'],1)
            self.assertTrue(post('/pointer',{'id':action['id'],'seq':1,'ok':True},device)['wait'])
            self.assertTrue(post('/pointer',{'id':action['id'],'poll':True},device)['wait'])
            self.assertFalse(post('/check',{'id':action['id']},device)['ok'])
            upload()
            self.assertTrue(post('/pointer',{'id':action['id'],'poll':True},device)['ready'])
            self.assertTrue(post('/check',{'id':action['id']},device)['ok'])
            post('/result',{'id':action['id'],'ok':True},device)
            with self.assertRaises(urllib.error.HTTPError) as rejected: post('/result',{'id':action['id'],'ok':True},device)
            self.assertEqual(rejected.exception.code,400); rejected.exception.close()
            self.assertEqual(server.state.steps,1)
            self.assertTrue(server.state.pointer_diagnostic['calibration_verified'])
            self.assertFalse(server.state.pointer_diagnostic['landing_observed'])
            self.assertIsNone(server.state.current_frame(after=server.state.last_action['frame']))
        finally:
            http.shutdown(); http.server_close(); thread.join(); server.state=old

    def setUp(self):
        self.provider=patch.dict('os.environ',{'VISION_PROVIDER':'anthropic'})
        self.provider.start()
    def tearDown(self):
        self.provider.stop()
    def test_old_vision_response_cannot_control_repaired_phone(self):
        old=server.state
        server.state=server.State()
        phone=server.state.pair('phone',server.state.code)
        device=server.state.pair('device',server.state.code)
        frame=screenshot()
        server.state.add_frame(frame); server.state.begin('打开设置')
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=http.serve_forever,daemon=True); worker.start()
        entered=threading.Event(); release=threading.Event(); replies=[]
        def slow_decision(*args):
            entered.set(); release.wait(timeout=3)
            return {'type':'open_app','text':'settings'}
        def request():
            req=urllib.request.Request('http://127.0.0.1:%d/next' % http.server_port,
                                       headers={'Authorization':'Bearer '+device})
            with urllib.request.urlopen(req,timeout=4) as response: replies.append(json.load(response))
        client=threading.Thread(target=request,daemon=True)
        try:
            with patch('server.decide',side_effect=slow_decision):
                client.start(); self.assertTrue(entered.wait(timeout=1))
                with server.state.lock:
                    server.state.frame['received']-=16
                    server.state.phone_paired_at-=16
                    server.state.pair('phone',server.state.code)
                    server.state.add_frame(frame); new_task=server.state.begin('打开相机')
                release.set(); client.join(timeout=4)
            self.assertEqual(replies,[{'action':None}])
            self.assertEqual(server.state.task['id'],new_task['id'])
            self.assertIsNone(server.state.pending)
            self.assertFalse(server.state.auth('phone',phone))
        finally:
            release.set(); client.join(timeout=4)
            http.shutdown(); http.server_close(); worker.join(); server.state=old
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
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(root+'/status',timeout=3)
            self.assertEqual(rejected.exception.code,401)
            rejected.exception.close()
            phone=post('/pair', {'role':'phone','code':server.state.code})['token']
            capabilities=['home_swipe_v1','app_navigation_auto_v1','speech_feedback_v1','native_touch_v1']
            device=post('/pair', {'role':'device','code':server.state.code,'capabilities':capabilities+['unknown']})['token']
            jpeg=screenshot()['jpeg']
            post('/frame', {'jpeg':jpeg,'width':390,'height':844,'point_width':390,'point_height':844},phone)
            with patch('server.transcribe', return_value='打开设置'):
                recognized=post('/audio', b'RIFFtest', device)
            self.assertEqual(recognized['transcript'],'打开设置')
            self.assertEqual(recognized['command'],'打开设置')
            with patch('server.decide', return_value={'type':'open_app','text':'settings'}):
                req=urllib.request.Request(root+'/next', headers={'Authorization':'Bearer '+device})
                with urllib.request.urlopen(req, timeout=3) as response: action=json.load(response)['action']
            self.assertTrue(post('/check', {'id':action['id']}, device)['ok'])
            post('/result', {'id':action['id'],'ok':True}, device)
            self.assertFalse(post('/check', {'id':action['id']}, device)['ok'])
            post('/stop', {}, phone)
            self.assertIsNone(server.state.current_frame())
            with urllib.request.urlopen(req, timeout=3) as response: stopped=json.load(response)
            self.assertIn('共享已停止',stopped['error'])
            new_phone=post('/pair', {'role':'phone','code':server.state.code})['token']
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                post('/frame', {'jpeg':jpeg,'width':390,'height':844,'point_width':390,'point_height':844},phone)
            self.assertEqual(rejected.exception.code,401)
            rejected.exception.close()
            post('/frame', {'jpeg':jpeg,'width':390,'height':844,'point_width':390,'point_height':844},new_phone)
            self.assertTrue(server.state.auth('device',device))
            req=urllib.request.Request(root+'/status',headers={'Authorization':'Pair '+server.state.code})
            with urllib.request.urlopen(req,timeout=3) as response: status=json.load(response)
            self.assertEqual(status['paired'],['device','phone'])
            self.assertEqual(status['device_capabilities'],capabilities)
            self.assertGreaterEqual(status['device_age_ms'],0)
            self.assertEqual(status['frames'],2)
            self.assertGreaterEqual(status['frame_age_ms'],0)
            self.assertNotIn(new_phone,json.dumps(status))
            self.assertNotIn(jpeg,json.dumps(status))
            with patch('server.transcribe',return_value='输入private instruction'):
                post('/audio',b'RIFFtest',device)
            def changed_during_analysis(*args):
                post('/frame',screenshot((10,100,30,130)),new_phone)
                return {'type':'tap','x':100,'y':200,'label':'private target'}
            with patch('server.decide',side_effect=changed_during_analysis):
                next_req=urllib.request.Request(root+'/next',headers={'Authorization':'Bearer '+device})
                with urllib.request.urlopen(next_req,timeout=3) as response: rejected=json.load(response)
            self.assertIsNone(rejected['action'])
            self.assertIn('画面内容已变化',rejected['error'])
            with urllib.request.urlopen(req,timeout=3) as response: status=json.load(response)
            self.assertEqual(status['last_decision'],{'type':'tap','status':'frame_changed'})
            self.assertIsNone(status['last_execution'])
            self.assertFalse(status['task_active'])
            self.assertEqual(status['task_error'],rejected['error'])
            self.assertNotIn('private',json.dumps(status))
            self.assertNotIn(jpeg,json.dumps(status))
            post('/frame',screenshot(),new_phone)
            with patch('server.transcribe',return_value='打开微信'):
                post('/audio',b'RIFFtest',device)
            output=io.StringIO()
            with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','VISION_PROVIDER':'anthropic'}), contextlib.redirect_stdout(output), \
                    patch('server.urllib.request.urlopen',side_effect=http_client.RemoteDisconnected('private body')):
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(next_req,timeout=3) as response:
                    failure=json.load(response)
            self.assertIsNone(failure['action'])
            self.assertIn('连接中断',failure['error'])
            self.assertIsNone(server.state.task)
            self.assertIsNone(server.state.pending)
            self.assertNotIn('private',output.getvalue())
            with urllib.request.urlopen(req,timeout=3) as response: status=json.load(response)
            self.assertEqual(status['provider_error']['error_type'],'RemoteDisconnected')
            with patch('server.transcribe',return_value='打开微信'):
                post('/audio',b'RIFFtest',device)
            with patch('server.decide',return_value={'type':'assist','label':'target_not_visible'}):
                with urllib.request.urlopen(next_req,timeout=3) as response: help_reply=json.load(response)
            self.assertIsNone(help_reply['action'])
            self.assertIn('未找到目标',help_reply['error'])
            self.assertIsNone(server.state.task)
            self.assertIsNone(server.state.pending)
            self.assertEqual(server.state.last_decision,{'type':'assist','status':'needs_help','reason':'target_not_visible'})
        finally:
            http.shutdown(); http.server_close(); thread.join(); server.state=old

class SlowUploadHTTPTests(unittest.TestCase):
    def setUp(self):
        state_patch=patch.object(server,'state',server.State())
        state_patch.start(); self.addCleanup(state_patch.stop)
        clock_patch=patch('server.time.monotonic',return_value=100.0)
        self.clock=clock_patch.start(); self.addCleanup(clock_patch.stop)
        for name in ('decide','transcribe','perceive','understand_intent'):
            guard=patch.object(server,name,side_effect=AssertionError('No model calls in upload tests'))
            guard.start(); self.addCleanup(guard.stop)
        self.read_entered=threading.Event()
        self.read_finished=threading.Event()
        entered,finished=self.read_entered,self.read_finished
        class ObservedHandler(server.Handler):
            def body(self):
                if self.headers.get('X-Test-Slow') != 'yes': return super().body()
                entered.set()
                result=super().body()  # Real socket read; the client withholds the remaining bytes.
                finished.set()
                return result
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),ObservedHandler)
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.worker.start(); self.addCleanup(self.close_server)

    def close_server(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join(timeout=3)

    def setup_calibration(self):
        server.state=server.State()
        self.clock.return_value=100.0
        self.phone=server.state.pair('phone',server.state.code)
        self.capabilities=['native_touch_v1','hid_delivery_v1','pointer_grid7_v1']
        self.device=server.state.pair('device',server.state.code,self.capabilities)
        self.payload=screenshot()
        server.state.add_frame(self.payload)
        server.state.calibration_phone(dict(op='begin',page='e847c00d-65a5-42bd-9b9a-c2186c841f2c',
                                           geometry=[390,844,'portrait'],touch_window_ms=5000))
        session=server.state.calibration_session
        self.poll=dict(op='poll',id=session.id,page=session.page,geometry=session.geometry)
        count=30+sum(units//server.calibration.REPORT_UNITS for units in session.units)
        self.moved=dict(op='moved',id=session.id,probe=session.probe,ok=True,
                        usb=dict(planned=count,submitted=count,completed=count,error='none',
                                 duration_ms=(count-1)*10,min_gap_ms=10,max_gap_ms=10))
        return session

    def post(self,path,body,token=None):
        connection=http_client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=3)
        try:
            headers={'Authorization':'Bearer '+token} if token else {}
            connection.request('POST',path,json.dumps(body).encode(),headers)
            response=connection.getresponse()
            return response.status,json.load(response)
        finally: connection.close()

    def slow_post(self,path):
        role='device' if path == '/calibration/device' else 'phone'
        body={'/frame':self.payload,'/calibration':self.poll,'/calibration/device':self.moved}[path]
        payload=json.dumps(body).encode()
        self.read_entered.clear(); self.read_finished.clear()
        connection=http_client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=3)
        connection.putrequest('POST',path)
        connection.putheader('Authorization','Bearer '+getattr(self,role))
        connection.putheader('Content-Length',str(len(payload)))
        connection.putheader('X-Test-Slow','yes')
        connection.endheaders(); connection.send(payload[:1])
        self.assertTrue(self.read_entered.wait(timeout=1),'slow request did not enter body()')
        return connection,payload[1:]

    def background_post(self,path,body,token=None):
        done=threading.Event(); result=[]
        def request():
            try: result.append(self.post(path,body,token))
            except Exception as exc: result.append(exc)
            finally: done.set()
        thread=threading.Thread(target=request,daemon=True); thread.start()
        return thread,done,result

    def finish_slow(self,connection,remainder):
        try:
            connection.send(remainder)
            response=connection.getresponse()
            return response.status,json.load(response)
        finally: connection.close()

    def test_partial_upload_does_not_block_calibration_heartbeat(self):
        for path in ('/frame','/calibration','/calibration/device'):
            with self.subTest(path=path):
                session=self.setup_calibration()
                session.heartbeat=99.5
                slow,remainder=self.slow_post(path)
                thread,done,result=self.background_post('/calibration',self.poll,self.phone)
                try:
                    completed_while_upload_pending=done.wait(timeout=1)
                    self.assertFalse(self.read_finished.is_set())
                    heartbeat=session.heartbeat
                finally:
                    slow_reply=self.finish_slow(slow,remainder)
                    thread.join(timeout=3)
                self.assertTrue(completed_while_upload_pending,'slow body held the global state lock')
                self.assertEqual(result[0][0],200)
                self.assertEqual(heartbeat,100.0)
                self.assertEqual(slow_reply[0],200)
                self.assertTrue(session.active)

    def test_token_rotated_during_upload_is_rechecked_before_mutation(self):
        for path in ('/frame','/calibration','/calibration/device'):
            with self.subTest(path=path):
                session=self.setup_calibration()
                slow,remainder=self.slow_post(path)
                role='device' if path == '/calibration/device' else 'phone'
                self.clock.return_value=200.0  # Make a real /pair rotation eligible, without sleeping.
                thread,done,result=self.background_post('/pair',dict(role=role,code=server.state.code,
                                                                      capabilities=self.capabilities))
                try:
                    rotated_while_upload_pending=done.wait(timeout=1)
                    self.assertFalse(self.read_finished.is_set())
                    sequence=server.state.sequence
                finally:
                    slow_reply=self.finish_slow(slow,remainder)
                    thread.join(timeout=3)
                self.assertTrue(rotated_while_upload_pending,'slow body blocked token rotation')
                self.assertEqual(result[0][0],200)
                self.assertEqual(slow_reply[0],401)
                self.assertIn('重新配对',slow_reply[1]['error'])
                self.assertEqual(server.state.sequence,sequence)
                self.assertEqual(session.phase,'failed')
                self.assertEqual(session.coarse_samples,[])
                self.assertEqual(session.usb_reports,[])
                if role == 'phone': self.assertIsNone(server.state.frame)


class ProviderTests(unittest.TestCase):
    def response(self, value):
        return io.BytesIO(json.dumps(value).encode())
    def test_simple_open_commands_do_not_need_a_visual_guess(self):
        for text, app in (('打开设置','settings'),('请打开设置。','settings'),('帮我打开相机！','camera')):
            with patch.dict('os.environ',{'VISION_PROVIDER':'anthropic'},clear=True), patch('server.urllib.request.urlopen') as api:
                self.assertEqual(server.decide({'text':text},{},None),{'type':'open_app','text':app})
                api.assert_not_called()
    def test_compound_commands_and_open_app_results_still_use_vision(self):
        for text, previous in (('打开设置并关闭蓝牙',None),('不要打开设置',None),
                               ('打开银行',None),('打开设置',{'type':'open_app','text':'settings'})):
            with self.subTest(text=text,previous=previous), patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','VISION_PROVIDER':'anthropic'},clear=True), \
                    patch('server.urllib.request.urlopen',return_value=self.response({'stop_reason':'end_turn','content':[{'type':'text','text':'{"type":"assist"}'}]})) as api:
                frame={'image':'aGVsbG8=','width':390,'height':844,'point_width':390,'point_height':844}
                self.assertEqual(server.decide({'text':text},frame,previous)['type'],'assist')
                api.assert_called_once()
    @patch('server.open_qwen')
    def test_qwen_wav_request(self, urlopen):
        urlopen.return_value = self.response({'output':{'text':'打开设置'}})
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY':'test'}, clear=True):
            self.assertEqual(server.transcribe(b'RIFFexample'), '打开设置')
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'qwen-audio-3.1-asr-flash')
        self.assertTrue(payload['input']['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,'))
    def test_interface_binding_rejects_unsupported_platform(self):
        request=urllib.request.Request('https://example.com/')
        with patch.dict('os.environ', {'DASHSCOPE_INTERFACE':'en0'}, clear=True), patch('server.sys.platform','linux'):
            with self.assertRaisesRegex(ValueError, '网络接口'):
                server.open_qwen(request)
    def test_provider_diagnostics_omit_sensitive_body(self):
        for provider, code, kind, reason in (('Qwen',401,'InvalidApiKey','密钥'),
                                           ('Qwen',403,'AllocationQuota.FreeTierOnly','额度'),
                                           ('Anthropic',404,'not_found_error','模型')):
            body=json.dumps({'code':kind,'message':'private credential and audio'}).encode()
            error=urllib.error.HTTPError('https://example.com/',code,'failed',{},io.BytesIO(body))
            output=io.StringIO()
            with contextlib.redirect_stdout(output): message=server.provider_error(provider,error)
            self.assertIn(str(code),str(message))
            self.assertIn(reason,str(message))
            self.assertIn(kind,output.getvalue())
            self.assertNotIn('private',output.getvalue())
    def test_qwen_http_error_is_reported(self):
        error=urllib.error.HTTPError('https://example.com/',401,'failed',{},io.BytesIO(b'{}'))
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY':'test'}, clear=True), \
                patch('server.open_qwen',side_effect=error), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError,'Qwen HTTP 401'):
                server.transcribe(b'RIFFexample')
    def test_provider_disconnects_are_reported_without_response_data(self):
        frame={'image':'aGVsbG8=','point_width':390,'point_height':844,'width':390,'height':844}
        for provider, target, call in (
                ('Anthropic','server.urllib.request.urlopen',lambda: server.decide_anthropic({'text':'打开微信'},frame,None)),
                ('Qwen','server.open_qwen',lambda: server.transcribe(b'RIFFexample'))):
            for error in (http_client.RemoteDisconnected('private body'),
                          http_client.IncompleteRead(b'private body',20), ConnectionResetError('private body')):
                output=io.StringIO()
                with self.subTest(provider=provider,error=type(error).__name__), \
                        patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','DASHSCOPE_API_KEY':'test'}), \
                        patch(target,side_effect=error), contextlib.redirect_stdout(output):
                    with self.assertRaisesRegex(ValueError,'连接中断'): call()
                self.assertNotIn('private',output.getvalue())
    @patch('server.urllib.request.urlopen')
    def test_anthropic_vision_request(self, urlopen):
        urlopen.return_value = self.response({'stop_reason':'end_turn','content':[{'type':'text','text':'{"type":"assist"}'}]})
        frame = {'image':'aGVsbG8=', 'point_width':390, 'point_height':844, 'width':390, 'height':844}
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'test'}, clear=True):
            self.assertEqual(server.decide_anthropic({'text':'hello'}, frame, None)['type'], 'assist')
        payload=json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload['model'], 'claude-haiku-5-5')
        self.assertEqual(payload['output_config']['format']['type'],'json_schema')
        schema=payload['output_config']['format']['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertIn('open_app',schema['properties']['type']['enum'])
    def test_vision_rejects_truncated_or_refused_action(self):
        for stop in ('max_tokens','refusal'):
            result={'stop_reason':stop,'content':[{'type':'text','text':'{"type":"open_app","text":"settings"}'}]}
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                server.parse_vision_action(result)
            self.assertEqual(server.state.provider_failure['stop_reason'],stop)
    def test_vision_format_diagnostics_exclude_output_text(self):
        output=io.StringIO()
        result={'stop_reason':'end_turn','content':[{'type':'text','text':'private screenshot details'}]}
        with contextlib.redirect_stdout(output), self.assertRaises(ValueError):
            server.parse_vision_action(result)
        self.assertEqual(server.state.provider_failure['output_chars'],26)
        self.assertNotIn('private',output.getvalue())

if __name__ == '__main__': unittest.main()
