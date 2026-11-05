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
| 打开 App | “打开微信”“帮我把美团打开” | 清单外的 App 用 `resolve_app` 找，名字要对得上；`launch_app` 后核对前台 |
| 关闭 App | “关闭抖音” | `terminate_app`，关微信会中断通话 |
| 输入文字 | “输入你好”“帮我写上明天见” | 写进当前焦点框，不回车；密码框拒绝 |
| 清空 | “清空输入框” | `clear_text` |
| 回车 | “回车”“搜索一下” | `type{text:"",submit:true}`；页面上有付款、删除类文字时拒绝 |
| 回主屏、锁屏 | “回到主屏幕”“锁屏” | `home`、`lock`；不提供解锁 |
| 读屏幕 | “读屏幕”“把这页读完” | `collect`，后者翻最多 4 页，文字显示在圆屏，可触摸滚动 |
| 滑动 | “往下滑”“往左滑” | 上下用 `page`，左右用 `swipe` |
| 点击、长按、双击 | “点一下搜索”“长按头像”“返回” | 只对唯一、在屏内的元素操作 |
| 滚动找控件再点 | “找到通用并点开” | 最多翻 8 页，同样要唯一 |
| 微信发消息 | “告诉张三今晚开会” | 见下面的流程，发送前不再确认 |
| 回复当前聊天 | “回复收到” | 发给正打开的聊天，圆屏显示发给了谁，**不核对对象** |
| 读微信消息 | “张三说了什么” | 打开聊天，用 OCR 读，只显示在圆屏；这些消息会变成已读 |
| 查未读 | “微信未读” | 只列聊天列表当前一屏，不打开聊天 |
| 微信搜索 | “微信搜露营” | 结果留在手机上 |
| 进入微信页面 | “朋友圈”“视频号”“公众号”“通讯录” | 从底部标签点进去；“服务”页有微信支付，不提供 |
| 扫一扫 | “扫一扫” | `weixin://scanqrcode` |
| 微信通话 | “给张三打语音电话”“和张三视频” | 直接拨出，不支持群聊 |
| 写朋友圈 | “发朋友圈今天天气真好” | 只写好文字，由用户在手机上点发表 |

内部链接实测后只留了扫一扫：`weixin://dl/moments`、`dl/settings`、`dl/favorites` 只会打开微信，到不了目标页，支付宝扫一扫打不开，设置的 `App-prefs:` 链接没能确认到目标页。

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
2. 点“搜索”，写入名字，读回搜索框核对。
3. 只接受 `联系人`、`最常使用`、`功能` 分组下名字完全相同的行，以及 `群聊` 分组下 `名字 (人数)` 的行。`聊天记录` 的行和底部搜一搜推荐也带这个名字，点了进不了聊天。没有就报“没有这个联系人”，多于一个就报“同名”，联系人和群同名时也算。
4. 打开后核对聊天标题。标题可以在逗号后带内容（比如免打扰），`张三丰` 不算 `张三`。
5. 输入框已有草稿就停止，不覆盖也不一起发出去。
6. 点输入框，写入内容，读回核对。
7. `type{text:"", submit:true}` 按回车发送，之后不再重复。输入框清空才报“已发送”，否则报“未能核实”。

每次点击和输入之前都确认前台是微信。一次探测中微信中途退到了主屏，原因没有查明，所以这个检查不能省。

已知限制：

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

以下由使用者在 iPhone 16e（iOS 27.2）上确认通过：

- 圆屏语音打开 App（不经过 Mac）、圆屏配对。
- Qwen 文字模型三十句测试：打开、输入、发送、否定、多目标、闲聊、转账等。
- 上面语音指令表里的全部指令，包括微信发消息、回复、读消息、查未读、搜索、页面入口、通话和写朋友圈。

还没有单独记录的情况：

- 同名联系人、输入框已有草稿、结果不明时的停止，这些由单元测试覆盖。
- 手机重启后不用 Mac 重新挂载 Developer Disk Image。

### 分发和系统版本

**runner 基本不可能走 TestFlight：**

- runner 要用带 `get-task-allow` 的开发描述文件签名，签名脚本也会拒绝分发描述文件。TestFlight 用的是分发签名。
- runner 运行要靠开发者模式和 Developer Disk Image。即使能装上，每台手机也要手动打开开发者模式，并在每次重启后重新挂载。
- 构建要上传到 App Store Connect，外部测试还要过 Beta 审核。runner 是 XCUITest 包，还能操控其他 App，很难通过。这一条是判断，没有实际上传试过。

能用的安装方式都只适合开发者或少量内测：

| 方式 | 手机上要做的 | 限制 |
|---|---|---|
| 付费开发者账号 + 开发描述文件（现在用的） | 注册 UDID，开开发者模式，挂载镜像 | 每类设备每年 100 台，描述文件约一年到期 |
| 对方用免费 Apple ID + SideStore | 自己签名安装，定期刷新 | 证书 7 天过期，最多 3 个 App |
| 对方用自己的付费开发者账号 | 自己签名 | 对方要有开发者账号 |

上游只说明用于自动化测试和研究。要面向普通用户，手机端得换方案：圆屏当键鼠（HID）、快捷指令和 App Intents，或无障碍外接设备接口，这些能走 TestFlight，但能力都比 iCTRL 弱。

**系统版本和机型：**

- runner 上游支持 iOS 16 及以上，最低部署版本 16.0。目前只实测过 iPhone 16e、iOS 27.2。
- 仓库里 iPhone App 的最低版本是 iOS 27.0（`project.yml`）。它用于经过 Mac 的录屏路径，圆屏直连用不到。
- 打开 App、点击和读屏幕用系统无障碍接口，受机型影响小。
- 固件的微信流程用了按 390×844 屏幕写的固定坐标，比如聊天标题的位置和搜索结果的缩进。其他尺寸的 iPhone 上可能对不上，这时会停在“打开的聊天不对”这类提示，不会乱点。要支持其他机型，得改成相对位置，并找对应的手机验证。

### 探测微信页面

固件仓库的 `tools/wechat_probe.py` 在手机上走一遍流程，并写一份报告。报告只有页面结构，名字和消息内容都换成字数。`tools/check_links.py` 逐个试内部链接，只报告能否到达目标页。手机锁屏时 `launch_app` 会报“没有安装”，这两个脚本不查锁屏，要先解锁。

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
