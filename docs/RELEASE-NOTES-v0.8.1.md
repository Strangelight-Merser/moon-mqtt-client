# 0.8.1 — macOS TLS 1.3 双向认证修复

macOS TLS 运行时改为明确加载 Homebrew OpenSSL 3，移除系统旧 LibreSSL 路径。
依赖更新为 `Strangelight-Merser/async-tls@0.1.1`；固定 MoonBit 0.10.17 不变。

macOS 安装要求：`brew install openssl@3`。客户端仍验证服务器证书和主机名，
保留 TLS 1.3，不降低协议版本或放宽证书验证。MQTT API、持久化格式和测试断言不变。

修复背景、原始失败、平台范围、完整验收和实际发布状态见
[0.8.1 验收记录](exec/release-v0.8.1/STATUS.md)。物理 B2 未验收。
