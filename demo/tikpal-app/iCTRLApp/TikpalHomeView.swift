import SwiftUI
import UIKit

/// The one screen most people need: after a reboot, one button brings everything back. The upstream screens stay
/// behind the gear, for the first pairing file and for looking closer.
struct TikpalHomeView: View {
    private enum Step: Equatable {
        case idle, tunnel, mounting, starting
        case failed(String)
    }

    private static let runnerURL = URL(string: "ictrl://start?callback=ictrl-app://started")!

    @EnvironmentObject private var appState: AppState
    @StateObject private var mounter = DDIMounter()
    @StateObject private var runner = RunnerStatus(port: 8200)
    @State private var step = Step.idle
    @State private var mounted = DeveloperDiskImage.status().isMounted
    @State private var hasPairingFile = PairingFileStore.exists
    @State private var showSetup = false

    private var busy: Bool { step == .tunnel || step == .mounting || step == .starting }
    private var ready: Bool { mounted && runner.isRunning }

    private var message: String {
        switch step {
        case .tunnel: return "正在接通"
        case .mounting: return "正在准备手机"
        case .starting: return "正在启动"
        case .failed(let why): return why
        case .idle: break
        }
        if ready { return "已就绪，可以用圆屏了" }
        if !hasPairingFile { return "还缺配对文件，只需设置一次" }
        return mounted ? "服务没有运行" : "手机重启过，需要恢复一下"
    }

    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: ready ? "checkmark.circle.fill" : "arrow.clockwise.circle.fill")
                .font(.system(size: 72))
                .foregroundColor(ready ? .green : .orange)
            Text(message)
                .font(.title3)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            if busy {
                ProgressView()
            } else if !hasPairingFile {
                Button("导入配对文件") { showSetup = true }
                    .buttonStyle(.borderedProminent)
            } else if !ready {
                Button("恢复") { Task { await restore() } }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
            Spacer()
        }
        .navigationTitle("Tikpal")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                NavigationLink {
                    SettingsView().environmentObject(appState)
                } label: {
                    Image(systemName: "gearshape")
                }
            }
        }
        .onAppear(perform: refresh)
        .onDisappear { runner.stopPolling() }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in refresh() }
        .onReceive(NotificationCenter.default.publisher(for: PairingFileStore.didImport)) { _ in hasPairingFile = true }
        .onOpenURL { url in
            // The runner bounced back. The probe below says whether it really came up.
            guard url.scheme == "ictrl-app" else { return }
            Task {
                await runner.check()
                step = runner.isRunning ? .idle : .failed("服务没有启动，请再试一次")
            }
        }
        .sheet(isPresented: $showSetup, onDismiss: refresh) { DDIMountView() }
    }

    private func refresh() {
        mounted = DeveloperDiskImage.status().isMounted
        hasPairingFile = PairingFileStore.exists
        runner.startPolling()
    }

    private func restore() async {
        if !DeveloperDiskImage.status().isMounted {
            guard DeveloperDiskImageStore.isComplete else {
                step = .failed("缺少系统镜像，请到设置里下载")
                return
            }
            step = .tunnel
            do {
                try await InAppTunnel.start()
            } catch {
                step = .failed(error.localizedDescription)
                await InAppTunnel.stop()
                return
            }
            step = .mounting
            await mounter.mount()
            await InAppTunnel.stop()
            if case .failed(let why) = mounter.phase {
                step = .failed(why)
                return
            }
            mounted = DeveloperDiskImage.status().isMounted
        }
        step = .starting
        UIApplication.shared.open(Self.runnerURL) { opened in
            if !opened { step = .failed("没有找到 Tikpal Runner，请重新安装") }
        }
    }
}
