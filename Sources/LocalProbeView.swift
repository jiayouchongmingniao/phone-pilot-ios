import SwiftUI

struct LocalProbeView: View {
    @StateObject private var capture = ScreenCapture()
    @StateObject private var server = LocalProbeServer()

    var body: some View {
        Form {
            Section("后台通信验证") {
                Button("启动探针并选择整个屏幕") {
                    server.start {
                        ["capturing": capture.running,
                         "frames": capture.sent,
                         "frame_age_ms": capture.probeFrameTime.map { Int(Date().timeIntervalSince($0) * 1000) } ?? -1]
                    }
                    capture.startProbe()
                }.disabled(capture.running || capture.connecting)
                Button("停止", role: .destructive) {
                    server.stop()
                    Task { await capture.stop() }
                }
                Text(server.status)
                Text("抓屏：\(capture.status)；采样帧数：\(capture.sent)")
                Text("已收到请求：\(server.requests)")
                if let error = capture.error { Text(error).foregroundStyle(.red) }
            }
            if !server.code.isEmpty {
                Section("测试连接") {
                    Text("测试码：\(server.code)").textSelection(.enabled)
                    Text("查询 iPhone Wi‑Fi 地址，从同一网络请求端口 8789 的 /probe，并在 X-Probe-Code 请求头中填写测试码。")
                    Text("接口只返回帧计数、帧龄和 iPhone 到 Apple 测试地址的 HTTPS 结果；不读取或发送画面，不执行操作。仅用于可信 Wi‑Fi。")
                        .font(.footnote)
                }
            }
        }
        .navigationTitle("局域网探针")
        .onDisappear {
            server.stop()
            Task { await capture.stop() }
        }
    }
}
