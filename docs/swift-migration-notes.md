# Swift 迁移预研

## 中文输入

`tests/PinyinProbe.swift` 和 `tests/compare_pinyin.py` 对照 macOS 15.7.9 的系统 `CFStringTransform` 与当前工作区使用的 pypinyin 0.55.0。固定样本中：

| 文字 | 系统转换 | 当前 Python 保守规则 |
|---|---|---|
| 你好 | nihao | nihao |
| 重庆 | chongqing | chongqing |
| 银行 | yinxing | yinhang |
| 音乐 | yinle | yinyue |
| 微信 | weixin | 拒绝：存在多个读音 |
| 重 | zhong | 拒绝：存在多个读音 |

系统转换仅提供一个结果，不能直接复现现有「存在多读音则拒绝」规则。不能用它替换现有安全判断后直接执行中文输入。后续需选定可识别多音字且许可适用的 Swift 方案，并与冻结的现有测试样本及真实输入法候选框联调；以上结果也不能保证与 iOS 系统版本完全一致。

可用现有项目虚拟环境执行：

```sh
swiftc -swift-version 6 tests/PinyinProbe.swift -o /tmp/phone-pilot-pinyin-probe
/Users/pom/Code/yaykit/phone-pilot-ios/demo/.venv/bin/python tests/compare_pinyin.py /tmp/phone-pilot-pinyin-probe
```

## 其他部分

- 坐标拟合做了隔离的 Swift 概念验证：与当前工作区 `pointer.py` 比较，一组正常数据和三组拒绝数据结果一致。它只在合成样本上测试，不进入 iOS App，也不能作为真实指针验收。复现方式：

  ```sh
  swiftc -parse-as-library -swift-version 6 tests/CalibrationProbe.swift -o /tmp/phone-pilot-calibration-probe
  /Users/pom/Code/yaykit/phone-pilot-ios/demo/.venv/bin/python tests/compare_calibration.py /tmp/phone-pilot-calibration-probe /Users/pom/Code/yaykit/phone-pilot-ios/demo/pointer.py
  ```

- 指针图像定位和画面差异检查仍由另一会话修改，先等待真实设备验证并冻结样本，再建立 Python/Swift 对照，避免移植不稳定算法。
- iOS 可以用 URLSession 调用模型，用 Keychain 保存密钥；本分支只验证无密钥 HTTPS，未实现或验证任何供应商请求。
- 本机只有 Command Line Tools、没有 iOS SDK；不能在此编译 iPhone App。现有 GitHub Actions 使用 iOS 27 SDK 的云端机器进行无签名构建。加入 `pipefail` 让 `xcodebuild` 失败不会被末尾的 `tail` 掩盖。TestFlight 的签名、权限和后台行为仍需另行验证。
