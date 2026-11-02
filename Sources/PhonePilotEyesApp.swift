import SwiftUI

/// Phone Pilot's iPhone side. For now a placeholder that proves the pipeline: built on GitHub Actions with Xcode 27,
/// signed with an App Store Connect API key and installed through TestFlight.
@main
struct PhonePilotEyesApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}

struct ContentView: View {
    private var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "iphone.gen3")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("手机遥控")
                .font(.title.bold())
            Text("Yay 圆屏的 iPhone 端。这一版只用来验证云端编译和 TestFlight 安装。")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Text("版本 \(version)")
                .font(.footnote.monospacedDigit())
                .foregroundStyle(.tertiary)
        }
        .padding(32)
    }
}
