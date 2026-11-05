# phone-pilot-ios

用 Yay 圆屏（ESP32-S3）的语音操作 iPhone。这个仓库放手机这一侧的代码和整个项目的文档。开发中，只在一台 iPhone 16e（iOS 27.2）上验证过。

圆屏固件在单独的仓库，不在这里。

## 现在怎么工作

按住圆屏侧键说话，运行时不需要 Mac：

```text
圆屏录音 → Qwen 语音识别 → 本地规则，听不懂再问 Qwen 文字模型
  → 固件核对原句 → 加密 JSON-RPC（Wi-Fi）→ iPhone 上的 iCTRL runner
```

[iCTRL](https://github.com/OpenMinis/iCTRL) 用系统的无障碍树找控件，并核对前台 App，所以不用录屏看图，也不用校准，还能直接输入中文。

## 能说什么

| 指令 | 例子 | 验证状态 |
|---|---|---|
| 打开 App，不限于固定清单 | “打开微信”“帮我把美团打开” | 通过 |
| 关闭 App | “关闭抖音” | 通过 |
| 输入文字、清空、回车 | “输入你好”“清空输入框”“回车” | 通过 |
| 回主屏、锁屏 | “回到主屏幕”“锁屏” | 通过 |
| 读屏幕，文字显示在圆屏 | “读屏幕”“把这页读完” | 通过 |
| 滑动 | “往下滑”“往左滑” | 通过 |
| 点击、长按、双击屏幕上唯一的控件 | “点一下搜索”“长按头像”“返回” | 通过 |
| 滚动找控件再点 | “找到通用并点开” | 通过 |
| 微信发消息，联系人或群 | “告诉张三今晚开会” | 通过 |
| 回复当前打开的微信聊天 | “回复收到” | 通过 |
| 读微信消息、查未读 | “张三说了什么”“微信未读” | 通过 |
| 微信搜索、进入页面 | “微信搜露营”“朋友圈”“扫一扫” | 通过 |
| 微信语音、视频通话 | “给张三打语音电话”“和张三视频” | 通过 |
| 写朋友圈，由你在手机上点发表 | “发朋友圈今天天气真好” | 通过 |

以上是 2026-11-05 在 iPhone 16e（iOS 27.2）上由使用者确认的结果。

不支持付款和删除，句子里提到钱、转账、删除或否定词时不会执行。微信发消息发送前不再确认，靠核对名字和聊天标题把关，有疑问就不发。语音内容识别错了，错字会直接发出去。完整规则见 [demo/ictrl-integration.md](demo/ictrl-integration.md)。

## 仓库内容

| 位置 | 内容 |
|---|---|
| [demo/ictrl-integration.md](demo/ictrl-integration.md) | 圆屏直连方案、安全规则、安装 runner 的步骤和验证状态，先看这份 |
| `.github/workflows/ictrl.yml`、`demo/ictrl_setup.py` | 在 GitHub Actions 里编译、签名 runner，再安装到手机 |
| `demo/ictrl_driver.py`、`demo/ictrl_probe.py` | Mac 上调试 runner 的命令行工具，真机验收也用它 |
| [demo/README.md](demo/README.md)、`demo/server.py` | 早期经过 Mac 的路径，可选走 iCTRL，默认走录屏和 USB 键鼠 |
| `Sources/`、`project.yml`、[docs/ci.md](docs/ci.md) | iPhone 端 App（屏幕共享），用 XcodeGen 生成工程，在云端编译并发到 TestFlight，最低 iOS 27，圆屏直连用不到 |
| `demo/third_party/` | 第三方代码和许可证，来源见其中的 README |

`demo/` 的测试这样运行，需要先按 `demo/requirements.txt` 装好依赖：

```zsh
cd demo && python3 -m unittest discover -s . -p 'test_*.py'
```

## 能给谁用

iCTRL runner 基本不可能通过 TestFlight 分发：它要开发签名、开发者模式和 Developer Disk Image。目前的安装方式只适合开发者或少量内测，普通用户要换手机端方案。

runner 支持 iOS 16 及以上，目前只实测过 iPhone 16e、iOS 27.2。微信流程用了按这台手机屏幕尺寸写的坐标，其他尺寸的 iPhone 可能要改。细节见 [分发和系统版本](demo/ictrl-integration.md#分发和系统版本)。

## 注意

- **固件不能外传：** 构建时会把 DashScope 密钥写进固件，所以编译产物不能发布或分享。
- **配对密钥等于手机的控制权：** 不用时关掉 runner 的远程访问，只在可信 Wi-Fi 上用，只批准自己发起的配对。
- **账号风险：** App 可能检测到自动化操作，微信等账号要自己权衡，并遵守各 App 的条款。
- **用途限制：** iCTRL 上游只说明用于自动化测试和研究，这部分不能直接作为面向用户的正式方案发布。
- **手机重启后：** 要重新挂载 Developer Disk Image，不用 Mac 的路径还没有实测。

## 致谢

指针定位参考 Henry Yuan 的 [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) 和 [Open iPhone Use](https://github.com/semsturgut/open-iphone-use)。iCTRL 的客户端和签名脚本来自 [OpenMinis/iCTRL](https://github.com/OpenMinis/iCTRL)。
