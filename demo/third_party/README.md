# 指针校准参考与许可

本 Demo 的指针闭环参考以下项目；以下两份许可证随本地改动保留。

- **[ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent)**，Henry Yuan，MIT，参考提交 `d986bedb9809cabd9c8148a584bab3b5110ad75c`。参考 `ios/IPhoneAgent/Agent/Pointer.swift` 和 `App/AutoCalibrator.swift` 的“移动—观测—纠偏—点击”流程。完整许可见 [ESP32-iPhone-Agent.LICENSE](ESP32-iPhone-Agent.LICENSE)。
- **[Open iPhone Use](https://github.com/semsturgut/open-iphone-use)**，Apache-2.0，参考提交 `4a0bbdc9baf12b96524d64c5151081426b2a3a02`。参考 `server/oiu/vision.py` 的灰色圆形指针检测、`calibration.py` 的分轴线性拟合，以及 `control.py` 的分轴定步长移动。完整许可见 [Open-iPhone-Use.LICENSE](Open-iPhone-Use.LICENSE)。

2026-11-03 本地改写在 `demo/pointer.py`、`demo/server.py` 及相邻圆屏仓库的 `main/keys.c`、`main/demo.c`：保留现有 ScreenCaptureKit 与 USB 生命周期；采用五次无点击探测、JPEG 像素到屏幕点坐标映射、逐帧时序校验和最多两次纠偏。首次校准后复用数值参数，后续直接定位并核对一张新画面。无法识别指针时停止，不采用预测落点直接点击的后备路径。没有引入这些项目的 AirPlay、蓝牙或其他传输实现。

此说明表示来源与改动记录，不表示上游作者认可本 Demo。当前实现仍待 iPhone 真机校准验收。

## iCTRL

`ictrl_rpc.py` 和 `sign-ipa.sh` 是 [OpenMinis/iCTRL](https://github.com/OpenMinis/iCTRL) 提交 `c1e4268859ba858d1579f7b846a56cb40641b7f6`（v1.98.16）的 `Scripts/` 下同名文件，Apache-2.0，未修改。完整许可见 [iCTRL.LICENSE](iCTRL.LICENSE)。`demo/ictrl_driver.py` 是本地编写的调用层，运行时需要手机上另装 iCTRL runner，未引入其他上游代码。

`sign-ipa.sh` 为 Tikpal 版加了 `-x` 选项：给 App 里的 `.appex` 单独放描述文件，并给 App 和扩展加 Network Extension 权限。
