import Foundation

@main
struct ProbeHarness {
    @MainActor static func main() {
        let server = LocalProbeServer()
        server.start { ["capturing": true, "frames": 17, "frame_age_ms": 200] }
        print(server.code)
        fflush(stdout)
        dispatchMain()
    }
}
