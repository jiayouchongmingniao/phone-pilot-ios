import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
import pointer
import server


def frame(point=None, sequence=1, size=(390,844), extra=None, fill='black'):
    image=Image.new('RGB',size,(245,245,245))
    draw=ImageDraw.Draw(image)
    if point:
        x,y=point[0]*size[0]/390,point[1]*size[1]/844
        r=10*size[0]/390
        draw.ellipse((x-r,y-r,x+r,y+r),fill=(130,130,130))
    if extra: draw.rectangle(extra,fill=fill)
    out=io.BytesIO(); image.save(out,format='JPEG',quality=65)
    return dict(image=base64.b64encode(out.getvalue()).decode(),width=size[0],height=size[1],
                point_width=390,point_height=844,orientation='portrait',sequence=sequence,received=0)


class PointerTests(unittest.TestCase):
    def test_finds_moved_pointer_and_blocks_page_change(self):
        for size in ((390,844),(384,832)):
            before=frame((90,160),size=size); after=frame((210,360),size=size)
            point=pointer.observe(before,after)
            self.assertLess(abs(point[0]-210),2); self.assertLess(abs(point[1]-360),2)
            detail={}
            with self.assertRaises(ValueError): pointer.observe(before,frame((210,360),size=size,extra=(0,500,389,600)),diagnostic=detail)
            self.assertEqual(detail['pointer_candidates'],[1,1])
            self.assertGreater(detail['scene_difference']['pixels_over_8'],0)
            self.assertIsNotNone(detail['scene_difference']['strong_bbox_px'])
            self.assertLess(abs(detail['observed'][0]-210),2)
            self.assertNotIn('image',detail)
        with self.assertRaises(ValueError): pointer.observe(frame(),frame())

    def test_fit_uses_separate_gains_offsets_and_rejects_bad_samples(self):
        samples=[(x,y,.9*x-12,1.3*y-20) for x,y in pointer.PROBES]
        fit=pointer.fit(samples)
        self.assertAlmostEqual(fit['gain'][0],.9); self.assertAlmostEqual(fit['gain'][1],1.3)
        self.assertAlmostEqual(fit['offset'][1],-20)
        for bad in (samples[:2],[(x,y,100,100) for x,y in pointer.PROBES],
                    samples[:-1]+[(140,264,200,700)]):
            with self.assertRaises(ValueError): pointer.fit(bad)

    def test_full_calibration_and_verification_before_click(self):
        job=pointer.Job({'type':'tap','x':260,'y':110},frame())
        current=frame(); reply=job.advance(current,{})
        moves=0
        while 'move' in reply:
            move=reply['move']; moves+=1
            self.assertTrue(move['home'])
            # Simulate a real pointer with unequal X/Y gain and a home offset.
            current=frame((move['dx']*.9-12,move['dy']*1.3-20),sequence=moves+1)
            self.assertEqual(job.advance(current,{'seq':move['seq'],'ok':True}),{'wait':True})
            current=dict(current,sequence=current['sequence']+100)
            reply=job.advance(current,{'poll':True})
        self.assertTrue(reply['ready']); self.assertEqual(moves,6)
        self.assertTrue(job.check(current))
        self.assertLess(max(job.diagnostic['error_points']),6)
        self.assertFalse(job.check(frame((260,110),sequence=1000,extra=(0,200,389,400))))

    def test_missing_pointer_duplicate_ack_usb_failure_and_timeout_stop(self):
        for request in ({'seq':1,'ok':False},{'seq':2,'ok':True},{'poll':True}):
            job=pointer.Job({'type':'tap','x':200,'y':200},frame())
            job.advance(frame(),{})
            with self.assertRaises(ValueError): job.advance(frame(),request)
        job=pointer.Job({'type':'tap','x':200,'y':200},frame())
        job.advance(frame(),{}); job.advance(frame(),{'seq':1,'ok':True})
        with self.assertRaises(ValueError): job.advance(frame(),{'seq':1,'ok':True})
        job=pointer.Job({'type':'tap','x':200,'y':200},frame())
        job.advance(frame(),{}); job.advance(frame(),{'seq':1,'ok':True})
        with self.assertRaisesRegex(ValueError,'指针'): job.advance(frame(sequence=2),{'poll':True})
        job=pointer.Job({'type':'tap','x':200,'y':200},frame())
        with patch('pointer.time.monotonic',return_value=job.deadline+1):
            with self.assertRaisesRegex(ValueError,'超时'): job.advance(frame(),{})

    def test_corrections_are_bounded_and_never_use_unobserved_prediction(self):
        calibration={'gain':[1,1],'offset':[0,0]}
        job=pointer.Job({'type':'tap','x':200,'y':200},frame(),calibration)
        reply=job.advance(frame(),{})
        for attempt in range(3):
            step=reply['move']['seq']
            current=frame((100,100),sequence=step*2)
            job.advance(current,{'seq':step,'ok':True})
            # Synthetic observation remains far away despite corrections.
            with patch('pointer.observe',return_value=(100,100)):
                if attempt == 2:
                    with self.assertRaisesRegex(ValueError,'落点'): job.advance(dict(current,sequence=step*2+1),{'poll':True})
                else: reply=job.advance(dict(current,sequence=step*2+1),{'poll':True})
        self.assertFalse(job.ready)

    def test_pointer_masks_do_not_relax_send_or_target_region_checks(self):
        before=frame((90,160)); after=frame((210,360))
        send={'type':'send','x':260,'y':700}
        self.assertIsNotNone(pointer.observe(before,after,send))
        for extra,fill in (((20,10,60,30),'black'),((300,500,340,530),(242,242,242))):
            with self.assertRaises(ValueError):
                pointer.observe(before,frame((210,360),extra=extra,fill=fill),send)
        app={'type':'tap','x':260,'y':110,'scope':'app_icon'}
        with self.assertRaises(ValueError):
            pointer.observe(before,frame((210,360),extra=(256,104,271,119),fill=(242,242,242)),app)

    def test_recording_timer_refresh_does_not_cancel_app_calibration(self):
        action={'type':'tap','x':224,'y':103,'scope':'app_icon'}
        for size in ((390,844),(384,832)):
            sx,sy=size[0]/390,size[1]/844
            recording=(32*sx,8*sy,105*sx,40*sy)
            detail={}
            point=pointer.observe(frame((90,160),size=size),
                frame((210,360),size=size,extra=recording,fill=(255,30,20)),action,detail)
            self.assertLess(abs(point[0]-210),2)
            self.assertLessEqual(detail['scene_difference']['max_channel_delta'],8)
        job=pointer.Job(action,frame(),{'gain':[1,1],'offset':[0,0]})
        job.advance(frame(),{}); current=frame((224,103),sequence=2)
        job.advance(current,{'seq':1,'ok':True})
        job.advance(dict(current,sequence=3),{'poll':True})
        self.assertTrue(job.check(frame((224,103),sequence=4,extra=(32,8,105,40),fill=(255,30,20))))

    def test_cached_fit_is_one_move_and_rechecks_actual_landing(self):
        job=pointer.Job({'type':'tap','x':260,'y':110,'scope':'app_icon'},frame(),
                        {'gain':[.9,1.3],'offset':[-12,-20]})
        reply=job.advance(frame(),{})
        self.assertFalse(reply['calibrating'])
        move=reply['move']
        current=frame((move['dx']*.9-12,move['dy']*1.3-20),sequence=2)
        self.assertEqual(job.advance(current,{'seq':1,'ok':True}),{'wait':True})
        self.assertEqual(job.advance(current,{'poll':True}),{'wait':True})
        self.assertTrue(job.advance(dict(current,sequence=3),{'poll':True})['ready'])
        self.assertEqual(job.sequence,1); self.assertEqual(job.samples,[])
        self.assertTrue(job.check(current))
        self.assertFalse(job.check(frame((260,120),sequence=4)))
        self.assertFalse(job.authorized)

    def test_state_calibration_reuse_result_barrier_and_cancellation(self):
        from test_calibration import calibrate_state
        state=server.State(); state.pair('phone',state.code)
        state.pair('device',state.code,['native_touch_v1','hid_delivery_v1','home_swipe_v1','pointer_grid7_v1'])
        def upload(point=None,**kw):
            payload=frame(point,**kw); payload['jpeg']=payload.pop('image'); state.add_frame(payload)
        upload(); calibration=calibrate_state(state); state.begin('打开微信')
        action=state.propose({'type':'tap','x':260,'y':110,'label':'微信','scope':'app_icon'},state.current_frame())
        with self.assertRaisesRegex(ValueError,'校准定位尚未检查'): state.result(action['id'],True)
        reply=state.pointer_step(action['id'],{})
        self.assertFalse(reply['calibrating'])
        self.assertEqual(reply['move']['seq'],1)
        state.pointer_step(action['id'],{'seq':1,'ok':True})
        self.assertEqual(state.pointer_step(action['id'],{'poll':True}),{'wait':True})
        self.assertFalse(state.check_pending(action['id']))
        upload()  # Real screen sharing has no cursor; unchanged pixels are valid.
        self.assertTrue(state.pointer_step(action['id'],{'poll':True})['ready'])
        self.assertEqual(state.steps,1)
        self.assertTrue(state.check_pending(action['id']))
        state.result(action['id'],True)
        self.assertEqual(len(state.task['history']),1)
        self.assertIsNone(state.current_frame(after=state.last_action['frame']))
        self.assertTrue(state.pointer_diagnostic['calibration_verified'])
        self.assertFalse(state.pointer_diagnostic['landing_observed'])
        state.finish(); upload(); state.begin('打开微信')
        action=state.propose({'type':'tap','x':100,'y':200,'label':'微信','scope':'app_icon'},state.current_frame())
        fast=state.pointer_step(action['id'],{})
        self.assertFalse(fast['calibrating']); self.assertEqual(state.pointer_calibration,calibration)
        state.finish()
        with self.assertRaisesRegex(ValueError,'取消'): state.pointer_step(action['id'],{'seq':1,'ok':True})
        state.stop_capture(); self.assertIsNone(state.pointer_calibration)

    def test_old_firmware_and_changed_geometry_cannot_use_cached_fit(self):
        from test_calibration import calibrate_state
        state=server.State(); state.pair('phone',state.code); state.pair('device',state.code)
        payload=frame(); payload['jpeg']=payload.pop('image'); state.add_frame(payload)
        state.begin('打开微信')
        with self.assertRaisesRegex(ValueError,'更新圆屏'):
            state.propose({'type':'tap','x':260,'y':110,'label':'微信'},state.current_frame())
        state.finish(); state.device_capabilities=['native_touch_v1','hid_delivery_v1','pointer_grid7_v1']
        fit=calibrate_state(state)
        for invalid in (None,dict(fit,verified=False),dict(fit,algorithm='old-motion'),
                        dict(fit,geometry=[844,390,'landscape'])):
            with self.subTest(invalid=invalid):
                state.pointer_calibration=invalid; state.begin('打开微信')
                with self.assertRaisesRegex(ValueError,'校准'):
                    state.propose({'type':'tap','x':260,'y':110,'label':'微信'},state.current_frame())
                self.assertIsNone(state.pending)
                self.assertIsNone(state.pointer_job)
                state.pointer_calibration=fit
                action=state.propose({'type':'tap','x':260,'y':110,'label':'微信'},state.current_frame())
                state.pointer_calibration=invalid
                with self.assertRaisesRegex(ValueError,'校准'): state.pointer_step(action['id'],{})
                self.assertFalse(state.check_pending(action['id']))
                state.finish()

    def test_scene_drift_is_compared_with_original_decision(self):
        job=pointer.Job({'type':'tap','x':260,'y':110,'scope':'app_icon'},frame())
        reply=job.advance(frame(),{})
        for index,shade in enumerate((241,232),start=1):
            move=reply['move']
            current=frame((move['dx'],move['dy']),sequence=index*2,
                          extra=(0,600,40,630),fill=(shade,shade,shade))
            job.advance(current,{'seq':move['seq'],'ok':True})
            current=dict(current,sequence=index*2+1)
            if index==1: reply=job.advance(current,{'poll':True})
            else:
                self.assertEqual(job.advance(current,{'poll':True}),{'wait':True})
                job.advance(dict(current,sequence=6),{'poll':True})
                with self.assertRaisesRegex(ValueError,'变化未消退'): job.advance(dict(current,sequence=7),{'poll':True})
        self.assertFalse(job.authorized)

    def test_transient_scene_wait_does_not_repeat_motion_or_authorize_click(self):
        action={'type':'tap','x':224,'y':101,'scope':'app_icon'}
        job=pointer.Job(action,frame())
        job.advance(frame(),{}); job.advance(frame(),{'seq':1,'ok':True})
        hover=frame((84,111),sequence=2,extra=(10,200,200,250))
        self.assertEqual(job.advance(hover,{'poll':True}),{'wait':True})
        self.assertEqual(job.diagnostic['phase'],'waiting_for_scene')
        self.assertGreater(job.diagnostic['scene_difference']['changed_pixels'],0)
        self.assertEqual(job.diagnostic['scene_difference']['protected_region_changed_pixels'],0)
        for _ in range(5): self.assertEqual(job.advance(hover,{'poll':True}),{'wait':True})
        self.assertEqual(job.scene_retries,1); self.assertEqual(job.sequence,1)
        self.assertFalse(job.authorized)
        reply=job.advance(frame((84,111),sequence=3),{'poll':True})
        self.assertEqual(reply['move']['seq'],2); self.assertEqual(len(job.samples),1)

    def test_scene_wait_is_bounded_and_does_not_apply_to_send(self):
        for action in ({'type':'tap','x':224,'y':101,'scope':'app_icon'},
                       {'type':'send','x':224,'y':101}):
            job=pointer.Job(action,frame()); job.advance(frame(),{})
            job.advance(frame(),{'seq':1,'ok':True})
            changed=frame((84,111),sequence=2,extra=(10,200,200,250))
            if action['type']=='send':
                with self.assertRaisesRegex(ValueError,'变化未消退'): job.advance(changed,{'poll':True})
            else:
                self.assertEqual(job.advance(changed,{'poll':True}),{'wait':True})
                with patch('pointer.time.monotonic',return_value=job.scene_deadline+1):
                    with self.assertRaisesRegex(ValueError,'变化未消退'): job.advance(changed,{'poll':True})
            self.assertFalse(job.authorized)


if __name__ == '__main__': unittest.main()
