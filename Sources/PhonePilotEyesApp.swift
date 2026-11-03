import SwiftUI

@main
struct PhonePilotEyesApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @StateObject private var capture = ScreenCapture()
    @AppStorage("serverAddress") private var address = "http://"
    @State private var code = ""
    @State private var showCalibration = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac 协调端") {
                    TextField("例如 http://192.168.1.2:8788", text: $address)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("6 位配对码", text: $code).keyboardType(.numberPad)
                    Button("连接并选择整个屏幕") { Task { await capture.connect(address: address, code: code) } }
                        .disabled(capture.running || capture.connecting)
                    Button("停止共享", role: .destructive) { Task { await capture.stop() } }
                        .disabled(!capture.running && !capture.connecting)
                }
                Section("状态") {
                    Text(capture.status)
                    Text("已发送 \(capture.sent) 帧")
                    if let error = capture.error { Text(error).foregroundStyle(.red) }
                }
                Section("指针校准") {
                    Button("打开安全校准页") { showCalibration = true }
                        .disabled(!capture.running)
                    Text("在空白页测量真实点击位置。校准时请勿触碰屏幕，保持竖屏和屏幕共享。")
                        .font(.footnote)
                }
                Section {
                    Text("画面通过当前 Wi-Fi 发给 Mac，任务所需画面会发送给 Anthropic。请只在可信网络使用。")
                        .font(.footnote)
                }
            }.navigationTitle("手机遥控")
        }
        .fullScreenCover(isPresented: $showCalibration) {
            TouchCalibrationView(capture: capture)
        }
    }
}
