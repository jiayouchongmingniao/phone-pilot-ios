# iPhone 局域网后台探针

这是独立开发验证，不接收屏幕像素或录音，不调用模型，也不发送 USB HID 操作。不要在公共网络运行。手机和测试客户端须位于能互访的同一 Wi-Fi。

1. 在 App 的「开发验证 → iPhone 局域网后台探针」启动探针，选择整个屏幕。查找 iPhone 的 Wi-Fi 地址并复制页面显示的测试码。
2. 在另一设备上的终端中，为避免把测试码放进命令历史，交互式输入测试码：

   ```sh
   read -r 'PHONE_PROBE_CODE?测试码: '
   curl --max-time 12 -H "X-Probe-Code: $PHONE_PROBE_CODE" "http://<iPhone-IP>:8789/probe"
   ```

3. iPhone 切到主屏或另一个 App，再多次请求。比较 `frames`、`frame_age_ms`、`https_status`/`https_error` 和 `https_ms`。`frame_age_ms` 为 -1 表示尚无采样帧；小帧龄和递增帧数才说明抓屏回调正在更新。`https_status: 200` 仅证明访问 Apple 测试地址成功，不代表任何云模型可用。
4. 单独记录前台、主屏、其他 App、锁屏、Wi-Fi 中断和恢复时的结果及持续时间。出现中断后不得推断 App 会自动恢复。完成后返回探针页面并停止。

请求和响应走明文 HTTP，测试码每次启动重新生成，只用于开发，不作为产品级配对或加密。探针不暴露实际设备协议，也不能证明圆屏或 USB HID 可用。正在使用真机测试的其他会话有优先权，避免同时占用。

本机可运行网络协议烟测：

```sh
swiftc -swift-version 6 Sources/LocalProbeServer.swift tests/ProbeHarness.swift -o /tmp/phone-pilot-probe-test
python3 tests/test_probe.py /tmp/phone-pilot-probe-test
```

这些是 macOS 测试，不能代替 iOS 编译或真机后台测试。
