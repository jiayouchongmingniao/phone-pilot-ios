#!/usr/bin/env python3
"""只读取手机当前页面，判断一个 App 能不能被 iCTRL 读到。不点击、不输入、不滑动。

    python3 demo/ictrl_probe.py [名字]

结果存到 phone-pilot/build/ictrl-probe/（被 git 忽略）。页面可能含地址、手机号等个人信息，不要放进公开仓库。
"""
import base64
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'third_party'))
import ictrl_rpc  # noqa: E402

READ_ONLY = {'device_info', 'page_source', 'screen_text', 'screenshot'}
OUT = os.path.expanduser('~/Code/yaykit/phone-pilot/build/ictrl-probe')


def call(host, method, params=None):
    if method not in READ_ONLY:
        raise SystemExit('只允许只读调用')
    return ictrl_rpc.device_call(host, method, params or {}, timeout=60)


def save(name, data):
    path = os.path.join(OUT, name)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(data)


def main():
    host = os.environ.get('ICTRL_HOST')
    if not host:
        raise SystemExit('需要环境变量 ICTRL_HOST')
    label = re.sub(r'[^\w-]', '_', sys.argv[1]) if len(sys.argv) > 1 else 'page'
    os.makedirs(OUT, mode=0o700, exist_ok=True)
    os.chmod(OUT, 0o700)

    info = call(host, 'device_info').get('result', {}).get('system', {})
    print('前台 App：', info.get('foreground_app', '未知'))
    print('锁屏：', info.get('screen_locked'))

    tree = call(host, 'page_source', {'visible_only': True, 'max_children': 200})
    result = tree.get('result', {})
    source = str(result.get('source', ''))
    save(label + '-tree.json', json.dumps(tree, ensure_ascii=False, indent=1).encode())
    kinds = collections.Counter(re.findall(r'^\s*[-→ ]*([A-Z][A-Za-z]+)', source, re.M))
    print('无障碍树：', len(source), '字符；错误：', tree.get('error'))
    print('元素类型：', kinds.most_common(10))
    for key in ('blocked_by', 'system_alert', 'channel', 'hint'):
        if key in result:
            print(key + '：', str(result[key])[:200])

    text = call(host, 'screen_text')
    save(label + '-ocr.json', json.dumps(text, ensure_ascii=False, indent=1).encode())
    print('OCR 文字条数：', len(text.get('result', {}).get('items', [])), '；错误：', text.get('error'))

    shot = call(host, 'screenshot', {'format': 'jpeg'})
    image = shot.get('result', {}).get('image') or shot.get('result', {}).get('data')
    if image:
        save(label + '.jpg', base64.b64decode(image.split(',')[-1]))
        print('截图：已保存')
    else:
        print('截图：没有取到；字段', sorted(shot.get('result', {}))[:10], '错误', shot.get('error'))
    print('已保存到', OUT)


if __name__ == '__main__':
    main()
