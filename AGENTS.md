# 交接说明：手机遥控（phone-pilot）

写给接手的编码助手（Codex）。这份文件讲整个项目；圆屏端的细节在圆屏仓库的 `AGENTS.md`。

**跟用户说话用中文**，包括进度说明。

## 1. 要做成什么

Yay 圆屏（ESP32-S3 的圆形小屏，Yaykit 平台）用语音操作用户的 iPhone：

```text
用户按住侧键说话
  → 圆屏录音，云端识别成文字（Yaykit 云能力）
  → 判断要做什么（本地规则；对不上的交给模型）
  → 圆屏通过 USB-C 当 iPhone 的键盘和鼠标去执行（已经能用）
  ↑
iPhone 上的 app 把屏幕画面发出来，让「判断」看得见当前界面（还没做）
```

两个仓库：

| 仓库 | 位置 | 是什么 |
|---|---|---|
| `phone-pilot` | 本机 `/Users/pom/Code/yaykit/phone-pilot`（只在本地，没有远程） | 圆屏上的应用（ESP-IDF 5.5.5，Yaykit 开发包 1.3） |
| `phone-pilot-ios` | 本机 `/Users/pom/Code/yaykit/phone-pilot-ios`，GitHub `jiayouchongmingniao/phone-pilot-ios`（**公开**） | iPhone 端 app，现在只是占位 |

## 2. 已经验证过的（真机，iPhone 16e）

- 圆屏当 **USB 键盘**：⌘空格调出 Spotlight，输 `settings`、`camera` 回车能打开设置、相机；能在输入框里打英文。
- 圆屏当 **USB 鼠标**：要先在 iPhone 打开「辅助功能 › 触控 › AssistiveTouch」才有指针。能把指针移到指定位置并点击，点开了主屏上的指定图标。
- **USB 交还**：应用占着 USB 时 Mac 只看到 HID 设备；离开应用后串口每次都回来，能再安装（两台圆屏测了多轮）。
- **iPhone 端流水线**：GitHub Actions 用 Xcode 27.1 编译 → API 密钥自动签名 → 上传 TestFlight → 已装到用户手机并能打开（0.1.0 build 5）。

## 3. 环境限制（先读）

- **开发用的 Mac 是 Intel 的 MacBook Pro 2018，macOS 15.7.9，没有 Xcode。** Xcode 27 只能装在 Apple 芯片 Mac 上，所以 **iPhone 端只能在云端编译**，本机编不了、也跑不了模拟器。
- 本机磁盘只剩二十几 GB，别装大东西。
- 访问 GitHub 有时连不上，要先设代理（`ALL_PROXY`），地址问用户。

## 4. iPhone 端：怎么编译和发版

- 工程由 [XcodeGen](https://github.com/yonaskolb/XcodeGen) 从 `project.yml` 生成，`.xcodeproj` 不进仓库。源码在 `Sources/`，图标由 `tools/make_icon.py` 生成。
- `.github/workflows/ios.yml`：每次推送在 `runs-on: xcode-27` 上**不签名编译**（约 1 分钟），这就是你的编译器，改完推上去看结果：

  ```bash
  gh run list -R jiayouchongmingniao/phone-pilot-ios --limit 3
  gh run view <运行编号> -R jiayouchongmingniao/phone-pilot-ios --log-failed
  ```

- 发 TestFlight（会签名上传，构建号 = 运行编号）：

  ```bash
  gh workflow run iOS -R jiayouchongmingniao/phone-pilot-ios -f testflight=true
  ```

- 仓库密钥 `ASC_KEY_ID`、`ASC_ISSUER_ID`、`ASC_KEY_P8`、`APPLE_TEAM_ID` 已经配好（Admin 角色团队密钥）。细节和常见报错见 `docs/ci.md`。
- 套装 ID `io.tikpal.phonepilot`，App Store Connect 里的名字「AI Phone Pilot」，最低 iOS 27。
- **仓库是公开的**：不要提交密钥、`.p8`、设备编号、MAC 地址、邮箱。

## 5. 任务 A：iPhone 抓屏（iOS 端）

**目标**：用户在 app 里点一次开始，之后切到任何 app，画面都持续发出来，供「判断」看当前界面。

**做法和要求**：

- 用 **iOS 27 的 ScreenCaptureKit**，以 Apple 官方示例「Capturing screen content on iOS」为起点（developer.apple.com/documentation/screencapturekit/capturing-screen-content-on-ios）：系统的内容共享选择框选「整个屏幕」，`Info.plist` 声明 `UIBackgroundModes` 含 `screen-capture`，这样 app 不在前台也继续收帧。
- **低频、小图**：每秒 1 帧左右，缩到宽 390 像素（iPhone 16e 的宽度是 390 点，一像素一点，坐标好对应），JPEG 压缩。上一帧还没发完就丢掉新帧，不排队。
- **发到哪**：先做一个**局域网接收端**用于开发（比如 Mac 上一个小的 HTTP 或 WebSocket 服务，能看最新一帧、统计帧率），地址在 app 里可以填。之后再定最终去处（圆屏或云端，见第 7 节）。要声明 `NSLocalNetworkUsageDescription`，ATS 允许本地网络。
- 每一帧带上：宽、高、时间戳，以及「1 像素 = 多少点」，方便把模型给的坐标换算成指针移动。
- 界面：接收端地址、开始/停止、状态、已发帧数、最近一次错误。中文界面。

**要实测并记下结论的**：

- 切到别的 app、锁屏、来电时，抓取会不会停；停了 app 能不能知道。
- **AssistiveTouch 的鼠标指针会不会出现在抓到的画面里**。会的话，可以用画面闭环修正指针位置（第 6 节）。
- 内存、发热、耗电，每帧从抓到到收到的延迟。

## 6. 任务 B：语音遥控（圆屏端，详见圆屏仓库 `AGENTS.md`）

- 流程：按住侧键录音 → `yk_cloud_asr()` 识别 → `main/intent.c` 本地规则 → `main/keys.c` 生成按键和鼠标步骤 → `main/usb_hid.c` 发给 iPhone。规则对不上的句子交给 `yk_cloud_chat()`（模型从固定的几种动作里选一个，回 JSON）。
- **卡在 Yaykit**：云能力要 Yaykit 在云端启用 `phone-pilot` 并开通 `asr`、`llm.fast`，已提单 [#17](https://github.com/vzheng56/tikpal-devkit/issues/17)。开通前所有 `yk_cloud_*` 都会被拒；代码里先 `yk_cloud_me()` 再 `yk_cloud_has("asr")`，没有就在屏幕上说人话。
- **指针定位**（iPhone 16e，AssistiveTouch 默认跟踪速度）：iOS 对鼠标有加速，所以只用固定速度——每次报告约 14 个单位（`keys_pointer_glide`），先发 30 次 `(-127,-127)` 推到左上角。测得约 **0.87 点/单位**，精度约 ±10 点。换手机或改跟踪速度要重新校准。
- **中文输入**：键盘只发键码，打不了中文。可选：用 iPhone 的拼音输入法打拼音；或者让 iPhone 端 app 写剪贴板，圆屏发 ⌘V。

## 7. 还没定的（要问用户，别自己拍板）

- **「判断」放在哪**：Yaykit 云端目前**没有看图的接口**（设备 API 只有文字对话、语音、文件），要看图得再找 Yaykit 加；另一种是 iPhone 端 app 自己调模型（用户自己的 API key）。
- **画面最终发给谁**：圆屏（开发包有 `yaykit_lan`，是圆屏热点上的 WebSocket 服务，为「自拍屏」做的，令牌走蓝牙）还是云端。
- 动作的格式（建议一个小 JSON：`open_app`、`type`、`key`、`tap{x,y 点}`、`scroll`），两端要对齐。
- 安卓：另一条路是手机装开源的 [Mika](https://github.com/wangzan101/Mika)，圆屏用 Wi-Fi 调它的 `/api/v1/tasks`。用户说过 iPhone 优先。

## 8. 硬规矩

- 涉及付款、发消息、删除这类动作，执行前要用户确认。
- 不往公开仓库提交任何密钥或个人信息。
- 圆屏相关的规矩（只写位置 2/3、装之前问用户、USB 必须交还等）见圆屏仓库 `AGENTS.md`，**全部照做**。
