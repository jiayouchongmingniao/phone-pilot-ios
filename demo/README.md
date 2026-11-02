# Phone Pilot 开发演示

此版本仍在开发中。iPhone、圆屏和 Mac 必须连接同一个可信的 Wi-Fi。局域网连接使用明文 HTTP；不要在公共网络运行。Mac 的图片分析使用 Anthropic Claude Haiku 5.5，录音通过阿里云 qwen-audio-3.1-asr-flash 转写。

在 Mac 的终端中启动服务。两个密钥在终端中静默输入，不要提交或发到聊天中：

```zsh
cd /Users/pom/Code/yaykit/phone-pilot-ios
read -s 'ANTHROPIC_API_KEY?Anthropic API key: '; export ANTHROPIC_API_KEY; echo
read -s 'DASHSCOPE_API_KEY?DashScope API key: '; export DASHSCOPE_API_KEY; echo
export HTTPS_PROXY=http://192.168.10.171:16005
python3 demo/server.py --bind "$(ipconfig getifaddr en0)"
```

默认使用阿里云北京地域的 `dashscope.aliyuncs.com`。密钥属于其他地域时，在启动前设置 `DASHSCOPE_BASE_URL` 为该地域的 DashScope API 基础地址（结尾为 `/api/v1`）；密钥和服务地域必须一致。服务输出 Mac 地址及配对码；在 iPhone 和圆屏各输入一次。每次重启服务要重新配对。

打开 iPhone App，输入地址和配对码，选择整个屏幕。圆屏填写相同地址和配对码并连接。圆屏按住侧键说话，每次点击都需要在圆屏确认，确认前查看 iPhone 屏幕。中文输入和发送确认还未实现，不能用本版本完成相关任务。用 Ctrl-C 停止 Mac 服务。

离开圆屏应用必须通过双击侧键打开应用环，返回 Yay；不可假定异常崩溃后 USB 一定恢复。运行时设备 USB 是 iPhone 键鼠，设备串口和刷写暂不可用。
