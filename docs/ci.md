# 云端编译和 TestFlight

开发用的 Mac 是 Intel 的，跑不了 Xcode 27（Xcode 27 只装在 Apple 芯片的 Mac 上，还要 macOS Tahoe 26.6 以上）。所以 iPhone 端全部在 GitHub Actions 上编：`runs-on: xcode-27` 是 GitHub 提供的装好 Xcode 27 的 Apple 芯片机器（目前是预览版，可能排队）。仓库是公开的，这类机器不收费。

工作流在 `.github/workflows/ios.yml`：

| 什么时候 | 做什么 | 要不要密钥 |
|---|---|---|
| 每次推送、每个 PR | 生成工程（XcodeGen），编译，不签名 | 不要 |
| 手动运行，勾上「签名并上传到 TestFlight」 | 归档、自动签名、导出、上传 TestFlight | 要（下面 4 个） |

## 第一次要做的（只有账号主人能做）

全部在付费的 Apple 开发者账号里操作。

1. **建 App 记录**：打开 App Store Connect →「App」→「+」→「新建 App」。
   - 平台：iOS
   - 名称：App Store 上不能和别人重名，比如「Yay 手机遥控」
   - 主要语言：简体中文
   - 套装 ID：`io.tikpal.phonepilot`。下拉里没有的话，先到 developer.apple.com →「Certificates, Identifiers & Profiles」→「Identifiers」用这个 ID 注册一个 App ID。
   - SKU：随便填一个自己认得的，比如 `phonepilot`
2. **建 API 密钥**：App Store Connect →「用户和访问」→「集成」→「App Store Connect API」→「团队密钥」→ 生成。
   - 角色选「管理」（Admin）。「App 管理」能归档，但导出时没有权限用云端管理的发布证书，会报 `Cloud signing permission error`（2026-11-02 实测）。
   - 下载 `AuthKey_XXXXXXXXXX.p8`。**只能下载一次**，存好，不要放进任何仓库。
   - 记下页面上的「密钥 ID」和「Issuer ID」。
3. **团队 ID**：developer.apple.com →「Membership details」里的 Team ID（10 位）。
4. **把 4 个值存成仓库密钥**（在自己电脑的终端里跑，密钥不经过聊天）：

   ```bash
   gh secret set ASC_KEY_ID -R jiayouchongmingniao/phone-pilot-ios
   gh secret set ASC_ISSUER_ID -R jiayouchongmingniao/phone-pilot-ios
   gh secret set APPLE_TEAM_ID -R jiayouchongmingniao/phone-pilot-ios
   gh secret set ASC_KEY_P8 -R jiayouchongmingniao/phone-pilot-ios < ~/下载/AuthKey_XXXXXXXXXX.p8
   ```

   前三个会提示你粘贴值。也可以在网页上：仓库 →「Settings」→「Secrets and variables」→「Actions」→「New repository secret」。

## 上传一版到 TestFlight

```bash
gh workflow run iOS -R jiayouchongmingniao/phone-pilot-ios -f testflight=true
gh run watch -R jiayouchongmingniao/phone-pilot-ios
```

- 构建号用运行编号（`github.run_number`），每次自动加一，不用手改。
- 上传后 App Store Connect 要处理十几分钟到半小时。处理完，在 App Store Connect 的「TestFlight」页把自己加进「内部测试」，iPhone 上的 TestFlight app 里就能装。
- 只给内部测试员用，不用过审。

## 出错时先看

- 「缺少仓库密钥」：第 4 步有没有存全，名字要一字不差。
- 导出时报 `Cloud signing permission error`：API 密钥的角色不够，换一个「管理」（Admin）角色的团队密钥。
- 签名报错说找不到 App ID 或 App：第 1 步的套装 ID 和 `project.yml` 里的 `PRODUCT_BUNDLE_IDENTIFIER` 要一样。
- 上传报图标错误：图标要 1024 × 1024、不带透明通道（`tools/make_icon.py` 生成的就是）。
