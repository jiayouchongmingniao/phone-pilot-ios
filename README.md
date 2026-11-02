# phone-pilot-ios

Yay 圆屏应用 phone-pilot 的 iPhone 端。圆屏通过 USB-C 当 iPhone 的键盘和鼠标；这个 app 以后负责 iPhone 这一侧的事。

现在这一版只是一个占位 app，用来跑通「GitHub Actions 用 Xcode 27 编译 → 自动签名 → TestFlight 安装」这条路。

- 工程用 [XcodeGen](https://github.com/yonaskolb/XcodeGen) 从 `project.yml` 生成，`.xcodeproj` 不进仓库。
- 最低 iOS 27。
- 编译、签名、上传全在云端，见 [docs/ci.md](docs/ci.md)。
- 图标由 `tools/make_icon.py` 生成。
