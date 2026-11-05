"""Measured HID landing, without clicking or sending frames to a model.

Disc detection and affine fitting adapted from Open iPhone Use (Apache-2.0).
Move/observe/correct-before-click workflow informed by ESP32 iPhone Agent
by Henry Yuan (MIT). See third_party/README.md and bundled licenses.
"""
import base64
import io
import math
import time
from PIL import Image, ImageChops, ImageDraw, ImageFilter

PROBES=((80,128),(200,400),(80,400),(200,128),(140,264))
TOLERANCE=6  # iPhone points, independently on both axes; provisional until measured.
MAX_UNITS=2000


class SceneChanged(ValueError):
    pass


def image(frame):
    with Image.open(io.BytesIO(base64.b64decode(frame['image'],validate=True))) as source:
        if source.format != 'JPEG' or source.size != (frame['width'],frame['height']) or source.width*source.height>2_000_000:
            raise ValueError('指针画面无效')
        return source.convert('RGB')


def components(mask):
    w,h=mask.size; pixels=bytearray(mask.tobytes()); found=[]
    for seed,value in enumerate(pixels):
        if not value: continue
        pixels[seed]=0; pending=[seed]; x0=x1=seed%w; y0=y1=seed//w
        for index in pending:
            x,y=index%w,index//w
            x0=min(x0,x); x1=max(x1,x); y0=min(y0,y); y1=max(y1,y)
            for ny in range(max(0,y-1),min(h,y+2)):
                for nx in range(max(0,x-1),min(w,x+2)):
                    n=ny*w+nx
                    if pixels[n]: pixels[n]=0; pending.append(n)
        found.append((len(pending),(x0,y0,x1+1,y1+1)))
    return found


def difference(a,b):
    red,green,blue=ImageChops.difference(a,b).split()
    return ImageChops.lighter(ImageChops.lighter(red,green),blue)


def discs(pic,delta):
    """Only changed, compact grey discs; never an arbitrary diff centroid."""
    w,h=pic.size; found=[]
    red,green,blue=pic.split()
    spread=ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(red,green),blue),
                               ImageChops.darker(ImageChops.darker(red,green),blue))
    for low,high,chroma in ((80,190,12),(60,110,48),(100,145,48),(135,180,48),(170,215,48)):
        mask=ImageChops.multiply(red.point(lambda v: 255 if low<=v<=high else 0),
                                spread.point(lambda v: 255 if v<chroma else 0))
        ImageDraw.Draw(mask).rectangle((0,0,w,math.ceil(49*h/844)),fill=0)
        mask=mask.filter(ImageFilter.MinFilter(3))
        for count,(x0,y0,x1,y1) in components(mask):
            cw,ch=x1-x0,y1-y0
            if not (4<=min(cw,ch) and max(cw,ch)<=w/10 and .8<=cw/ch<=1.25 and .6<=count/(cw*ch)<=.94): continue
            if delta is not None:
                changed=delta.crop((x0,y0,x1,y1)).histogram()
                if sum(changed[13:]) < cw*ch*.2: continue
            center=((x0+x1-1)/2,(y0+y1-1)/2)
            if not any(math.dist(center,p)<3 for p,_ in found):
                # Include the eroded edge and JPEG halo, but no whole UI region.
                box=(max(0,(x0-2)//16*16),max(0,(y0-2)//16*16),
                     min(w,math.ceil((x1+2)/16)*16),min(h,math.ceil((y1+2)/16)*16))
                found.append((center,box))
    return found


def protected_region(residual,action):
    if action and (action['type']=='swipe' or action.get('scope')=='app_icon'):
        bounds=(78,688,312,712) if action['type']=='swipe' else (
            action['x']-24,action['y']-24,action['x']+24,action['y']+24)
        sx,sy=residual.width/390,residual.height/844
        return residual.crop((max(0,math.floor(bounds[0]*sx)),max(0,math.floor(bounds[1]*sy)),
                              min(residual.width,math.ceil(bounds[2]*sx)),min(residual.height,math.ceil(bounds[3]*sy))))


def scene_matches(residual,action=None):
    navigation=action is None or action['type']=='swipe' or action.get('scope')=='app_icon'
    if residual.getextrema()[1]>(8 if navigation else 0): return False
    region=protected_region(residual,action)
    return region is None or region.getextrema()[1]==0


def observe(before,after,action=None,diagnostic=None):
    if any(before[k] != after[k] for k in ('width','height','point_width','point_height','orientation')):
        raise ValueError('屏幕尺寸或方向已变化，请重新校准')
    a,b=image(before),image(after); delta=difference(a,b)
    old,new=discs(a,delta),discs(b,delta)
    if diagnostic is not None:
        diagnostic.update(pointer_candidates=[len(old),len(new)],
                          pointer_boxes_px=[list(box) for _,box in old+new])
    if len(new)!=1 or len(old)>1: raise ValueError('共享画面无法唯一识别指针，请保持标准灰色指针可见')
    p=new[0][0]
    observed=(p[0]*390/b.width,p[1]*844/b.height)
    if diagnostic is not None: diagnostic['observed']=list(observed)
    residual=delta.copy(); draw=ImageDraw.Draw(residual)
    if action is None or action['type'] != 'send':
        draw.rectangle((0,0,b.width,math.ceil(49*b.height/844)),fill=0)
    for _,box in old+new: draw.rectangle(box,fill=0)
    if diagnostic is not None:
        histogram=residual.histogram()
        region=protected_region(residual,action)
        diagnostic['scene_difference']={'max_channel_delta':residual.getextrema()[1],
            'changed_pixels':sum(histogram[1:]),'pixels_over_8':sum(histogram[9:]),
            'bbox_px':residual.getbbox(),
            'strong_bbox_px':residual.point(lambda v: 255 if v>8 else 0).getbbox(),
            'protected_region_changed_pixels':sum(region.histogram()[1:]) if region else 0}
    if not scene_matches(residual,action): raise SceneChanged('指针移动后画面变化未消退，已停止点击')
    return observed


def fit(samples):
    if len(samples)<5: raise ValueError('指针校准样本不足')
    result={'gain':[],'offset':[],'rms':[]}
    for axis,span in ((0,40),(1,80)):
        units=[s[axis] for s in samples]; positions=[s[axis+2] for s in samples]
        if not all(math.isfinite(v) for v in units+positions) or max(positions)-min(positions)<span:
            raise ValueError('指针校准范围不足或碰到屏幕边缘')
        mu,mp=sum(units)/len(units),sum(positions)/len(positions)
        variance=sum((u-mu)**2 for u in units)
        if variance==0: raise ValueError('指针校准移动量无效')
        gain=sum((u-mu)*(p-mp) for u,p in zip(units,positions))/variance
        offset=mp-gain*mu
        errors=[p-(gain*u+offset) for u,p in zip(units,positions)]
        rms=math.sqrt(sum(e*e for e in errors)/len(errors))
        if not .1<=gain<=3 or abs(offset)>80 or rms>3 or max(map(abs,errors))>TOLERANCE:
            raise ValueError('指针校准不一致，请检查跟踪速度和缩放')
        result['gain'].append(gain); result['offset'].append(offset); result['rms'].append(rms)
    return result


class Job:
    def __init__(self,action,frame,calibration=None):
        if (frame['point_width'],frame['point_height'],frame['orientation'])!=(390,844,'portrait'):
            raise ValueError('指针校准仅支持 iPhone 16e 竖屏')
        self.target=((300 if action['direction']=='left' else 90),700) if action['type']=='swipe' else (action['x'],action['y'])
        if not (6<self.target[0]<384 and 60<self.target[1]<838): raise ValueError('目标靠近屏幕边缘，无法验证指针')
        self.action=action; self.origin=frame; self.reference=frame; self.calibration=calibration
        self.samples=[]; self.issued=None; self.barrier=None; self.sequence=0
        self.ready=False; self.authorized=False; self.failure=None; self.corrections=0
        self.units=None; self.landing=None; self.deadline=time.monotonic()+60
        self.scene_retries=0; self.scene_deadline=None
        self.diagnostic={'phase':'calibrating' if calibration is None else 'aiming','target':list(self.target),'verified':False}

    def issue(self,units):
        if not all(type(u) is int and 0<=u<=MAX_UNITS for u in units) or sum(units)>3000: raise ValueError('校准后的鼠标移动量越界')
        self.sequence+=1; self.units=units
        self.scene_retries=0; self.scene_deadline=None
        self.diagnostic.update(move_sequence=self.sequence,requested_units=list(units),scene_retries=0)
        self.issued={'seq':self.sequence,'home':True,'dx':units[0],'dy':units[1]}
        return {'move':dict(self.issued),'calibrating':self.calibration is None}

    def aim(self):
        self.diagnostic.update(phase='aiming',gain=self.calibration['gain'],offset=self.calibration['offset'])
        return self.issue(tuple(round((self.target[a]-self.calibration['offset'][a])/self.calibration['gain'][a]) for a in (0,1)))

    def advance(self,frame,body):
        if self.failure: raise ValueError(self.failure)
        try: return self._advance(frame,body)
        except (ValueError,OSError) as exc:
            self.failure=str(exc); self.ready=False; self.authorized=False
            self.diagnostic.update(phase='stopped',verified=False)
            raise ValueError(self.failure) from exc

    def _advance(self,frame,body):
        if time.monotonic()>self.deadline: raise ValueError('指针校准超时，已停止点击')
        if frame is None: raise ValueError('屏幕共享已中断，已停止点击')
        if any(frame[k]!=self.reference[k] for k in ('width','height','point_width','point_height','orientation')):
            raise ValueError('屏幕尺寸或方向已变化，请重新校准')
        if self.scene_deadline is not None and time.monotonic()>self.scene_deadline:
            raise SceneChanged('指针移动后画面变化未消退，已停止点击')
        if 'seq' in body:
            if not self.issued or type(body['seq']) is not int or body['seq']!=self.issued['seq']:
                raise ValueError('指针移动回报重复或失效')
            if body.get('ok') is not True: raise ValueError('USB 移动失败，已停止点击')
            self.issued=None; self.barrier=frame['sequence']
            return {'wait':True}
        if self.issued: raise ValueError('指针移动未回报，禁止重复执行')
        if self.ready: return self.ready_reply()
        if self.barrier is None:
            if body.get('poll'): raise ValueError('指针校准尚未开始')
            return self.issue(PROBES[0]) if self.calibration is None else self.aim()
        if frame['sequence']<=self.barrier: return {'wait':True}
        # Compare with the decision's original scene, not just the previous
        # probe: small changes must not accumulate into an undetected page edit.
        try:
            self.landing=observe(self.origin,frame,self.action,self.diagnostic)
        except SceneChanged:
            navigation=self.action['type']=='swipe' or self.action.get('scope')=='app_icon'
            if not navigation or self.scene_retries>=2: raise
            self.scene_retries+=1
            if self.scene_deadline is None: self.scene_deadline=time.monotonic()+3
            self.barrier=frame['sequence']
            self.diagnostic.update(phase='waiting_for_scene',scene_retries=self.scene_retries)
            return {'wait':True}
        self.scene_deadline=None; self.reference=frame
        self.diagnostic['phase']='calibrating' if self.calibration is None else 'aiming'
        self.diagnostic['observed']=list(self.landing)
        if self.calibration is None:
            self.samples.append((*self.units,*self.landing)); self.diagnostic['samples']=len(self.samples)
            if len(self.samples)<len(PROBES): return self.issue(PROBES[len(self.samples)])
            self.calibration=fit(self.samples)
            return self.aim()
        error=[abs(self.target[a]-self.landing[a]) for a in (0,1)]
        self.diagnostic.update(error_points=error,corrections=self.corrections)
        if max(error)<=TOLERANCE:
            self.ready=True; self.diagnostic.update(phase='verified',verified=True)
            return self.ready_reply()
        if self.corrections>=2: raise ValueError('指针落点仍有偏差，已停止点击')
        self.corrections+=1
        # Rehome and compensate the observed miss, then require another observation.
        return self.issue(tuple(round(self.units[a]+(self.target[a]-self.landing[a])/self.calibration['gain'][a]) for a in (0,1)))

    def ready_reply(self):
        result={'ready':True}
        if self.action['type']=='swipe':
            units=round(210/self.calibration['gain'][0])*(-1 if self.action['direction']=='left' else 1)
            if not 1<=abs(units)<=MAX_UNITS: raise ValueError('翻页移动量越界')
            result['drag_units']=units
        return result

    def check(self,frame):
        self.authorized=False
        if not self.ready or self.failure or frame is None or time.monotonic()>self.deadline: return False
        if any(frame[k]!=self.reference[k] for k in ('width','height','point_width','point_height','orientation')): return False
        # The last observed pointer is the only body region exempted after aiming.
        # No fresh prediction can substitute for a measured landing.
        current=image(frame)
        candidates=[p for p,_ in discs(current,None)
                    if max(abs(p[0]*390/current.width-self.target[0]),abs(p[1]*844/current.height-self.target[1]))<=TOLERANCE]
        if len(candidates)!=1: return False
        delta=difference(image(self.reference),current); draw=ImageDraw.Draw(delta)
        x,y=self.landing[0]*delta.width/390,self.landing[1]*delta.height/844
        radius=delta.width*.055
        draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=0)
        if self.action['type'] != 'send':
            draw.rectangle((0,0,delta.width,math.ceil(49*delta.height/844)),fill=0)
        self.authorized=scene_matches(delta,self.action)
        return self.authorized
