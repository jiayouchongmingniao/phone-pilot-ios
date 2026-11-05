# phone-pilot-ios

圆屏直连手机（语音 → Qwen → iCTRL，不经过 Mac）的现状和验证结果见 [demo/ictrl-integration.md](demo/ictrl-integration.md)。当前独立 Demo 见 [demo/README.md](demo/README.md)。指针定位参考 Henry Yuan 的 [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) 和 [Open iPhone Use](https://github.com/semsturgut/open-iphone-use)，来源及本地修改见 [许可说明](demo/third_party/README.md)。以下保留最初工程说明。

Yay 圆屏应用 phone-pilot 的 iPhone 端。圆屏通过 USB-C 当 iPhone 的键盘和鼠标；这个 app 以后负责 iPhone 这一侧的事。

现在这一版只是一个占位 app，用来跑通「GitHub Actions 用 Xcode 27 编译 → 自动签名 → TestFlight 安装」这条路。

- 工程用 [XcodeGen](https://github.com/yonaskolb/XcodeGen) 从 `project.yml` 生成，`.xcodeproj` 不进仓库。
- 最低 iOS 27。
- 编译、签名、上传全在云端，见 [docs/ci.md](docs/ci.md)。
- 图标由 `tools/make_icon.py` 生成。
