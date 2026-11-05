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
| 打开 App | “打开微信”“麻烦把微信给我打开” | 真机通过 |
| 输入文字（不发送） | “输入你好”“帮我写上明天见” | 理解已通过，写入待验证 |
| 回主屏 | “回到主屏幕” | 待验证 |
| 读屏幕，文字显示在圆屏 | “读屏幕”“屏幕上写了什么” | 待验证 |
| 滑动 | “往下滑”“往左滑” | 待验证 |
| 点击屏幕上唯一的控件 | “点一下搜索”“返回” | 待验证 |
| 给微信联系人发消息 | “告诉张三今晚开会” | 发给文件传输助手通过，真实联系人待验证 |

可以打开的 App 有 19 个：设置、相机、照片、微信、支付宝、地图、音乐、日历、时钟、计算器、备忘录、天气、信息、电话、邮件、浏览器、抖音、淘宝、小红书。

不支持付款和删除，句子里提到钱、转账、删除或否定词时不会执行。微信发消息发送前不再确认，靠核对联系人名字和聊天标题把关，有疑问就不发。语音内容识别错了，错字会直接发出去。完整规则和验证记录见 [demo/ictrl-integration.md](demo/ictrl-integration.md)。

## 仓库内容

| 位置 | 内容 |
|---|---|
| [demo/ictrl-integration.md](demo/ictrl-integration.md) | 圆屏直连方案、安全规则、安装 runner 的步骤和验证状态，先看这份 |
| `.github/workflows/ictrl.yml`、`demo/ictrl_setup.py` | 在 GitHub Actions 里编译、签名 runner，再安装到手机 |
| `demo/ictrl_driver.py`、`demo/ictrl_probe.py` | Mac 上调试 runner 的命令行工具，真机验收也用它 |
| [demo/README.md](demo/README.md)、`demo/server.py` | 早期经过 Mac 的路径，可选走 iCTRL，默认走录屏和 USB 键鼠 |
| `Sources/`、`project.yml`、[docs/ci.md](docs/ci.md) | iPhone 端 App（屏幕共享），用 XcodeGen 生成工程，在云端编译并发到 TestFlight，最低 iOS 27 |
| `demo/third_party/` | 第三方代码和许可证，来源见其中的 README |

`demo/` 的测试这样运行，需要先按 `demo/requirements.txt` 装好依赖：

```zsh
cd demo && python3 -m unittest discover -s . -p 'test_*.py'
```

## 注意

- **固件不能外传：** 构建时会把 DashScope 密钥写进固件，所以编译产物不能发布或分享。
- **配对密钥等于手机的控制权：** 不用时关掉 runner 的远程访问，只在可信 Wi-Fi 上用，只批准自己发起的配对。
- **账号风险：** App 可能检测到自动化操作，微信等账号要自己权衡，并遵守各 App 的条款。
- **用途限制：** iCTRL 上游只说明用于自动化测试和研究，这部分不能直接作为面向用户的正式方案发布。
- **手机重启后：** 要重新挂载 Developer Disk Image，不用 Mac 的路径还没有实测。

## 致谢

指针定位参考 Henry Yuan 的 [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) 和 [Open iPhone Use](https://github.com/semsturgut/open-iphone-use)。iCTRL 的客户端和签名脚本来自 [OpenMinis/iCTRL](https://github.com/OpenMinis/iCTRL)。
