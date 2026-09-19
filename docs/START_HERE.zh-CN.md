# 从这里开始

这是一个 MoonBit 原生 MQTT 客户端库：让 MoonBit 程序连接现有消息服务器，
接收设备/应用消息，执行自己的规则，再发布结果。底层编解码复用现有开源包；
本项目实现异步连接、收发、确认、心跳、TLS、断线重连和失败语义。默认 MQTT 3.1.1，
同时提供 MQTT 5 应用子集、原生 WS/WSS、可恢复 QoS 1 和 SQLite 持久 outbox。

已公开在 [GitHub](https://github.com/Strangelight-Merser/moon-mqtt-client)，
注册表版本与安装验证记录见 [Releases](https://github.com/Strangelight-Merser/moon-mqtt-client/releases)。
**示例中的设备是模拟进程，没有真实硬件。**

本机完整复跑：`./scripts/check.sh`。工具链、Paho 与本地测试 broker 位于本目录的忽略项中。

## 看什么

- [README](../README.md)：支持范围、API、运行与失败语义。
- [运行时契约](API-CONTRACT.md)：超时、队列、取消与失败分类的正式约定。
- [三个使用场景](SCENARIOS.md)：输入、业务规则、输出以及各自边界。
- [本轮缺陷与修复](FINDINGS.md)：问题、原因、修复与验证方式。
- [实测记录](VALIDATION.md)：哪些检查真正运行通过。
- [发布与参赛清单](RELEASE.md)：已完成项与仍待完成的发布门禁。

## 最直观的演示：状态同步

一条命令跑完四个场景（正常开关、丢 PUBACK、broker 重启、控制器重启）：

```sh
./scripts/moon.sh build --target native
.venv/bin/python examples/mqtt_demo/demo.py
```

- 控制器只维护**期望状态**，收到设备反馈前不会显示“执行成功”。
- 设备是独立进程（同一库），反馈带回启动 ID、序号和关联命令/查询 ID。
- 反馈可保留，但控制器启动/重连后一定主动查询，只认自己这次查询 ID 对应的新反馈。
- 阈值 28℃ 开、26℃ 关；中间区间保持最近目标；首次处于中间区间且状态未知时先查询。

每条输出都把 `desired`（期望）、`result`（sent / not_sent / unknown）和
`reported`（设备实际反馈）分开，不混成一种“成功”。

## 其他演示

```sh
# 薄层发布/订阅 CLI（支持 QoS、retain、TLS、--stats 诊断）
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  publish --host 127.0.0.1 -t lab/state -m ON --qos 1 --retain --stats

# Frigate 去重 + ROS 命令/遥测契约（真实 broker 往返）
./scripts/moon.sh run examples/scenario_runner --target native
```

## 当前最重要的约定

QoS 1 成功只表示收到 broker 的 PUBACK，不表示机器人或设备真的执行。
若数据已开始发送但断线或确认超时，API 返回 `OutcomeUnknown`，由业务查询或重发幂等命令。
默认重连使用新的 clean session 并恢复订阅；离线期间可能缺消息，不自动重放旧命令。
显式选择 ResumeSession 与 delivery handle 后可恢复 QoS 1；持久化另需 with_durable_client。
这些模式的会话丢失、过期和重复投递边界见运行时契约。
取消未完成请求会中止当前连接（保守语义），并保证每个请求以“未发送”或“未知”结束。
