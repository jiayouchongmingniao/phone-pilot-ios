# iCTRL 执行层

2026-11-05。runner 已在 iPhone 16e、iOS 27.2 上运行，`accept 微信 --rounds 10` 10/10 通过（中位约 1.3 秒，最长约 1.7 秒）。

现在有两条路径：

- **圆屏直连（当前使用）：** 圆屏自己调用 Qwen，再通过 Wi-Fi 操作手机，运行时不需要 Mac。见下面“圆屏直连”。
- **经过 Mac：** `demo/server.py` 设 `EXECUTOR=ictrl`。默认不启用，不影响现有 HID 路径。

## 圆屏直连

```text
按住侧键说话 → 圆屏录音 → Qwen 语音识别 → 本地规则，听不懂再问 Qwen 文字模型
  → 圆屏固件的 iCTRL 客户端 → 加密 JSON-RPC（Wi-Fi）→ iPhone 上的 iCTRL runner
```

固件在单独的本地仓库 `phone-pilot`，不在这个仓库里。构建时把 DashScope 密钥写进固件，所以**编译产物不能发布或分享给别人**。

| 模型 | 用途 | 接口 |
|---|---|---|
| `qwen-audio-3.1-asr-flash` | 语音识别 | `multimodal-generation` |
| `qwen3.7-flash` | 理解本地规则听不懂的话，输出固定 JSON | `multimodal-generation` |

`qwen3.7-flash` 不能走 `text-generation`，否则返回 `InvalidParameter: url error`。

### 语音指令

| 指令 | 例子 | 做法 |
|---|---|---|
| 打开 App | “打开微信”“麻烦把微信给我打开” | `launch_app`，核对前台 |
| 输入文字 | “输入你好”“帮我写上明天见” | 写进当前焦点框，不回车；密码框拒绝 |
| 回主屏 | “回到主屏幕” | `home` |
| 读屏幕 | “读屏幕”“屏幕上写了什么” | `collect{pages:0}`，文字显示在圆屏，可触摸滚动 |
| 滑动 | “往下滑”“往左滑” | 上下用 `page`，左右用 `swipe` |
| 点击 | “点一下搜索”“返回” | 只点唯一、在屏内的元素 |
| 微信发消息 | “告诉张三今晚开会” | 见下面的流程，发送前不再确认 |

Qwen 的回答不直接执行。固件会回到原句再核对一次，下面的情况一律不执行：

- 模型标记为多目标，或者答案不是原句里的连续文字。
- 句子里有钱、转账、付款、购买、删除或否定词（“不要”“别”“不用”）。发消息时只检查请求本身，消息内容里可以有“别迟到”“买菜”，但开头是否定词，或提到转账、付款、红包、删除时仍然不发。
- 打开 App 时原句没有“打开”这类动词，或者提到了第二个 App。这条是必需的：测试时模型把“把支付宝的钱转给李四”判成过“打开支付宝”。
- 发消息时提到短信、邮件、电话、QQ、钉钉、飞书。目前只支持微信。

点击规则：

- 付款、删除类标签直接拒绝，模糊匹配实际命中的标签也要过一遍。
- “确认”“确定”这类标签单独说不清楚，要先读页面，页面上有付款、删除类文字就拒绝。
- 无障碍树找不到时才用 OCR，文字必须完全相等且只有一处。
- 超时或结果不明时停止，不重发。

### 微信发消息

用户明确要求发送前不再确认，所以这里靠核对把关，有疑问就不发：

1. 打开微信，用“返回”退到聊天列表（最多 6 次）。
2. 点“搜索”，写入联系人名字，读回搜索框核对。
3. 只接受 `联系人`、`最常使用`、`功能` 分组下名字完全相同的行。`聊天记录` 的行和底部搜一搜推荐也带这个名字，点了进不了聊天。没有就报“没有这个联系人”，多于一个就报“同名”。
4. 打开后核对聊天标题。标题可以在逗号后带内容（比如免打扰），`张三丰` 不算 `张三`。
5. 输入框已有草稿就停止，不覆盖也不一起发出去。
6. 点输入框，写入内容，读回核对。
7. `type{text:"", submit:true}` 按回车发送，之后不再重复。输入框清空才报“已发送”，否则报“未能核实”。

每次点击和输入之前都确认前台是微信。一次探测中微信中途退到了主屏，原因没有查明，所以这个检查不能省。

已知限制：

- 群聊暂不支持：群聊结果的名字带人数（如 `项目组 (8)`）。
- 备注名和昵称不同时，要说备注名。
- 语音内容识别错了，错字会直接发出去。名字听错一般会因找不到人而停止，除非刚好有同名联系人。
- 页面结构来自中文界面的微信，微信更新后位置规则可能失效，失效时会停止，不会乱点。
- 键盘不在无障碍树里，找不到“发送”键，所以用回车发送。
- runner 对 `type` 总是警告“未确认焦点”，实际已写入，所以以读回内容为准。

### 配对和换 IP

- 第一次连接时圆屏显示指纹，在手机通知里允许。密钥存在圆屏 NVS（命名空间 `phone_pilot`）。
- 连不上手机时，圆屏会显示 IP 输入框，不需要电脑。
- 手机重启后要重新挂载 Developer Disk Image。不用 Mac 的路径见下面的“手机端准备”，还没有实测。

### 验证状态（2026-11-05）

| 项目 | 状态 |
|---|---|
| 圆屏语音“打开微信”，不经过 Mac | 通过 |
| 圆屏配对 | 通过 |
| Qwen 文字模型七句测试（打开、输入、发送、否定、多目标、闲聊、转账） | 通过 |
| 回主屏、读屏幕、滑动、点击 | 代码和单元测试完成，真机未确认 |
| 微信发消息：找人、核对标题、写入并读回 | 脚本在真机上通过（目标是文件传输助手） |
| 微信发消息：回车发送 | **未验证** |
| 停掉 Mac 服务后的完整测试、手机重启后不用 Mac 重新挂载 | 未测试 |

### 探测微信页面

固件仓库的 `tools/wechat_probe.py` 在手机上走一遍流程，并写一份报告。报告只有页面结构，名字和消息内容都换成字数。

## 经过 Mac 的路径

下面是最初的设计。

## 为什么换

| 环节 | HID + 录屏（现有） | iCTRL |
|---|---|---|
| 找目标 | Claude 看图估框。最新记录里同一图标两次框中心相差约 110 点 | 无障碍树给出元素，`ref` 在点击时重新定位活元素 |
| 打开 App | 找图标、翻页、移动指针、点击 | `launch_app{bundle_id}`，不碰屏幕坐标 |
| 确认结果 | 模型判断新画面 | 系统前台 App 必须等于目标 bundle |
| 中文输入 | 拼音 + 候选词，未完成验收 | 直接输入 Unicode |
| 校准 | 九点校准，重启、重配对后重做 | 不需要 |
| 手机上的准备 | 屏幕共享不能中断，USB 接圆屏 | 需要 runner 在运行，USB 空出来 |

从记录看，目标框的波动明显大于校准误差：独立验证点误差约 2 点，而模型框相差约 110 点。不过出错那个位置的真实指针落点没有实测。iCTRL 同时替掉找目标和执行两段，不必再分辨是哪一段的问题。

## 数据流

```text
圆屏按住说话 → Mac /audio（Qwen 转写）→ 本地规则或 Claude 理解指令
  → demo/ictrl_driver.py → 加密 JSON-RPC（Wi-Fi）→ iPhone 上的 iCTRL runner
  → 结果文字经 /next 显示在圆屏
```

`/next` 不下发 `action`，所以圆屏固件和 iOS App 都不用改。这条路径不调用 Jev 和看图模型，也不做像素比较。

## 规则

- 打开 App：前台不是目标就不算完成，报“未能核实”。
- 点击：只接受唯一、完全在屏内的元素，用 `ref` 点一次。有相似元素、半露、不可点时停止。OCR 坐标要显式开启，且文字必须完全相等、只有一处。
- 超时不等于失败：iCTRL 的调用可能还在跑，一律停止，绝不重发。
- 系统弹窗、验证码、限流、锁屏时停止。滑块验证不尝试。
- 付款、删除类标签直接拒绝；发送类标签要确认。输入绝不按回车；密码框拒绝输入。
- runner 不可用时报错，**不自动退回 HID**，避免同一句话走两条路径。

## 经过 Mac 时的语音范围

- 现在支持：打开清单内的 22 个 App 名、在当前焦点框输入（含中文和中英混排）。
- 暂不支持：语音发送和语音点击。固件的确认框绑在指针流程上，要加一种“只确认”的动作并更新固件。在这之前“发送”会停下并提示到手机上操作。
- 点击目前只有命令行，供调试。

## 手机端准备

runner 是 XCUITest，不能走 TestFlight。开发用的 Mac 没有 Xcode 和证书，所以在 GitHub Actions 里编译和签名（分支 `ictrl-ci`，工作流 `.github/workflows/ictrl.yml`）。

- `build` 从固定的上游提交（v1.98.16）编译，bundle id 换成 `io.tikpal.ictrl*`。这个任务会运行上游的构建脚本，所以不给任何密钥。
- `sign` 用已有的 App Store Connect 仓库密钥准备证书、注册手机、创建通配 App ID（`io.tikpal.*`）和描述文件。它只运行本仓库的代码。
- 仓库是公开的，签名后的 IPA 含设备编号，只加密上传，保留 3 天。

第一次要加两个仓库密钥。用 USB 把 iPhone 接到 Mac，解锁并信任，然后运行（值不经过聊天，也不进日志）：

```zsh
ideviceinfo -k UniqueDeviceID | gh secret set IPHONE_UDID -R jiayouchongmingniao/phone-pilot-ios
mkdir -p ~/.ictrl && (umask 077; openssl rand -hex 32 > ~/.ictrl/artifact-pass)
gh secret set ICTRL_ARTIFACT_PASSWORD -R jiayouchongmingniao/phone-pilot-ios < ~/.ictrl/artifact-pass
gh run rerun -R jiayouchongmingniao/phone-pilot-ios \
  "$(gh run list -R jiayouchongmingniao/phone-pilot-ios --workflow 'iCTRL build' --branch ictrl-ci --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch -R jiayouchongmingniao/phone-pilot-ios \
  "$(gh run list -R jiayouchongmingniao/phone-pilot-ios --workflow 'iCTRL build' --branch ictrl-ci --limit 1 --json databaseId --jq '.[0].databaseId')"
```

构建成功后，手机仍接着 Mac，运行：

```zsh
cd phone-pilot-ios
python3 demo/ictrl_setup.py install-ci
```

它会下载最新产物、解密并装到手机。第一次还会把新建的开发证书存回仓库密钥 `ICTRL_DEV_P12_B64`，以后的构建就复用这张证书。`sign` 会在 Apple 账号里注册这台手机（占一个设备名额）并新建一张开发证书。

`demo/ictrl_setup.py provision` 和 `install` 是本机签名的备用路线，需要本机有 `.p8`。

然后在手机上：

1. 打开开发者模式：设置 › 隐私与安全性 › 开发者模式，重启。
2. 设置 › 通用 › VPN 与设备管理，信任开发者证书。
3. 挂载 Developer Disk Image。Mac 没有 Xcode 时用 iCTRL App：
   - 装 LocalDevVPN 并连接。VPN 类 App 在中国区商店可能搜不到，那就需要换其他地区的 Apple ID 下载。
   - 用 [idevice_pair](https://github.com/jkcoxson/idevice_pair) 在 Mac 上选 Remote pairing 生成配对文件，AirDrop 到手机，导入 iCTRL App。
   - 在 iCTRL App 的 Settings › Runtime requirements › Mount without a Mac 里按提示挂载。首次要联网。
   - **每次重启手机都要重新挂载。**
4. 打开 runner，允许通知和本地网络，按需输入锁屏密码。
5. 长按 runner 的通知，选“Enable Remote Access”。

## Mac 端

```zsh
cd phone-pilot-ios
export ICTRL_HOST=<iPhone 的 IP>:8200

demo/.venv/bin/python demo/ictrl_driver.py pair      # 核对指纹后在手机上点 Allow
demo/.venv/bin/python demo/ictrl_driver.py health    # 只读
demo/.venv/bin/python demo/ictrl_driver.py accept 微信 --rounds 10   # 回主屏再打开，连续 10 次

export EXECUTOR=ictrl
demo/.venv/bin/python demo/server.py --bind "$(ipconfig getifaddr en0)"
```

- 不设 `EXECUTOR` 时行为和原来一样。
- 密钥保存在 `~/.ictrl/token.json`，30 天没用会失效。
- `GET /status` 的 `executor` 和 `ictrl_diagnostic` 可看最近一次结果，不含转写和输入内容。
- 连接报 `No route to host` 时，检查 Mac 的 系统设置 › 隐私与安全性 › 本地网络。

## 经过 Mac 时的验收顺序

1. `health` 返回版本且 `screen_locked` 为 false。已通过。
2. `accept 微信 --rounds 10` 全部通过，看 `median_ms`。已通过。
3. 语音连续两次“打开微信”。这是 HID 路径一直没通过的验收项。
4. 在输入框里说“输入你好hello”，确认没有被发送出去。
5. 拔掉圆屏到 iPhone 的 USB 再测一遍。从代码看不需要 USB，但没有实测。
6. 记录锁屏、重启、断 Wi-Fi 时的提示，以及长时间运行后的发热和耗电。

## 风险

- 配对密钥等于这台手机的完全控制权。不用时关掉 Remote Access 或停掉 runner，只在可信 Wi-Fi 上用，只批准自己发起的配对。
- App 可能检测自动化，iCTRL 不保证不被发现。微信等账号要自己权衡，上游也要求遵守各 App 的条款。
- 自绘或网页界面可能读不到无障碍树，这时输入会报“未能核实”，点击会停止或需要显式开启 OCR。
- 上游只说明用于自动化测试和研究，这部分不能作为面向用户的正式方案直接发布。
