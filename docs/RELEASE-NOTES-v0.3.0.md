# v0.3.0 — 双向 TLS

## 新增与修复

- 新增 `ClientIdentity`：PEM 客户端证书链与未加密私钥，支持双向 TLS。
- CLI 新增成对的 `--cert` / `--key` 参数，要求启用 TLS。
- 身份文件加载、格式及密钥不匹配归为 `InvalidConfig`；TLS 握手拒绝、TLS 1.3 延迟告警和首次 MQTT 握手期间的 TLS 关闭归为 `TlsFailure`。
- 修复空闲读取取消污染 TLS 状态、已关闭 TLS 再次读写访问已释放句柄的问题；新增两个独立回归测试。
- 修复 TLS 单元测试的 TCP 资源泄漏，以及 workspace 构建后演示程序的路径定位。
- TLS 传输采用 `Strangelight-Merser/async-tls@0.1.0`，基于官方 async 0.21.3，C 符号独立，可与官方 TLS 同时链接。

范围仍为 native MQTT 3.1.1、QoS 0/1、clean session；不支持 MQTT 5、持久会话、加密私钥或硬件密钥。

## 验证

最新本地复验：macOS ARM 与 Ubuntu 24.04 x86_64 仿真均通过 39 项单元测试、25 项集成测试、10 项协议故障注入、独立 TCP/mTLS 消费者及四个演示场景，启用 FD 泄漏检查。

macOS 心跳和 EMQX 重连各有一次失败，重跑后通过；未确定根因，保留全部失败日志。EMQX 重跑为 4/4。此前 600 秒 mTLS soak 的结果及本轮验证边界见验收报告。

现有错误分类边界：TLS 握手阶段的连接超时分类、不可读 CustomCA 文件分类尚未完全统一；本次未宣称整个错误契约已经收敛。

详见 [新修复验收报告](V0.3-FIX-VERIFICATION-2026-09-16.md)。

## 发布前追加验证

托管 Linux CI 暴露了空闲读取取消及关闭后读取的生命周期问题，已修复；TLS 单元测试增至 14 项，workspace 共 41 项。原验收测试和时限保持原样。

Linux CI 的 broker 仅监听 IPv4，因此回环解析优先选择 127.0.0.1。上游 async 0.21.3 的 Happy Eyeballs 对重复 IPv6 地址逐项等待；此前托管环境的 30 次连接实际全部成功，但总计约 24 秒，超过验收的 20 秒。此环境配置只影响 CI，不改变客户端的 IPv4/IPv6 策略。

## 发布记录

发布流程进行中：远端 CI、合并、Mooncakes 两个模块发布、注册表干净安装及附件校验完成后记录结果。v0.2.0 标签保持原位。
