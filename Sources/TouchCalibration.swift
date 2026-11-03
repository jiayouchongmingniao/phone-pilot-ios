import SwiftUI
import UIKit

struct CalibrationReply: Decodable, Sendable {
    let id: String?
    let phase: String?
    let probe: String?
    let step: Int?
    let total: Int?
    let error: String?
}

struct CalibrationFailure: LocalizedError, Sendable {
    let message: String
    var errorDescription: String? { message }
}

struct CalibrationPoint: Sendable {
    let id: String
    let page: String
    let probe: String
    let x: Double
    let y: Double
    let touchType: String
}

private struct CalibrationArm {
    let id: String
    let page: String
    let probe: String
    let uptime: TimeInterval
}

@MainActor
struct TouchCalibrationView: View {
    @ObservedObject private var capture: ScreenCapture
    @StateObject private var session: TouchCalibrationSession
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    init(capture: ScreenCapture) {
        self.capture = capture
        _session = StateObject(wrappedValue: TouchCalibrationSession(capture: capture))
    }

    var body: some View {
        ZStack {
            CalibrationSurfaceView(session: session).ignoresSafeArea()
            VStack(spacing: 12) {
                VStack(spacing: 12) {
                    Text("指针校准").font(.title2.bold())
                    Text(session.status).font(.headline)
                    Text("请勿触碰屏幕。圆屏会自动移动并点击；保持竖屏。")
                        .font(.subheadline).multilineTextAlignment(.center)
                    if let touchType = session.lastTouchType {
                        Text("收到的触控类型：\(touchType)").font(.caption)
                    }
                    if let error = session.error {
                        Text(error).foregroundStyle(.red).font(.subheadline)
                    }
                }
                .allowsHitTesting(false)
                Spacer()
                if !session.started {
                    Button("开始校准") { session.start() }
                        .buttonStyle(.borderedProminent)
                        .disabled(!session.geometryOK || !capture.running || scenePhase != .active)
                }
                Button(session.running ? "取消校准" : "关闭") {
                    session.leave()
                    dismiss()
                }
                .buttonStyle(.bordered)
                .frame(height: 44)
            }
            .padding(.horizontal, 24)
            .padding(.top, 24)
            .padding(.bottom, 4)
        }
        .background(.white)
        .foregroundStyle(.black)
        .interactiveDismissDisabled()
        .onAppear { session.appear(active: scenePhase == .active) }
        .onDisappear { session.leave() }
        .onChange(of: scenePhase) { _, value in session.setActive(value == .active) }
        .onChange(of: capture.running) { _, value in
            if !value { session.cancel("屏幕共享已停止，校准已取消。") }
        }
    }
}

@MainActor
private final class TouchCalibrationSession: ObservableObject {
    @Published private(set) var status = "正在检查全屏尺寸"
    @Published private(set) var error: String?
    @Published private(set) var running = false
    @Published private(set) var started = false
    @Published private(set) var geometryOK = false
    @Published private(set) var lastTouchType: String?

    private let capture: ScreenCapture
    private let page = UUID().uuidString
    private weak var surface: CalibrationSurface?
    private var worker: Task<Void, Never>?
    private var active = false
    private var visible = false
    private var previousIdleSetting: Bool?
    private var id: String?
    private var probe: String?
    private var arm: CalibrationArm?
    private var point: CalibrationPoint?
    private var submittedProbe: String?

    init(capture: ScreenCapture) { self.capture = capture }

    func attach(_ surface: CalibrationSurface) { self.surface = surface }

    func appear(active: Bool) {
        visible = true
        self.active = active
        if active { keepAwake() }
    }

    func leave() {
        visible = false
        cancel(nil)
        restoreIdleTimer()
    }

    func setActive(_ active: Bool) {
        self.active = active
        if !active {
            cancel("页面已离开前台，校准已取消。")
            restoreIdleTimer()
        } else if visible, !started { keepAwake() }
    }

    func geometryChanged(valid: Bool) {
        geometryOK = valid
        if !valid {
            if running { cancel("屏幕尺寸或方向已改变，校准已取消。") }
            else { status = "需要全屏竖屏，屏幕尺寸为 390 × 844 点" }
        } else if !started { status = "准备就绪后点开始" }
    }

    func start() {
        guard !started, worker == nil, visible, active, geometryOK, capture.running else { return }
        started = true; running = true; error = nil
        worker = Task { await run() }
    }

    func cancel(_ message: String?) {
        disarm()
        if let message, started, running { error = message }
        running = false
        worker?.cancel()
        restoreIdleTimer()
    }

    func received(_ point: CalibrationPoint) {
        guard running, active, visible, geometryOK, capture.running,
              let arm, point.id == arm.id, point.id == id,
              point.page == arm.page, point.probe == arm.probe,
              ProcessInfo.processInfo.systemUptime - arm.uptime <= 2 else { return }
        self.point = point
        lastTouchType = point.touchType
    }

    func rejected(_ reason: String) { if running { cancel(reason) } }

    private func disarm() {
        surface?.disarm()
        arm = nil
        point = nil
        submittedProbe = nil
    }

    private func restoreIdleTimer() {
        if let previousIdleSetting {
            UIApplication.shared.isIdleTimerDisabled = previousIdleSetting
            self.previousIdleSetting = nil
        }
    }

    private func keepAwake() {
        if previousIdleSetting == nil {
            previousIdleSetting = UIApplication.shared.isIdleTimerDisabled
            UIApplication.shared.isIdleTimerDisabled = true
        }
    }

    private func checkPage() throws {
        try Task.checkCancellation()
        guard running, active, visible, geometryOK, capture.running,
              UIApplication.shared.applicationState == .active else {
            throw CalibrationFailure(message: "校准页面或屏幕共享已不可用。")
        }
        if let arm, point == nil, ProcessInfo.processInfo.systemUptime - arm.uptime > 2 {
            throw CalibrationFailure(message: "本次点击等待超时，校准已停止。")
        }
    }

    private func request(_ op: String, point: CalibrationPoint? = nil) async throws -> CalibrationReply {
        try await capture.calibrationRequest(op: op, page: page, id: id, probe: probe, point: point)
    }

    private func run() async {
        var completed = false
        do {
            try checkPage()
            var reply = try await request("begin")
            id = reply.id
            guard let id, !id.isEmpty else { throw CalibrationFailure(message: reply.error ?? "Mac 没有返回校准编号。") }
            while true {
                try checkPage()
                guard reply.id == id else { throw CalibrationFailure(message: "校准编号已改变。") }
                if let message = reply.error { throw CalibrationFailure(message: message) }
                if reply.phase == "done" {
                    completed = true; status = "校准完成"; break
                }
                if reply.phase == "failed" || reply.phase == "cancelled" {
                    throw CalibrationFailure(message: "校准未完成，请重新打开校准页。")
                }
                guard let nextProbe = reply.probe, !nextProbe.isEmpty else {
                    throw CalibrationFailure(message: "Mac 没有返回探针编号。")
                }
                if probe != nextProbe { disarm(); probe = nextProbe }
                let progress = "第 \(reply.step ?? 0) / \(reply.total ?? 0) 点"
                switch reply.phase {
                case "move":
                    guard arm == nil, submittedProbe == nil else { throw CalibrationFailure(message: "校准阶段已改变。") }
                    status = "\(progress) · 正在移动指针"
                case "arm":
                    guard submittedProbe == nil else { throw CalibrationFailure(message: "已提交的探针不能重新接收点击。") }
                    status = "\(progress) · 准备接收点击"
                    if arm == nil {
                        let nextArm = CalibrationArm(id: id, page: page, probe: nextProbe, uptime: ProcessInfo.processInfo.systemUptime)
                        if surface?.arm(nextArm) == true {
                            arm = nextArm
                            reply = try await request("armed")
                            continue
                        }
                    }
                case "click":
                    if submittedProbe == nextProbe {
                        status = "\(progress) · 已收到点击，等待确认"
                        break
                    }
                    guard arm?.probe == nextProbe else { throw CalibrationFailure(message: "未启用的探针收到点击请求。") }
                    status = "\(progress) · 等待点击，请勿碰屏幕"
                    if let point {
                        guard point.id == id, point.page == page, point.probe == nextProbe else {
                            throw CalibrationFailure(message: "点击不属于当前探针。")
                        }
                        disarm()
                        submittedProbe = nextProbe
                        reply = try await request("point", point: point)
                        continue
                    }
                default:
                    throw CalibrationFailure(message: "Mac 返回了未知校准阶段。")
                }
                try await Task.sleep(for: .milliseconds(250))
                try checkPage()
                reply = try await request("poll")
            }
        } catch is CancellationError {
            if error == nil { status = "校准已取消" }
        } catch {
            self.error = error.localizedDescription
            status = "校准已停止"
        }
        running = false
        disarm()
        restoreIdleTimer()
        if !completed {
            // The poll has already unwound. This fresh task only sends the final cancellation,
            // so cancellation of the polling task cannot suppress cleanup or overlap requests.
            await Task { @MainActor in _ = try? await request("cancel") }.value
        }
        worker = nil
    }
}

@MainActor
private struct CalibrationSurfaceView: UIViewRepresentable {
    let session: TouchCalibrationSession

    func makeUIView(context: Context) -> CalibrationSurface {
        let view = CalibrationSurface()
        view.backgroundColor = .white
        view.isMultipleTouchEnabled = true
        view.session = session
        session.attach(view)
        return view
    }

    func updateUIView(_ uiView: CalibrationSurface, context: Context) {}
}

// Observe the whole window, including the SwiftUI controls above the measurement view.
// This recognizer never recognizes or prevents another gesture; it only counts held touches.
@MainActor
private final class CalibrationTouchTracker: UIGestureRecognizer {
    private var touches = Set<ObjectIdentifier>()
    var isIdle: Bool { touches.isEmpty }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        touches.forEach { self.touches.insert(ObjectIdentifier($0)) }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {}

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        finish(touches)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        finish(touches)
    }

    private func finish(_ touches: Set<UITouch>) {
        touches.forEach { self.touches.remove(ObjectIdentifier($0)) }
        if self.touches.isEmpty { state = .failed }
    }

    override func reset() {
        super.reset()
        touches.removeAll()
    }

    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool { false }
    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool { false }
}

@MainActor
private final class CalibrationSurface: UIView {
    weak var session: TouchCalibrationSession?
    private var activeTouches = Set<ObjectIdentifier>()
    private var armed: CalibrationArm?
    private var consumed = false
    private var candidate: (touch: ObjectIdentifier, arm: CalibrationArm, start: CGPoint, type: String)?
    private var reportedGeometry: Bool?
    private weak var monitoredWindow: UIWindow?
    private var touchTracker: CalibrationTouchTracker?

    override func layoutSubviews() {
        super.layoutSubviews()
        reportGeometry()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if monitoredWindow !== window {
            if let touchTracker { monitoredWindow?.removeGestureRecognizer(touchTracker) }
            touchTracker = nil
            monitoredWindow = window
            if let window {
                let tracker = CalibrationTouchTracker(target: nil, action: nil)
                tracker.cancelsTouchesInView = false
                tracker.delaysTouchesBegan = false
                tracker.delaysTouchesEnded = false
                tracker.requiresExclusiveTouchType = false
                window.addGestureRecognizer(tracker)
                touchTracker = tracker
            }
        }
        reportGeometry()
    }

    private func reportGeometry() {
        guard let scene = window?.windowScene else { return }
        let rect = convert(bounds, to: scene.screen.coordinateSpace)
        let screen = scene.screen.bounds
        let valid = scene.interfaceOrientation == .portrait && screen.width == 390 && screen.height == 844 &&
            abs(rect.minX) < 0.5 && abs(rect.minY) < 0.5 && abs(rect.width - 390) < 0.5 && abs(rect.height - 844) < 0.5
        guard reportedGeometry != valid else { return }
        reportedGeometry = valid
        // Avoid publishing ObservableObject changes during SwiftUI's layout/update pass.
        Task { @MainActor [weak self] in self?.session?.geometryChanged(valid: valid) }
    }

    func arm(_ arm: CalibrationArm) -> Bool {
        guard touchTracker?.isIdle == true, activeTouches.isEmpty, candidate == nil,
              reportedGeometry == true else { return false }
        armed = arm
        return true
    }

    func disarm() { armed = nil; candidate = nil; consumed = false }

    private func screenPoint(_ touch: UITouch) -> CGPoint? {
        guard let screen = window?.windowScene?.screen else { return nil }
        return convert(touch.location(in: self), to: screen.coordinateSpace)
    }

    private func reject(_ message: String) {
        disarm()
        session?.rejected(message)
    }

    private func hasOtherActiveTouch(in event: UIEvent?, excluding touch: UITouch) -> Bool {
        event?.allTouches?.contains {
            $0 !== touch && $0.phase != .ended && $0.phase != .cancelled
        } == true
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        touches.forEach { activeTouches.insert(ObjectIdentifier($0)) }
        if consumed {
            reject("检测到多点或重复触摸，校准已停止。")
            return
        }
        guard let armed else { return }
        guard touches.count == 1, activeTouches.count == 1, let touch = touches.first,
              !hasOtherActiveTouch(in: event, excluding: touch),
              let point = screenPoint(touch), touch.timestamp >= armed.uptime,
              touch.timestamp - armed.uptime <= 2 else {
            reject("检测到多点或迟到触摸，校准已停止。")
            return
        }
        let type: String
        switch touch.type {
        case .direct: type = "direct"
        case .indirectPointer: type = "indirectPointer"
        default:
            reject("收到不支持的触控类型，校准已停止。")
            return
        }
        // AssistiveTouch may report direct touches; record the actual type, never claim provenance.
        candidate = (ObjectIdentifier(touch), armed, point, type)
        consumed = true
        self.armed = nil
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let candidate else { return }
        if let touch = touches.first(where: { ObjectIdentifier($0) == candidate.touch }),
           let point = screenPoint(touch), hypot(point.x - candidate.start.x, point.y - candidate.start.y) > 4 {
            reject("检测到拖动，校准已停止。")
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        defer { touches.forEach { activeTouches.remove(ObjectIdentifier($0)) } }
        guard let candidate, let touch = touches.first(where: { ObjectIdentifier($0) == candidate.touch }) else { return }
        self.candidate = nil
        guard activeTouches.count == 1, !hasOtherActiveTouch(in: event, excluding: touch),
              let point = screenPoint(touch),
              touch.timestamp >= candidate.arm.uptime, touch.timestamp - candidate.arm.uptime <= 2,
              hypot(point.x - candidate.start.x, point.y - candidate.start.y) <= 4,
              point.x >= 0, point.x < 390, point.y >= 0, point.y < 844 else {
            reject("点击位置或持续时间无效，校准已停止。")
            return
        }
        session?.received(CalibrationPoint(id: candidate.arm.id, page: candidate.arm.page, probe: candidate.arm.probe,
                                          x: point.x, y: point.y, touchType: candidate.type))
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        touches.forEach { activeTouches.remove(ObjectIdentifier($0)) }
        if candidate != nil || armed != nil { reject("触摸被系统取消，校准已停止。") }
    }
}
