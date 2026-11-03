import Foundation
@preconcurrency import Network

// Development-only: never receives screen pixels, audio, model keys, or HID commands.
@MainActor
final class LocalProbeServer: ObservableObject {
    @Published private(set) var status = "未启动"
    @Published private(set) var requests = 0
    @Published private(set) var code = ""
    private var listener: NWListener?
    private var generation = UUID()

    func start(snapshot: @escaping @MainActor () -> [String: Any]) {
        stop()
        code = UUID().uuidString
        let current = generation
        do {
            let listener = try NWListener(using: .tcp, on: 8789)
            self.listener = listener
            listener.stateUpdateHandler = { [weak self] state in
                MainActor.assumeIsolated {
                    guard let self, self.generation == current else { return }
                    switch state {
                    case .ready: self.status = "监听中，端口 8789"
                    case .failed(let error): self.status = "监听失败：\(error.localizedDescription)"
                    default: break
                    }
                }
            }
            listener.newConnectionHandler = { [weak self] connection in
                MainActor.assumeIsolated {
                    guard let self, self.generation == current else { connection.cancel(); return }
                    self.receive(connection, snapshot: snapshot, generation: current)
                }
            }
            listener.start(queue: .main)
            status = "正在启动"
        } catch { status = "监听失败：\(error.localizedDescription)" }
    }

    func stop() {
        generation = UUID()
        listener?.cancel(); listener = nil
        code = ""; status = "未启动"
    }

    private func receive(_ connection: NWConnection, snapshot: @escaping @MainActor () -> [String: Any], generation: UUID) {
        connection.start(queue: .main)
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { connection.cancel() }
        read(connection, data: Data(), snapshot: snapshot, generation: generation)
    }

    private func read(_ connection: NWConnection, data: Data, snapshot: @escaping @MainActor () -> [String: Any], generation: UUID) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 4096) { [weak self] part, _, complete, error in
            MainActor.assumeIsolated {
                guard let self, self.generation == generation, error == nil else { connection.cancel(); return }
                let data = data + (part ?? Data())
                guard data.count <= 4096 else { self.reply(connection, code: 413, body: [:]); return }
                guard let end = data.range(of: Data("\r\n\r\n".utf8)) else {
                    if complete { connection.cancel() }
                    else { self.read(connection, data: data, snapshot: snapshot, generation: generation) }
                    return
                }
                guard let text = String(data: data[..<end.lowerBound], encoding: .utf8) else {
                    self.reply(connection, code: 400, body: [:]); return
                }
                let lines = text.components(separatedBy: "\r\n")
                guard lines.first == "GET /probe HTTP/1.1" else { self.reply(connection, code: 404, body: [:]); return }
                guard lines.dropFirst().contains(where: { $0.lowercased().hasPrefix("x-probe-code:") &&
                    $0.dropFirst("x-probe-code:".count).trimmingCharacters(in: .whitespaces) == self.code }) else {
                    self.reply(connection, code: 401, body: [:]); return
                }
                self.requests += 1
                Task { @MainActor in
                    var request = URLRequest(url: URL(string: "https://www.apple.com/library/test/success.html")!)
                    request.timeoutInterval = 8
                    let started = Date()
                    var result: [String: Any] = [:]
                    do {
                        let (_, response) = try await URLSession.shared.data(for: request)
                        result["https_status"] = (response as? HTTPURLResponse)?.statusCode ?? 0
                    } catch { result["https_error"] = error.localizedDescription }
                    guard self.generation == generation else { connection.cancel(); return }
                    result.merge(snapshot()) { _, new in new }
                    result["https_ms"] = Int(Date().timeIntervalSince(started) * 1000)
                    result["requests"] = self.requests
                    self.reply(connection, code: 200, body: result)
                }
            }
        }
    }

    private func reply(_ connection: NWConnection, code: Int, body: [String: Any]) {
        let payload = (try? JSONSerialization.data(withJSONObject: body)) ?? Data("{}".utf8)
        let header = "HTTP/1.1 \(code) \(code == 200 ? "OK" : "Error")\r\nContent-Type: application/json\r\nContent-Length: \(payload.count)\r\nConnection: close\r\n\r\n"
        connection.send(content: Data(header.utf8) + payload, completion: .contentProcessed { _ in connection.cancel() })
    }
}
