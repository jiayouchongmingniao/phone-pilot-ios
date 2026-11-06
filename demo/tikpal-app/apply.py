#!/usr/bin/env python3
"""Turns a checkout of upstream iCTRL into the Tikpal build: one button after a reboot, no separate VPN app.

    python3 apply.py <upstream checkout>

Every edit must find exactly what it expects, so a change upstream stops the build here and not on the phone.
"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = sys.argv[1]


def edit(path, pairs):
    path = os.path.join(ROOT, path)
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f'{path}: expected once, found {text.count(old)}: {old[:60]!r}')
        text = text.replace(old, new)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


def rename_in_strings(path):
    """Only inside string literals, so identifiers such as iCTRLUITests are left alone."""
    path = os.path.join(ROOT, path)
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', lambda m: m.group(0).replace('iCTRL', 'Tikpal'), text)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


shutil.copytree(os.path.join(HERE, 'TikpalTunnel'), os.path.join(ROOT, 'TikpalTunnel'))
for name in ('InAppTunnel.swift', 'TikpalHomeView.swift'):
    shutil.copy(os.path.join(HERE, 'iCTRLApp', name), os.path.join(ROOT, 'iCTRLApp', name))

# Icons, made by make-icons.py. The set must match upstream's, so a new size upstream stops the build here.
shutil.copy(os.path.join(HERE, 'icon_1024x1024.png'),
            os.path.join(ROOT, 'iCTRLApp/Assets.xcassets/AppIcon.appiconset/icon_1024x1024.png'))
RUNNER_ICONS = os.path.join(ROOT, 'Config/RunnerIcon')
if sorted(os.listdir(RUNNER_ICONS)) != sorted(os.listdir(os.path.join(HERE, 'RunnerIcon'))):
    sys.exit(f'{RUNNER_ICONS}: upstream has a different set of icons')
shutil.rmtree(RUNNER_ICONS)
shutil.copytree(os.path.join(HERE, 'RunnerIcon'), RUNNER_ICONS)

edit('project.yml', [
    ('''      - framework: Frameworks/IDevice.xcframework
        embed: false
''', '''      - framework: Frameworks/IDevice.xcframework
        embed: false
      - target: TikpalTunnel
'''),
    ('''        PRODUCT_BUNDLE_IDENTIFIER: "$(ICTRL_BUNDLE_PREFIX).ictrl"
''', '''        PRODUCT_BUNDLE_IDENTIFIER: "$(ICTRL_BUNDLE_PREFIX).ictrl"
        INFOPLIST_KEY_CFBundleDisplayName: Tikpal
'''),
    ('''  iCTRLUITests:
    type: bundle.ui-testing
''', '''  TikpalTunnel:
    type: app-extension
    platform: iOS
    sources:
      - path: TikpalTunnel
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: "$(ICTRL_BUNDLE_PREFIX).ictrl.tunnel"
        INFOPLIST_FILE: TikpalTunnel/Info.plist
        GENERATE_INFOPLIST_FILE: NO
        MARKETING_VERSION: "1.0.0"
        CURRENT_PROJECT_VERSION: 1
        CODE_SIGN_STYLE: Automatic

  iCTRLUITests:
    type: bundle.ui-testing
'''),
    ('NSLocalNetworkUsageDescription: "iCTRL reaches', 'NSLocalNetworkUsageDescription: "Tikpal reaches'),
    ('NSLocalNetworkUsageDescription: "iCTRL needs', 'NSLocalNetworkUsageDescription: "Tikpal needs'),
])

edit('iCTRLApp/iCTRLApp.swift', [('''                SettingsView()
                    .environmentObject(appState)
''', '''                TikpalHomeView()
                    .environmentObject(appState)
''')])

edit('iCTRLApp/Views/SettingsView.swift', [('.navigationTitle("iCTRL")', '.navigationTitle("Tikpal")')])
edit('iCTRLApp/Models/RunnerClient.swift', [('static let clientName = "iCTRL App"', 'static let clientName = "Tikpal"')])

# The tunnel is built in now.
edit('iCTRLApp/DDI/LocalDevVPN.swift', [('''        UIApplication.shared.canOpenURL(probeURL)
''', '''        true
''')])
edit('iCTRLApp/DDI/DDIMountView.swift', [
    ('''                actionButton(vpnInstalled ? L10n.t(.openVPNAndConnect) : L10n.t(.getVPNFromAppStore)) {
                    // enable? starts the tunnel and bounces back to us; the bare
                    // App Store link is the fallback when it isn't installed.
                    UIApplication.shared.open(vpnInstalled ? LocalDevVPN.enableURL
                                                           : LocalDevVPN.appStoreURL)
                }
''', '''                actionButton("接通内置隧道") {
                    Task {
                        try? await InAppTunnel.start()
                        refreshVPNState()
                    }
                }
'''),
    ('title: L10n.t(.startLocalDevVPN),', 'title: "内置隧道",'),
])

# The image travels inside the app when the signing step has put it there, so nothing has to be fetched from GitHub.
edit('iCTRLApp/DDI/DeveloperDiskImageStore.swift', [('''    nonisolated static var directory: URL {
        let dir = rootDirectory.appendingPathComponent(mountMethod.mirrorFolder)
''', '''    nonisolated static var directory: URL {
        if let bundled = bundledDirectory { return bundled }
        let dir = rootDirectory.appendingPathComponent(mountMethod.mirrorFolder)
'''), ('''    private nonisolated static func migrateLegacyFiles(into dir: URL) {
''', '''    /// This device's set inside the app, when all of it is there.
    private nonisolated static var bundledDirectory: URL? {
        guard let dir = Bundle.main.resourceURL?.appendingPathComponent("DDI/" + mountMethod.mirrorFolder) else { return nil }
        let all = mountMethod.components.allSatisfy {
            FileManager.default.fileExists(atPath: dir.appendingPathComponent($0.rawValue).path)
        }
        return all ? dir : nil
    }

    private nonisolated static func migrateLegacyFiles(into dir: URL) {
''')])

# Reachable on the network from the first start. A client still has to be allowed on the phone before it can do anything.
edit('iCTRLUITests/iCTRLUITests.swift', [('''        get { UserDefaults.standard.bool(forKey: allowRemoteAccessKey) }
''', '''        get { UserDefaults.standard.object(forKey: allowRemoteAccessKey) as? Bool ?? true }
''')])

rename_in_strings('iCTRLUITests/Localized.swift')
