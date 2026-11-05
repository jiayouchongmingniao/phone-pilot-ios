# ESP32 iPhone Agent 调研

2026-11-03。只读核对上游当前提交 [d986bedb](https://github.com/onehumanbeing/esp32-iphone-agent/commit/d986bedb9809cabd9c8148a584bab3b5110ad75c)，三项并行审阅分别覆盖任务循环、画面稳定性、产品反馈。以下是源码已实现机制与本项目建议，不代表已移植或真机通过。

## 优先借鉴

| 能力 | 上游实现 | Phone Pilot 的落点 |
|---|---|---|
| 每步效果和进度 | 下一轮携带近期动作及画面是否变化；重复无效动作触发重新判断 | 明确区分 HID 已执行、结果待验证、目标已满足、结果未知；现有 Jev 完成证据继续保留 |
| 暂停、接管、继续 | 失败后可由用户处理，再从当前画面重新规划；每次任务最多求助两次 | 将 `assist` 从一律结束扩展为有界暂停；继续不能替代发送确认，动作总数仍最多 10 |
| 故障分层恢复 | 分别报告设备、热点和镜像中断；恢复后取新帧、释放按键、归位 | 对应圆屏离线、手机共享中断、模型故障；旧坐标/候选失效，已发出但结果未知的发送不重放 |
| 操作回放与纠错 | 截图叠加点击/轨迹，可标记对错、纠正坐标、备注、导出 ZIP | 先做用户显式开启的单次调试记录，关联 before/after 帧、目标点、实测点、执行回报与独立验收；默认仍不保存原始截图/音频 |
| 清楚的阶段反馈 | 区分等待、观察、规划、思考、动作、重试、求助 | 在现有“识别/指令”下展示等待画面、识图、Jev 决策、定位、结果验证；优先于动画或灵动岛 |
| 短任务计划与保存任务 | Planner 生成最多 6 步清单，支持 saved task/hints，Siri/Shortcuts 多入口 | 先稳定现有操作，再扩展本地允许动作组成的短任务；Claude 提供解释与证据，Jev 保持唯一决策者 |

源码：[AgentLoop](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/Agent/AgentLoop.swift)、[Recovery](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/Agent/Recovery.swift)、[ReviewModel](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/Review/ReviewModel.swift)、[IslandStatus](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/App/IslandStatus.swift)、[AgentShortcuts](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/App/AgentShortcuts.swift)。

## 不能直接照搬的边界

- 上游 `changed` 只是像素反馈，`done` 仍是模型声明；缺图或解码失败时 `Vision.changed` 返回 true。不能把它当作操作成功或发送成功证明。本项目已有执行后新帧、App 前台证据、发送结果证据，应保留。
- 上游 `settled` 利用 AirPlay 在画面变化时送帧的特点，观察到帧静默和传输时延。我们每秒固定上传 JPEG，不能直接照搬“没有新帧就是稳定”的条件。即使连续帧稳定，也不代表与原始决策画面一致。
- 多动作批次已部分实现，最多六条；`tap → type/key` 有中途检查，但未覆盖所有依赖。上游默认 40 指的是循环轮数，不能替代本项目 10 个物理动作上限，也不应先照搬批量发送。
- 回放导出没有完整的动作后帧关联、稳定源 step ID、完整系统提示与失败尝试；`changed` 在写 trace 后才更新内存。导出不是可复现运行或独立验收。新调试记录应补齐关联，不能仅复制文件格式。
- 无 Mac 架构涉及 ESP32 AirPlay、BLE 回传同一 iPhone 解码、板载热点和蜂窝访问模型，是另一个部署方案。当前 USB 执行器与 ScreenCaptureKit 可继续保留。其 ASCII 输入和横屏限制也不替我们解决中文输入。
- README 仍提私有 `suspend`，但当前 `AppPresence.leaveApp` 已采用 HID cmd+h、home swipe 或提示手动返回主屏；文档和源码有滞后。

证据：[Vision](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/Agent/Vision.swift)、[VideoFeed](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/Video/VideoFeed.swift)、[导出限制](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/docs/run-data-export.md#limits-relevant-to-analysis)、[AppPresence](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/App/AppPresence.swift)。

## 当前校准误判

真实诊断见 [validation.md](validation.md)。用户随后明确确认当前录屏不带指针。因此此前检测器给出的 `[84,111]` 只是误认的候选图形，不能称为实际落点；基于该坐标作出的“小组件悬停”解释撤回。数值差异只说明画面像素变化，不能证明指针移动到了哪里或用户换页。

Apple 的 `SCStreamConfiguration` 类支持 iOS 27，但 [`showsCursor`](https://developer.apple.com/tutorials/data/documentation/screencapturekit/scstreamconfiguration/showscursor.json) 和 [`showMouseClicks`](https://developer.apple.com/tutorials/data/documentation/screencapturekit/scstreamconfiguration/showmouseclicks.json) 的平台 metadata 仅列 macOS / Mac Catalyst。不能在原生 iOS 上加一个这类开关就声称解决。

可借鉴上游 [TouchProbe](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/App/TouchProbe.swift#L95) 和 [TouchCalibration](https://github.com/onehumanbeing/esp32-iphone-agent/blob/d986bedb9809cabd9c8148a584bab3b5110ad75c/ios/IPhoneAgent/App/TouchCalibration.swift)：在自己的全屏空白页收到真实 `UITouch`，转换成屏幕点坐标，按探测编号关联已发 HID 点击。五点拟合后必须另用未参与拟合的点验收，全部通过才缓存；不能照搬上游验证前存储、缺验证回报仍可能成功或回退视觉识别的路径。首次先实测 AssistiveTouch 点击能否收到、坐标和事件类型是否正确。离开本 App 后不能接收其他 App 的触控；后续用已校准映射执行，并从新画面核实操作结果，不宣称每次落点已观测。

已有有界等待和灰圈代码仍在，尚未实现触控校准替换、暂停恢复或回放。此前 81 项本地测试、固件构建和安装是历史自动化/部署证据；合成灰圈测试不验证真实采集链路。本次仅更正文档及增加 [Mika 源码调研](mika-review.md)，未运行上游测试或修改执行代码。真实校准、打开微信及本轮新固件正常退出恢复仍未验收。


后续实施：原生触控校准三端代码和本地协议测试已完成，生产路径已切换为 `native_touch_v1`，不再调用灰圈校准。进度及真机边界见 [native-calibration.md](native-calibration.md)；本文上面的“尚未实现”是调研时点记录。
