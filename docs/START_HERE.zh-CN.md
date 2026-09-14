# 从这里开始

这是一个 MoonBit 原生 MQTT 客户端库：让 MoonBit 程序连接现有消息服务器，
接收设备/应用消息，执行自己的规则，再发布结果。底层编解码复用现有开源包；
本项目实现异步连接、收发、确认、心跳、TLS、断线重连和失败语义。

首版可以本地运行。它还没有发布到 GitHub 或 Mooncakes，也没有经过真实硬件部署。

本机完整复跑：`./scripts/check.sh`。工具链、Paho 和本地测试 broker 已准备在本目录的忽略项中。

## 看什么

- [README](../README.md)：支持范围、API、运行与失败语义。
- [三个使用场景](SCENARIOS.md)：输入、业务规则、输出以及各自边界。
- [实测记录](VALIDATION.md)：哪些检查真正运行通过。
- [参赛与发布剩余事项](RELEASE.md)：工程完成和正式提交分开核对。

## 最直观的演示

启动本机 Mosquitto，然后运行：

```sh
./scripts/moon.sh run examples/scenario_runner
```

它会通过真实 broker 发出并收回样例消息，验证：同一个 Frigate 人员事件不会在
去重窗口内重复告警；自定义速度命令转换成 ROS Twist 的 JSON 字段；遥测和
应用层接收回执能完成往返。成功时会打印 `scenario fixture passed`。

另一个长期运行的例子：

```sh
./scripts/moon.sh run examples/temperature_controller
```

向 `demo/thermostat/temperature` 发 `{"temperature_c":28}`，输出一次 ON；
发 27 保持原状态；发 26 输出 OFF。状态是示例控制器自己的逻辑状态，不是实际继电器反馈。
两个示例都支持 `MQTT_TEST_HOST` / `MQTT_TEST_PORT`，默认 `127.0.0.1:1883`。

## 当前最重要的约定

QoS 1 成功只表示收到 broker 的 PUBACK，不表示机器人或设备真的执行。
若数据已开始发送，但断线或确认超时，API 返回 `OutcomeUnknown`，由业务判断能否重试。
重连使用新的 clean session，恢复订阅；离线期间可能缺消息，不自动重放旧命令。

如果要继续做真实应用，优先接入一个现有 MQTT 数据源，保留其原始消息作为测试样本。
Frigate 和 ROS 目前是按公开格式构造的契约示例，不是三个已部署的用户案例。
