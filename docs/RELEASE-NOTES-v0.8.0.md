# 0.8.0 — 软件验收版本

此版本已由 [0.8.1](RELEASE-NOTES-v0.8.1.md) 替代，后者修复新 macOS 环境的 TLS 1.3 双向认证问题。

2026-10-31 已发布：[GitHub Release](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.8.0) · [Mooncakes](https://mooncakes.io/docs/Strangelight-Merser/moon-mqtt-client)。

固定工具链升级到 `moonc 0.10.17+d971670cd`（`moon 0.1.20261023`），满足
`moonc >= 0.10.16` 的比赛要求。依赖版本与 SQLite schema 2 保持不变。

- MQTT 5 Receive Maximum 用尽时，SUBSCRIBE/UNSUBSCRIBE 可越过等待额度的
  PUBLISH；发布顺序和业务队列容量保持原有约束。
- 心跳使用明确状态，PINGRESP 超时从 PINGREQ 写入完成后计算。
- DISCONNECT 本地写入已完成后，随后到达的 reader close 不再覆盖成功结果。
- 超时读取子协程开始时再次检查会话状态，避免关闭后访问传输连接。
- 明确断线统计含失败恢复拨号；恢复运维工具保留存储失败的非零退出及诊断。

本版本整合已有的软件修复与独立 broker、故障、持久化和包消费者验收入口。
具体提交、测试结果、发布状态和安装验证见 [验收记录](exec/release-v0.8.0/STATUS.md)。

支持原生 macOS/Linux。MQTT 5 为应用子集，不支持 QoS 2；持久 outbox 不代表
设备恰好执行一次。HA 示例的软件模拟与真实 ESP32/GPIO 验收分开，硬件 B2
尚未完成，不属于本次软件发布声明。恢复运维工具的 resolve/resume 仍为实验功能。
