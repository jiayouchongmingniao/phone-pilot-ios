import SwiftUI
import UIKit

/// The one screen most people need: one button starts the service, whatever a reboot took away, and the same button
/// stops it. The upstream screens stay behind the gear, for the first pairing file and for looking closer.
struct TikpalHomeView: View {
    private enum Step: Equatable {
        case idle, tunnel, mounting, starting, stopping, allowing
        case failed(String)
        case notStopped(String)
    }

    private static let runnerURL = URL(string: "ictrl://start?callback=ictrl-app://started")!
    /// The runner normally calls back well before this. Past it the button comes back, so a retry is possible.
    private static let startTimeoutSeconds: UInt64 = 20

    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var runnerAdmin: RunnerAdmin
    @StateObject private var mounter = DDIMounter()
    @StateObject private var runner = RunnerStatus(port: 8200)
    @State private var step = Step.idle
    @State private var mounted = DeveloperDiskImage.status().isMounted
    @State private var hasPairingFile = PairingFileStore.exists
    @State private var showSetup = false

    private var probing: Bool {
        switch runner.state {
        case .unknown, .checking: return true
        case .running, .stopped: return false
        }
    }

    private var busy: Bool {
        switch step {
        case .tunnel, .mounting, .stopping, .allowing: return true
        case .starting: return !runner.isRunning
        case .idle, .failed, .notStopped: return probing
        }
    }

    private var message: String {
        switch step {
        case .tunnel: return "正在接通"
        case .mounting: return "正在准备手机"
        case .starting where !runner.isRunning: return "正在启动"
        case .stopping: return "正在停止"
        case .allowing: return "请长按手机上的授权通知，点“允许”\n只需一次，以后一点就停"
        case .notStopped(let why) where runner.isRunning: return why
        case .failed(let why) where !runner.isRunning: return why
        default: break
        }
        if probing { return "正在检查" }
        if runner.isRunning { return "已就绪，可以用圆屏了" }
        if !hasPairingFile { return "还缺配对文件，只需设置一次" }
        return mounted ? "服务没有运行" : "手机重启过，点“开始服务”恢复"
    }

    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: runner.isRunning ? "checkmark.circle.fill" : "power.circle.fill")
                .font(.system(size: 72))
                .foregroundColor(runner.isRunning ? .green : .orange)
            Text(message)
                .font(.title3)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            if busy {
                ProgressView()
            } else if runner.isRunning {
                Button("停止服务", role: .destructive) { Task { await stop() } }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
            } else if !hasPairingFile {
                Button("导入配对文件") { showSetup = true }
                    .buttonStyle(.borderedProminent)
            } else {
                Button("开始服务") { Task { await start() } }
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
                if url.host == "started" {
                    step = runner.isRunning ? .idle : .failed("服务没有启动，请再试一次")
                }
            }
        }
        .sheet(isPresented: $showSetup, onDismiss: refresh) { DDIMountView() }
    }

    private func refresh() {
        mounted = DeveloperDiskImage.status().isMounted
        hasPairingFile = PairingFileStore.exists
        runner.startPolling()
    }

    private func start() async {
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
        try? await Task.sleep(nanoseconds: Self.startTimeoutSeconds * 1_000_000_000)
        if step == .starting && !runner.isRunning {
            step = .failed("服务没有启动，请再试一次")
        }
    }

    /// The stop goes over the channel only a paired app has, so nothing else on the phone can stop the service. Pairing
    /// takes one approval on the phone; the runner keeps the key across restarts. A key it has since rejected costs one
    /// more approval, hence the second round.
    private func stop() async {
        for _ in 0..<2 {
            if !runnerAdmin.isPaired {
                step = .allowing
                await runnerAdmin.connect()
                guard runnerAdmin.isPaired else {
                    step = .notStopped(runnerAdmin.notice ?? "没有允许，服务还在运行")
                    return
                }
            }
            step = .stopping
            if await runnerAdmin.stopRunner() {
                // Give the runner a moment to close its port, or the probe still finds it.
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                await runner.check()
                step = .idle
                return
            }
        }
        step = .notStopped(runnerAdmin.notice ?? "没有停下来，请再试一次")
    }
}
