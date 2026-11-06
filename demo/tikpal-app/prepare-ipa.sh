#!/usr/bin/env bash
# Just before signing: names and hides the runner, and puts the Developer Disk Image inside the app.
#
#   prepare-ipa.sh <iCTRLUITests-Runner.ipa> <iCTRL.ipa>
#
# The image is Apple's file, so this only happens in the job whose output is encrypted. It must never reach an unencrypted
# artifact of this public repository.
set -euo pipefail
RUNNER_IPA="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
APP_IPA="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
MIRROR=https://github.com/doronz88/DeveloperDiskImage/raw/e2aaeac89ca8fa56c2fa1f144d182994e07a525b/PersonalizedImages
PB=/usr/libexec/PlistBuddy

runner_dir="$(mktemp -d)"
app_dir="$(mktemp -d)"
trap 'rm -rf "${runner_dir:?}" "${app_dir:?}"' EXIT

(cd "$runner_dir" && unzip -q "$RUNNER_IPA")
plist="$(find "$runner_dir/Payload" -maxdepth 2 -name Info.plist | head -1)"
$PB -c 'Delete :CFBundleDisplayName' "$plist" 2>/dev/null || true
$PB -c 'Add :CFBundleDisplayName string Tikpal Runner' "$plist"
# Off the home screen: Tikpal starts and stops it, so there is nothing to tap here.
$PB -c 'Delete :SBAppTags' "$plist" 2>/dev/null || true
$PB -c 'Add :SBAppTags array' "$plist"
$PB -c 'Add :SBAppTags:0 string hidden' "$plist"
rm -f "$RUNNER_IPA"
(cd "$runner_dir" && zip -qry "$RUNNER_IPA" Payload)

(cd "$app_dir" && unzip -q "$APP_IPA")
app="$(find "$app_dir/Payload" -maxdepth 1 -name '*.app' -type d | head -1)"
# iOS 26.4 and later. An older iOS has no set here, and the app downloads its own as before.
folder=Xcode_iOS_DDI_Cryptex
mkdir -p "$app/DDI/$folder"
for name in BuildManifest.plist Image.dmg Image.dmg.trustcache Image.dmg.cryptex_info Image.dmg.root_hash; do
    curl -fsSL --retry 3 -o "$app/DDI/$folder/$name" "$MIRROR/$folder/$name"
    echo "$name: $(wc -c < "$app/DDI/$folder/$name") bytes"
done
rm -f "$APP_IPA"
(cd "$app_dir" && zip -qry "$APP_IPA" Payload)
