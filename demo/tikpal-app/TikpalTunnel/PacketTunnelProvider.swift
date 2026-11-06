import NetworkExtension

/// Makes this device's own debug service reachable, so the app can mount the Developer Disk Image without a computer.
///
/// An app cannot reach its own lockdownd on 127.0.0.1. So this is not a real VPN: it routes one small private range and
/// sends every packet straight back with its addresses swapped, which makes a connection to 10.7.0.1 look like an ordinary
/// one coming in. No other traffic enters it. The tunnel is only up for the moment of the mount.
final class PacketTunnelProvider: NEPacketTunnelProvider {
    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
        let ipv4 = NEIPv4Settings(addresses: ["10.7.0.0"], subnetMasks: ["255.255.255.0"])
        ipv4.includedRoutes = [NEIPv4Route(destinationAddress: "10.7.0.0", subnetMask: "255.255.255.0")]
        ipv4.excludedRoutes = [NEIPv4Route.default()]
        settings.ipv4Settings = ipv4
        settings.mtu = 1500
        setTunnelNetworkSettings(settings) { [weak self] error in
            completionHandler(error)
            if error == nil { self?.reflect() }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        completionHandler()
    }

    private func reflect() {
        packetFlow.readPackets { [weak self] packets, protocols in
            guard let self else { return }
            self.packetFlow.writePackets(packets.map(Self.swappingAddresses), withProtocols: protocols)
            self.reflect()
        }
    }

    /// Source and destination change places. The IP and TCP checksums add the same words either way, so they still hold.
    private static func swappingAddresses(_ packet: Data) -> Data {
        guard packet.count >= 20, packet[packet.startIndex] >> 4 == 4 else { return packet }
        var out = packet
        let start = out.startIndex
        for i in 0..<4 { out.swapAt(start + 12 + i, start + 16 + i) }
        return out
    }
}
