import SwiftUI
import CoreImage
import CoreMedia
import UIKit
@preconcurrency import ScreenCaptureKit

@MainActor
final class ScreenCapture: NSObject, ObservableObject {
    @Published var status = "未连接"
    @Published var error: String?
    @Published var running = false
    @Published var connecting = false
    @Published var sent = 0

    private let picker = SCContentSharingPicker.shared
    private let context = CIContext()
    private var pickerObserver: PickerObserver?
    private var stream: SCStream?
    private var baseURL: URL?
    private var token = ""
    private var uploading = false
    private var lastFrame = Date.distantPast
    private var generation = UUID()

    override init() {
        super.init()
        pickerObserver = PickerObserver(owner: self)
    }

    func connect(address: String, code: String) async {
        guard let url = URL(string: address), url.scheme == "http", url.host != nil,
              code.count == 6, code.allSatisfy(\.isNumber) else {
            error = "请输入 Mac 的 HTTP 地址和 6 位配对码"; return
        }
        connecting = true; error = nil; status = "正在连接 Mac"
        do {
            var request = URLRequest(url: url.appendingPathComponent("pair"))
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: ["role": "phone", "code": code])
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let newToken = json["token"] as? String else { throw CaptureError.pairing }
            token = newToken; baseURL = url
            guard let observer = pickerObserver else { throw CaptureError.pairing }
            picker.add(observer); picker.isActive = true
            var configuration = SCContentSharingPickerConfiguration()
            configuration.showsMicrophoneControl = false
            picker.defaultConfiguration = configuration
            status = "请选择整个屏幕"
            picker.present()
        } catch {
            connecting = false; self.error = error.localizedDescription; status = "连接失败"
        }
    }

    func start(filter: SCContentFilter) async {
        guard filter.style == .display else {
            await stop(); error = "请选择整个屏幕"; return
        }
        do {
            let config = SCStreamConfiguration()
            let next = SCStream(filter: filter, configuration: config, delegate: self)
            try next.addStreamOutput(self, type: .screen, sampleHandlerQueue: .main)
            try await next.startCapture()
            stream = next; running = true; connecting = false; status = "正在共享屏幕"
        } catch {
            connecting = false; self.error = error.localizedDescription; status = "抓屏失败"
        }
    }

    func stop() async {
        generation = UUID()
        if let stream { try? await stream.stopCapture() }
        stream = nil; running = false; connecting = false
        if let observer = pickerObserver { picker.remove(observer) }
        picker.isActive = false
        if let url = baseURL {
            var request = URLRequest(url: url.appendingPathComponent("stop"))
            request.httpMethod = "POST"; request.httpBody = Data("{}".utf8)
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            _ = try? await URLSession.shared.data(for: request)
        }
        status = "已停止共享"
    }

    private func send(_ buffer: CMSampleBuffer) {
        guard running, !uploading, Date().timeIntervalSince(lastFrame) >= 1,
              let pixel = CMSampleBufferGetImageBuffer(buffer), let url = baseURL else { return }
        let input = CIImage(cvPixelBuffer: pixel)
        guard input.extent.width > 0, input.extent.height > 0 else { return }
        let scale = 390 / input.extent.width
        let image = input.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cg = context.createCGImage(image, from: image.extent),
              let jpeg = UIImage(cgImage: cg).jpegData(compressionQuality: 0.65) else { return }
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first,
              let screen = scene.windows.first?.screen else { return }
        let bounds = screen.bounds
        let payload: [String: Any] = ["jpeg": jpeg.base64EncodedString(), "width": cg.width, "height": cg.height,
            "point_width": bounds.width, "point_height": bounds.height,
            "orientation": bounds.width > bounds.height ? "landscape" : "portrait", "timestamp": Date().timeIntervalSince1970]
        guard let body = try? JSONSerialization.data(withJSONObject: payload) else { return }
        var request = URLRequest(url: url.appendingPathComponent("frame"))
        request.httpMethod = "POST"; request.httpBody = body; request.timeoutInterval = 5
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        uploading = true; lastFrame = Date()
        let current = generation
        Task {
            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw CaptureError.upload }
                if current == generation { sent += 1; status = "正在共享屏幕"; error = nil }
            } catch {
                if current == generation { self.error = error.localizedDescription; status = "画面发送失败" }
            }
            uploading = false
        }
    }

    enum CaptureError: LocalizedError {
        case pairing, upload
        var errorDescription: String? { self == .pairing ? "配对失败，请重启 Mac 服务并重新配对" : "画面发送失败" }
    }
}

private struct SendableSampleBuffer: @unchecked Sendable {
    let value: CMSampleBuffer
}

extension ScreenCapture: SCStreamOutput, SCStreamDelegate {
    nonisolated func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return }
        let wrapped = SendableSampleBuffer(value: sampleBuffer)
        MainActor.assumeIsolated { send(wrapped.value) }
    }
    nonisolated func stream(_ stream: SCStream, didStopWithError error: Error) {
        Task { @MainActor in self.error = error.localizedDescription; await self.stop() }
    }
}

private final class PickerObserver: NSObject, SCContentSharingPickerObserver, @unchecked Sendable {
    private weak var owner: ScreenCapture?
    init(owner: ScreenCapture) { self.owner = owner }
    nonisolated func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        Task { @MainActor in await owner?.start(filter: filter) }
    }
    nonisolated func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Task { @MainActor in await owner?.stop() }
    }
    nonisolated func contentSharingPickerStartDidFailWithError(_ error: Error) {
        Task { @MainActor in owner?.error = error.localizedDescription; await owner?.stop() }
    }
}
