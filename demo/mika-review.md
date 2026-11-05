# Mika 如何操作手机及 Phone Pilot 借鉴方案

2026-11-03，源码固定在 [4ce3ca67e8cf8af8323a4d74505f51fd94178137](https://github.com/wangzan101/Mika/commit/4ce3ca67e8cf8af8323a4d74505f51fd94178137)，版本 0.1.34。三个只读子任务分别核对执行/权限、任务循环/反馈、画面变化/定位。未安装 Mika、运行上游测试、调用真实模型或操作设备；下述移植建议尚未实现。

## 结论

Mika 的执行器基于 Android 无障碍服务、ADB shell 助手和独立虚拟屏，不依赖截图中的鼠标光标。可以借其观察—执行—验证协议；不能把 Android 执行能力直接移到当前 iPhone。当前 iPhone 方案仍为 ScreenCaptureKit → Mac 观察/决策 → 圆屏 USB HID。

用户已确认 iPhone 录屏无指针，因此需要先以自己的原生校准页测量真实点击，再复用映射。Mika 不解决 HID 加速与坐标标定。Qwen 转写、Claude 提供语义/视觉证据、Jev 唯一决策的分工不变。

## 实际执行链

| 环节 | Mika 源码实现 | iPhone 适配边界 |
|---|---|---|
| 看界面 | 按 display 读取无障碍窗口/节点；虚拟屏通过 ImageReader 取图；主屏主动截图另走 AccessibilityService.takeScreenshot | 当前只能获得授权共享画面，没有这套其他 App 的节点树 |
| 点击 | 节点优先 ACTION_CLICK；坐标触控先 dispatchGesture，失败再用 shell 注入触控 | 我们仍需已校准的 HID；没有无障碍节点点击后备 |
| 滑动 | 无障碍手势，或 shell 按时间注入 DOWN/MOVE/UP | 可借动作格式，执行仍由圆屏鼠标按下/拖动/抬起 |
| 输入 | ACTION_SET_TEXT 后读回；WebView 等走选中/剪贴板粘贴并还原剪贴板 | 不能靠此解决 iPhone HID 中文；现有拼音方案仍须单独实测 |
| 打开 App | 启动 Intent 指定虚拟屏，核实任务/包名位于目标屏 | 当前 iPhone 继续用图标点击或 Spotlight，并看实际 App 页面 |
| 后台运行 | shell 创建独立 VirtualDisplay，目标 App 在其中执行 | 此仓库无 iOS 后端；不能承诺在 iPhone 隐藏虚拟屏中操纵微信 |

执行证据：[AndroidScreen 点击/手势](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/cap/phone/a11y/AndroidScreen.kt#L1691)、[输入与读回](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/cap/phone/a11y/AndroidScreen.kt#L478)、[虚拟屏采集](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/cap/phone/display/AgentDisplay.kt#L18)、[指定屏启动 App](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/cap/phone/shell/ShellService.kt#L224)。

不要求 root，但手机操作需要无障碍服务和 ADB shell 权限。无线调试配对或电脑 USB ADB 启动 helper 后，helper 校验 shell UID 2000；不是普通 App 权限即可全局注入。项目支持 Android 11 安装，操作 App 要 12+，推荐 14+。可信虚拟屏降级时可能共享焦点，不能将“不干扰用户”视为所有机型的保证。见 [HelperHandoff](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/shell/HelperHandoff.kt#L51)、[配置指南](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/docs/setup-guide.en.md)。

## 优先借鉴的机制

1. **目标属于一次观察。** Mika 的 `snapshotId + ref` 绑定节点；执行前刷新类型、名字、资源 ID 和位置，变化后只在当前界面唯一重定位。我们已有 frame sequence，可绑定目标框、识别证据和原始帧；真实页面改变后重新识图，不能复用旧坐标。普通 observe 并未原子冻结截图和树，不应声称上游已解决所有同步问题。[resolve](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/cap/phone/a11y/AndroidScreen.kt#L1591)

2. **传输成功、界面效果、任务完成分开。** `ActionResult.Ok` 只代表动作已发出；效果按界面结构、输入读回和前台 App 分为 `done / small_change / no_effect / place_changed`。其中 `done` 是单步效果分类，不是整个任务目标已完成。iPhone 应让 Claude 报告“微信页面已见 / 仍在桌面 / 别的 App / 过渡中 / 不可判断”，Jev 决定下一步；不把模型观察伪装成真实系统包名或控件读回。[ScreenPort](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/uistate/ScreenPort.kt#L139)、[ActionEffect](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/uistate/ActionEffect.kt#L173)

3. **无效果先重新观察，不自动补发点击。** Mika 动作后可有界等待再读一次，不重复执行该动作。我们必须等不同编号的新帧；1fps 下每 150ms 重读缓存不是“两张稳定帧”。没看到变化可能是焦点等不可见效果，应保留未知。[ScreenSession.withEffect](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/uistate/ScreenSession.kt#L370)

4. **模型重试与设备重试分开。** 临时模型网络错误可在动作尚未执行时有界重试；HID 已发出而回报丢失时先检查现场，不重放发送。用 task/step/action/before-frame/after-frame 关联事实，便于分清识别错、决策错、执行偏差和验收错。[RetryPolicy](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/agent/RetryPolicy.kt#L7)、[RunEvent](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/record/RunEvent.kt#L40)

5. **保留 App 语义知识，不缓存固定坐标。** Mika 的微信说明提醒模型：搜索混有联系人/群/公众号等，进入会话后标题才是对象，输入框文字只是草稿。这类短知识可以加入观察证据规则，布局仍以当前画面为准。[微信知识](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/resources/knowledge/apps/wechat/APP.md)

6. **阶段与接管可见。** 区分运行、等待回答、等待手机接管、等待确认；停止后说明做到了哪。接管后重新观察，不能从持久化旧画面中自动恢复点击。圆屏可显示“找到微信 → 正在点击 → 正在核实”，与识别原文、解析指令并列。[TaskState](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/agent/TaskState.kt#L19)、[RunRecovery](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/task/RunRecovery.kt#L29)

## 不照搬的策略

- Mika 用粗格子亮度差作为效果旁证：24×48 格、亮度差大于 16、整屏 1% 或 ROI 15% 表示可见变化；另有 OCR 等待和 aHash 检查。没有专门识别时钟。它们不能替代我们的点击前目标有效性和发送检查，也不能证明点击落点。[FrameGrid](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/visual/FrameGrid.kt)、[FrameTuning](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/visual/FrameTuning.kt)
- `OperationGuards.beforeAction()` 当前直接返回空列表，注释里的重复动作签名阶梯没有启用。实际仍有无效果、连续失败和无新内容重读反馈；`no_new_state` 只提醒。正常连续翻页可能是进展，不能只因动作名称相同就判循环。[OperationGuards](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/guard/OperationGuards.kt#L114)
- 产品路径设 `reviewFinalAnswer=false`，正常任务默认不限步数。存在 Verifier 文件不代表终局独立核查已启用。我们保留 10 个物理动作上限和目标完成证据。[TaskRun](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/task/TaskRun.kt#L793)
- Mika Ask 模式默认只对花钱询问，自主模式更宽。我们仍保持 App 打开/翻页无需确认、发送需要具体收件人和内容的一次性确认、付款删除不支持。[模式说明](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/docs/modes.md)
- 普通运行记录和详细模型 trace 不同；后者仅 DEBUG/perf、默认关闭。我们默认仍不保存原始录音/截图，调试图像记录需要显式开启。[DetailedTraces](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/app/src/main/java/com/wangzan101/mika/record/DetailedTraces.kt#L9)

## 如以后扩展 Android

圆屏可将任务通过局域网提交给手机 Mika 的 `POST /api/v1/tasks`，再查询/等待/取消；也可由 Mac 通过 MCP 的 `mika_run_task` 等接口接入。默认端口 8765，网络接入默认关闭，Bearer 鉴权；USB 回环和 LAN token 分开。它主要对外提供任务接口，不是公开任意 `/tap` 服务。手机接管不能用普通 `/answer` 替代。这是另一个执行后端，不改变当前 iPhone 优先的范围。[RemoteTaskApi](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/remote/RemoteTaskApi.kt#L43)、[RemoteTaskService](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/core/src/main/kotlin/com/wangzan101/mika/core/remote/RemoteTaskService.kt#L73)

## 当前 iPhone 的实施与验收顺序

1. 专用空白校准页先证明 HID 点击可被公开触控 API 收到，核对屏幕点坐标与事件类型。探测编号单次有效，超时、重复、取消、切后台、共享停止和几何变化均停止；不回退灰圈。
2. 五点拟合加独立验证，通过后缓存。同一有效会话后续任务复用，不能每次完整校准。其他 App 内 `landing_observed=false`，只能说使用已验证映射。
3. 打开微信：看到图标则点击；没看到时仅在已确认主屏上翻页/搜索；每个动作后取新帧，核实微信实际界面。误开其他 App 不算成功，也不沿用原坐标继续点。
4. 加入结果分类和有限重看，以及可关联阶段诊断。先用测试覆盖时钟刷新、真实翻页、目标小范围变化、延迟效果、缓存帧重复、缺帧和执行失败，再真机验收。
5. 输入的目标文字与画面核对通过后再允许发送确认；默认不保存原图/录音，10 步和发送授权不变。

本次只新增研究与证据更正文档，没有修改服务/iOS/固件代码，未重启或刷写。真实点击校准和打开微信仍未通过。Mika 为 [Apache-2.0](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/LICENSE)，附 [NOTICE](https://github.com/wangzan101/Mika/blob/4ce3ca67e8cf8af8323a4d74505f51fd94178137/NOTICE)；本次没有拷贝上游实现，后续如引入代码需保留相应许可与归属。


后续实施：已先完成原生触控校准的代码、映射复用和新帧检查，见 [native-calibration.md](native-calibration.md)。Mika 的完整结果分类、有限重看和阶段诊断仍按顺序待后续实施；不能把本阶段代码/编译通过当作真实打开微信已通过。
