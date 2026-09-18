# moon-mqtt-client

[![原生客户端检查](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/workflows/check.yml)

面向 MoonBit 的原生异步 MQTT 3.1.1 客户端。连接现有 MQTT 消息服务器（broker），
订阅设备或应用事件，发布命令与状态，无需为每个应用单独编写 socket 收发循环。

本项目复用 **zbhzs1/moonbit-mqtt** 的报文编解码，在 **moonbitlang/async** 之上实现
连接生命周期、TLS 传输、请求跟踪、心跳和 clean session 断线重连。
目前属于早期实现，尚未通过 MQTT 协议一致性认证。

## 安装与构建

源码基线为 **0.3.0**，支持双向 TLS（mTLS）；本开发分支继续实现原生 WS/WSS。
发布与注册表安装状态见 [执行状态](docs/exec/STATE.md)，源码能力不代表已发布。添加注册表依赖的命令为：

```sh
moon add Strangelight-Merser/moon-mqtt-client
```

需要从源码运行示例或测试时，使用支持 native 目标的 MoonBit 工具链。
已验证版本为 `moon 0.1.20260904` 和 `moonc v0.10.12+1634b282e`（2026-09-07）。

```sh
moon update
moon check --target native
moon test --target native
moon build --target native
```

仓库中的 `scripts/moon.sh` 优先使用显式配置的 `MOON_HOME`，其次使用本地
`.tools/moon`，否则使用 PATH 中的 `moon`。工具链和构建产物不随源码分发。
版本、发布附件及校验值见 [GitHub Releases](https://github.com/Strangelight-Merser/moon-mqtt-client/releases)。

## 先运行状态同步演示

主演示由控制器进程和**独立的模拟设备进程**组成，通过真实 broker 通信。
设备由软件模拟，没有使用实际硬件。

先通过系统包管理器安装 Mosquitto 和 OpenSSL，再准备 Python 测试依赖：

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/integration/requirements.txt
./scripts/moon.sh build --target native
.venv/bin/python examples/mqtt_demo/demo.py
```

演示依次运行四种可复现情形：正常开关、设备执行命令但 PUBACK 丢失、broker 重启，
以及设备保持开启时控制器重启。输出分别展示：

- `desired`：带回差的温控规则所期望的状态。
- `result`：发送结果，取值为 `sent`、`not_sent` 或 `unknown`。
- `reported`：设备实际反馈的状态，通过 ID 与本次命令或查询关联。

主题和业务规则见[使用场景](docs/SCENARIOS.md)。

发布/订阅命令行工具直接使用本库公开 API；以下命令需要已有 broker 监听本机 1883 端口：

```sh
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  publish --host 127.0.0.1 -t lab/state -m ON --qos 1 --retain --stats
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  subscribe --host 127.0.0.1 -t 'lab/#' --count 1 --timeout-ms 3000
```

## 支持范围

- 原生 TCP/TLS，以及开发分支的 WS/WSS；TLS 支持系统根证书或自定义 PEM CA。
- 可选双向 TLS（mTLS）：PEM 证书链 + 未加密私钥；`Plain` 不能搭配客户端身份。
- MQTT 3.1.1、QoS 0/1、保留消息、遗嘱消息（Last Will）和用户名/密码认证。
- 订阅与取消订阅确认，包括逐主题的订阅拒绝结果。
- 有上限的发送队列、事件队列、报文大小和并发请求数。
- 默认 `CleanSession=true`：重连创建新会话，恢复已确认的订阅，随后发出
  `Connected(generation)` 事件。可显式选择 `ResumeSession`，通过投递句柄在同一
  进程作用域内恢复未确认的 QoS 1 发布。
- 任务和 socket 的生命周期由回调作用域管理。回调正常返回或调用 `disconnect()` 时
  发送 DISCONNECT；回调异常或被取消时直接关闭传输连接。

暂不支持 QoS 2、MQTT 5、离线接收队列、跨进程投递恢复、加密私钥、
浏览器和微控制器目标。

## 原生 WebSocket

配置 `transport=WebSocket("/mqtt")`，由 `tls` 决定 WS 或 WSS；WSS 使用同一套服务器验证与可选客户端身份配置。服务端必须选择 `mqtt` 子协议。只接收二进制消息，MQTT 包可以跨 WebSocket 帧和消息，报文大小仍受 `max_packet_size` 限制。

```moonbit
let config = @mqtt.Config::new(
  "broker.example.com", "native-wss-client", port=8084,
  transport=@mqtt.WebSocket("/mqtt"), tls=@mqtt.SystemRoots,
)
```

CLI 为现有命令增加 `--ws-path`；以下需要已配置 WSS 的 broker：

```sh
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  publish --host broker.example.com --port 8084 --ws-path /mqtt --tls \
  -t lab/state -m ON --qos 1
```

私有 CA 使用 `--ca`，双向 TLS 再提供 `--cert` 与 `--key`。客户端不协商 WebSocket 压缩扩展，不提供 HTTP 代理或浏览器适配。开发验收命令与实际结果记录在 [执行任务](docs/exec/TASKS.md)。

## API 示例

以下示例展示可运行程序使用的回调形式。将本库导入为 `@mqtt`，将
`moonbitlang/core/encoding/utf8` 导入为 `@utf8`。

```moonbit
async fn main {
  let config = @mqtt.Config::new("127.0.0.1", "my-moon-client")
  @mqtt.with_client(config, async fn(client) {
    let results = client.subscribe([
      { topic: "lab/temperature", qos: @mqtt.AtLeastOnce },
    ])
    if results == [@mqtt.Granted(@mqtt.AtLeastOnce)] {
      client.publish("lab/status", @utf8.encode("ready"),
        qos=@mqtt.AtLeastOnce, retain=true)
    }
    // 在这里处理 Connected、Disconnected 和 MessageReceived 事件。
    // 长期运行的订阅者必须持续调用 next_event() 消费事件。
  })
}
```

需要对 PUBACK 丢失或短暂断线进行有界恢复时，显式使用持久会话与投递句柄：

```moonbit
let config = @mqtt.Config::new(
  "127.0.0.1", "exclusive-stable-client-id",
  session_policy=@mqtt.ResumeSession,
)
@mqtt.with_client(config, async fn(client) {
  let delivery = client.submit_delivery(
    "command-20260918-001", "lab/command", @utf8.encode("ON"),
  )
  let status = delivery.wait()
  // Acknowledged means a matching broker PUBACK was received. Reconcile
  // TerminalOutcomeUnknown with application state before issuing a new command.
  match status.terminal_cause {
    Some(@mqtt.BrokerSessionLost(_)) => () // establish a new logical session
    Some(@mqtt.DeliveryAttemptsExhausted(_)) => () // reconcile before retry
    _ => ()
  }
})
```

`submit_delivery` 只在连接就绪时非阻塞接收，受现有发送队列和 `max_inflight`
上限约束，不是离线队列。断线后仅在 broker 返回 `Session Present=true` 时，才按原
packet ID 和顺序重发；已经开始写入的发布设置 `DUP=1`。等待句柄被取消不会取消投递。
该能力只保存当前进程作用域内已接收的投递，要求稳定 client ID 由单一客户端独占；它不
恢复进程重启前的内存，也不保证设备执行或应用层恰好一次。

`with_client` 在首次连接成功后调用回调；首次连接、CONNACK 或 TLS 失败会直接返回给调用方。
连接曾经建立后发生的故障会触发有次数上限的重试。`wait_connected()` 可等待重连完成；
断线期间调用发布、订阅或取消订阅会返回 `NotConnected`，请求不会进入离线队列。

公共 CA 证书可配合端口 8883 和 `TlsMode::SystemRoots` 使用；私有 CA 使用
`TlsMode::CustomCA("path/to/ca.pem")`。设备接入需要客户端证书时传入
`client_identity`（证书链 PEM 与未加密私钥 PEM）。连接配置中的主机名也是
TLS 验证使用的主机名，没有关闭证书验证的选项。身份文件在每次新建连接时重新读取。
文件格式、缺失或证书/私钥不匹配会得到 `InvalidConfig`；握手或服务器证书验证失败会得到
`TlsFailure`。CLI 使用成对的 `--cert` / `--key`，并且必须同时指定 `--tls`。

## 投递结果与失败语义

超时、排队、取消和失败分类的完整约定见[运行时契约](docs/API-CONTRACT.md)。

| 结果 | 能确认什么 |
|---|---|
| QoS 0 发布正常返回 | 传输写入完成；没有 broker 确认。 |
| QoS 1 发布正常返回 | 本次连接收到匹配的 PUBACK；不能据此确认下游处理完成或设备执行了动作。 |
| `NotSent` | 请求在开始写入前失败。 |
| `OutcomeUnknown` | 写入已经开始，但操作结果未确认，包括部分写入和确认丢失。 |
| 请求返回 `Backpressure` | 发送队列或在途请求达到上限，无法接受本次请求。 |
| 事件队列或控制队列溢出 | 客户端报错并终止连接。 |

`operation_timeout_ms` 限制整个操作的耗时，默认 5000 ms，包含排队、写入和等待确认。
单次 socket 写入另受 `write_timeout_ms` 限制；PINGRESP 的等待时间从 PINGREQ
**写入完成**后开始计算。操作超时会关闭连接并结束**所有**待完成请求，旧请求标识不会带入新连接。
是否重试结果不确定的操作由应用决定；适合重试的业务可使用幂等状态设置命令或应用级命令 ID。

PUBACK、PINGREQ、DISCONNECT 等协议控制报文使用预留且有上限的槽位，业务发送队列已满时
仍可提交控制报文；控制槽位也耗尽时，客户端报错终止连接。

收到的 QoS 1 消息进入有界事件队列后即被确认，此时应用可能尚未处理。
事件队列仅保存在内存中，QoS 1 可能产生重复消息。clean session 重连间隙可能丢失消息，
重新订阅时 broker 也可能再次投递保留状态。本库不保证恰好处理一次、持久投递或订阅无中断。

默认限制：

| 配置 | 默认值 |
|---|---|
| 业务发送队列 / 控制槽位 / 事件队列 | 64 / 16 / 128 |
| 待完成操作数 / 单个报文大小 | 32 / 65,536 字节 |
| 连接、操作、写入、PINGRESP 超时 | 各 5 秒 |
| keepalive | 30 秒 |
| 连续重连次数 | 最多 10 次 |
| 重连间隔 | 从 250 ms 增长至 5 秒，并加入每个客户端独立的随机抖动 |

`Client::set_reconnect_seed` 可在测试中固定重连抖动。已确认的订阅过滤器会一直保存在内存中，
直至取消订阅；应用应控制订阅集合的大小。

`Client::stats()` 返回只读快照：连接代次与状态、业务/控制/事件队列占用、待完成请求数、
重连和断线次数、结果未知次数及最近一次断线原因。快照不包含凭据和消息正文，也不依赖监控服务。

## 更多使用场景

[从这里开始](docs/START_HERE.zh-CN.md) 提供阅读与运行顺序。
[使用场景](docs/SCENARIOS.md) 给出以下三类用途的输入、规则、输出和失败边界：

1. 带回差、保留状态和在线状态的温度控制。
2. 有界 Frigate 事件去重，对已结束的人员事件发出提醒。
3. ROS 桥接的 JSON/基本类型契约，包含命令校验与回执。

示例使用测试数据，没有部署真实摄像头、机器人或 Zigbee 集成。
Frigate 和 ROS 部分提供补充适配契约：校验非有限速度值和空命令 ID，验证引号、反斜杠及
控制字符的 JSON 转义，所有输出负载均为 JSON。

## 验证

安装上述依赖后，可单独运行 Mosquitto/Paho 集成测试，或运行本地检查入口：

```sh
PYTHON=.venv/bin/python ./tests/integration/run.sh
./scripts/check.sh
```

`scripts/check.sh` 包含类型检查、单元测试、构建、集成测试、协议故障测试、
场景冒烟和独立消费模块验证。四场景演示、注册表安装验证、EMQX 和长时间压力测试单独运行：

```sh
# 独立模拟设备的四种状态同步场景
.venv/bin/python examples/mqtt_demo/demo.py

# 从 Mooncakes 安装到全新的临时模块，并完成 QoS 1 消息往返
.venv/bin/python tests/consumer_smoke.py --registry

# 固定版本的官方 EMQX 镜像：收发、拒绝订阅、重启恢复；需要 Docker
./tests/emqx.sh

# 30 分钟重启压力测试：1 KiB QoS 1、16 个并发任务、100 次断线恢复
# broker 默认仅记录警告和错误，日志大小有上限
MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python tests/soak.py \
  --duration 1800 --cycles 100 --artifacts tests/integration/artifacts/soak
```

已记录的验证包括 26 项单元测试、12 项集成测试、10 项协议故障测试、4 个状态同步场景、
EMQX 互操作，以及 30 分钟、100 次断线恢复的压力测试。长测的 RSS/FD 资源采样不可用，
尚不能据此确认持续负载下没有资源泄漏。

集成测试使用独立的 Mosquitto 和 Eclipse Paho 进程、仅监听回环地址的端口、临时证书及
报文故障注入。具体实测结果、对应 CI 提交与证据限制见[验证记录](docs/VALIDATION.md)。

## 许可证与上游归属

采用 Apache-2.0，详见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
依赖保持为独立软件包，各自保留原有署名和许可证；MQTT 报文编解码、异步运行时及 TLS
实现的贡献归属于对应上游项目。
