import Foundation
import NetworkExtension

/// The tunnel built into this app, in place of a separate VPN app. See TikpalTunnel/PacketTunnelProvider.swift.
enum InAppTunnel {
    enum Failure: LocalizedError {
        case notReachable, localNetworkDenied

        var errorDescription: String? {
            switch self {
            case .notReachable: return "隧道没有接通，请确认已连接 Wi-Fi，再试一次"
            case .localNetworkDenied: return "请在 设置 › Tikpal 里打开“本地网络”"
            }
        }
    }

    private static func manager() async throws -> NETunnelProviderManager {
        let saved = try await NETunnelProviderManager.loadAllFromPreferences()
        let manager = saved.first ?? NETunnelProviderManager()
        let config = NETunnelProviderProtocol()
        config.providerBundleIdentifier = (Bundle.main.bundleIdentifier ?? "") + ".tunnel"
        config.serverAddress = "Tikpal"
        manager.protocolConfiguration = config
        manager.localizedDescription = "Tikpal"
        manager.isEnabled = true
        // The first time, iOS asks once for permission to add this configuration.
        try await manager.saveToPreferences()
        try await manager.loadFromPreferences()
        return manager
    }

    /// Returns once the device's own service answers through the tunnel.
    static func start() async throws {
        let tunnel = try await manager()
        if tunnel.connection.status != .connected { try tunnel.connection.startVPNTunnel() }
        for _ in 0..<15 {
            switch await LocalDevVPN.probeTunnel(timeout: 1) {
            case .reachable: return
            case .localNetworkDenied: throw Failure.localNetworkDenied
            case .unreachable: break
            }
        }
        throw Failure.notReachable
    }

    /// The mount is over: nothing needs the tunnel until the next reboot.
    static func stop() async {
        guard let saved = try? await NETunnelProviderManager.loadAllFromPreferences() else { return }
        saved.forEach { $0.connection.stopVPNTunnel() }
    }
}
