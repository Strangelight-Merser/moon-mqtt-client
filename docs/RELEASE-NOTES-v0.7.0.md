# v0.7.0 — 原生传输与可恢复投递

本版本集中交付原规划 v0.4–v0.7 的软件能力，未单独发布中间版本。默认仍为 MQTT 3.1.1、TCP 和 clean session。

## 新增能力

- 原生 WS/WSS：校验 `mqtt` 子协议，有界二进制帧与 MQTT 字节流适配，复用 TLS/mTLS；CLI 支持 `--ws-path`。
- 可恢复 QoS 1：显式 `ResumeSession`、稳定 DeliveryId 和 delivery handle；跨连接保留 packet ID、DUP 与次序，等待者取消不取消投递所有权，broker 会话丢失明确终止。
- SQLite 持久 outbox beta：在进程重启后恢复投递，事务先于网络可见性，PUBACK 后先提交删除再完成 handle；有界记录/字节/页、绝对过期时间、独占所有权与损坏/磁盘满失败语义。
- MQTT 5 应用子集：数值 reason code、Session/Message Expiry、Receive Maximum、Maximum Packet Size、User Properties、Response Topic、Correlation Data；协商额度不阻塞控制报文，重传不延长过期时间。
- HA 主机示例：discovery、availability、相关状态查询和过期/去重控制消息，附独立设备模拟器。PUBACK 不被当成实际设备执行确认。

## 升级与失败边界

使用 `Config::new` 的旧默认调用保留原行为。公共 `Config`、`Message` 和投递诊断结构新增字段，手写完整结构体字面量的调用方需要更新。原 `publish` 仍返回 Unit；详细 ACK 使用 `publish_detailed` 等明确 API。恢复投递必须显式选择对应会话与句柄 API，普通 publish 不会静默变成离线重放。

持久 outbox 使用 schema2；早期未发布的 schema1 和未知 schema 将拒绝打开，不删除、不自动迁移。进程可能在 broker 已确认但 SQLite 删除提交前崩溃，因此存在重复投递窗口，业务需幂等处理。可能已发送的过期/阻塞记录保留用于核对；不会自动重放物理命令。

MQTT 5 只覆盖应用子集，不含 QoS2、Topic Alias、Subscription Identifiers、共享订阅或 Enhanced AUTH。仅支持原生 macOS/Linux；实际 HA 实例、ESP32/GPIO、浏览器与 MCU 均不在验证声明内。

## 验证入口

固定 MoonBit 工具链下运行 `scripts/check.sh`；WS/WSS 另由 EMQX 验证。`tests/registry_roadmap.py --package <ZIP>` 验证解包后的独立依赖，发布后去掉 `--package` 从 Mooncakes 安装，复用相同 MQTT5/WS/WSS/HA 主机验收断言。实际执行结果及对应提交见 [验证记录](VALIDATION.md) 和 [执行状态](exec/STATE.md)。
