## 本轮决定：从“保留问题”转入“逐项关闭”，但不以改验收标准代替修复

**保护已交付的 `5ee6912f`、阶段包和桌面入口，在新 worktree 中完成修复候选。** 本轮不增加产品功能，不改变 v0.8 retry 默认、公共 API、schema 2 或持久化强度；也不执行合并、tag、registry 上传、正式发布或赛事提交。

这次需要纠正一个收尾方式：**历史事件无法追溯，不应让已经修复、可以验证的现有实现无限期等待；反过来，新实现通过也不能把历史失败改成成功。**

我重新核查了公开 `bc56bcd` 的相关源码、PR #8 状态以及 SQLite、MQTT、Espressif 官方资料。远端 PR #8 仍为 Draft，head 仍是 `bc56bcd9543fba989fbca44a6913e342a42e5659`。你提供的 `5ee6912f` 本地阶段结果，本轮尚未读取其原始日志，因此下文将其作为执行端必须核验的起点，不冒充新的独立实测。

**直接交给执行 agent：**

:chatgpt-content-reference{index="16"}[下载完整缺陷关闭任务包](sandbox:/mnt/data/moon_mqtt_defect_closure_20260929.zip)  
:chatgpt-content-reference{index="17"}[完整执行任务书](sandbox:/mnt/data/moon_mqtt_defect_closure_20260929/EXECUTION_PLAN.zh-CN.md)

包内还包含实验预注册和关闭裁定 JSON 模板，默认均未执行，没有预填 PASS。

---

# 一、五个问题的明确处理决定

| 问题 | 本轮采取的动作 | 真正的关闭标准 |
|---|---|---|
| **E06 trace A/B 不可判定** | 先分离日志、trace、存储与调度成本，再修实际阻塞或执行环境问题 | 回到**原程序、原指标、原阈值**，新候选原完整验收真实通过 |
| **历史 WSS 首败未归因** | 保留已知 local-write-completion 修复，完成强制时序及负控，分开缺陷与历史事件 | 可关闭现有结算缺陷；历史事件继续注明“原因未确定”，不无限正常重跑 |
| **逐拨证据只来自诊断 build** | 对最终候选本身补外部 socket／broker 阶段观测 | 同一候选二进制、逐拨归属和数字原因可核对，不能转贴旧诊断结果 |
| **ESP 无物理输出证据** | 核实板型与连接，再选择已确认输出和真实独立观测 | 实际 GPIO／灯／测量装置有独立记录；仅逻辑状态或自报计数不算 |
| **query 无 last_applied_command_id** | **先不扩协议**；利用现有设备侧 `applied` 记录做完整、同 boot 的命令对账 | 该窗口中原命令的逻辑 apply 可由设备记录关联；记录缺失或重启仍为 unknown |

其中最后一项是明确取舍，不是推迟判断：**当前已有设备侧记录能够支持本轮有限窗口的归因，就不应为了一个 query 字段引入新固件协议和新的兼容成本。**

---

# 二、执行起点：新 worktree，旧交付不动

执行 agent 先完成一次只读冻结：

```text
解析 5ee6912f 为唯一完整 commit
→ 核对其与 bc56bcd 的父关系
→ 核对两份新增脚本及实际工作区状态
→ 建立新的 defect-closure worktree
→ 冻结原测试、阈值、依赖、API、旧入口和包的指纹
```

新分支可以叫 `codex/defect-closure-20260929`。不得在旧 `eeb93be` 主目录修，也不得切换或覆写目前可用的 `5ee6912f` 工作区。

必须输出：

```text
BASELINE.json
PROTECTED_PATHS.json
WORKSPACE_MAP.json
INPUT_EVIDENCE_INDEX.json
EXPERIMENT_PROTOCOL.json
```

**保护范围包括原 tests、原 benchmark、原 E06 比较器和场景表。** 新白盒测试单独加文件；新诊断放 `tests/closure/`；需要门闩的源码变体放隔离实验副本，保存 patch、source hash、binary hash，不能当正式 candidate。

当前设备固件以本机实际版本为准。公开仓库 `.ino` 只是审阅参照，**不能覆盖已经修好的 CA/RX 分块版本**。

---

# 三、E06：先做能区分原因的实验，不再重复同一种 A/B 抽样

## 3.1 先把两份历史结果分开

原 c52 hosted Release 的三对样本，与 9/25 平衡六对、12 trial 不是同一次验收。后者 off/on p99 CV 为 20.831%／26.918%，仍不可判定；不能把它归并成 c52 原 run 的新解释。PR #8 也明确保留了这一证据边界。

本轮不做“第三轮再跑十二次，看看能不能好一点”。

## 3.2 已找到两个具体干预点

公开源码中：

**第一，SQLite trace 回调会做 SQL 展开和同步文件写入。**

`tests/benchmark_driver/measurement.c` 的 `identify()` 调用 `sqlite3_expanded_sql()`；`record()` 通过 `dprintf()` 写 trace 文件。这些操作发生在 trace 回调路径中。

**第二，durable/stages 的直方图包含一次异步日志输出。**

`workload()` 在 `handle.wait()` 返回后取得 `completed_ns`，随后 `log(stages)`；而加入直方图的 `elapsed_ns` 是日志返回后重新取的。因此这里统计的不只是 handle 完成延迟。trace-off 也保留这条日志路径。

这两个点是**可测量的开销来源，不是已经证明的历史根因**。SQLite 官方说明 PROFILE 回调发生在语句结束时；回调中的额外工作仍可能影响调用方继续执行的时刻。:chatgpt-content-reference{index="4"}

### 需要读的函数

| 文件 | 必读位置 |
|---|---|
| `tests/benchmark_driver/measurement.c` | `record`、`identify`、`trace`、`extension`、`moon_bench_trace_enable` |
| `tests/benchmark_driver/main.mbt` | `log`、`workload`、`barrier` |
| `tests/benchmark.py` | `trial`、`Peer.serve`、`trace_comparison`、A/B 执行顺序 |
| `tests/evidence_integrity.py` | 原 E06 门槛与 inventory 路径 |
| 本地 9/25 A/B harness | 实际构建对象、平衡顺序、原始十二份 trial |
| `durable_store.mbt` | 只读确认事务和同步配置，不以优化为名修改 |

## 3.3 固定诊断：四个条件，每个三次

新增**隔离诊断 driver**，导入相同候选库。保持消息、并发、SQLite 配置、数据库文件系统、时长与计时端点一致。

| 条件 | 唯一干预 |
|---|---|
| A | 原日志路径，trace-off |
| B | 原日志路径，trace-on |
| C | 相对 A，只把 stages／observer 的热路径落盘改成有界内存记录、结束后导出 |
| D | 相对 B，再把 trace callback 改成有界预分配记录、结束后导出 |

每个条件固定 **3 次，5 秒预热、30 秒采样**；顺序预先登记。另选一对 A/B 做外部 I/O／调度剖析，**剖析运行不并入正式性能数**。

新增记录至少包括：

```text
handle_return
stage_log_enter / return
trace_callback_enter / return
wire / ACK
fsync / fdatasync 与目录同步耗时
CPU、线程、文件系统与后台负载
```

内存缓冲必须有容量、记录数和丢失计数；溢出就把该诊断标为不完整，不能静默丢样本。

**C/D 只用于因果诊断，不是替换原 E06 的新指标。** 不能把缓冲后的好看 p99 填回旧测试结果。

## 3.4 根据结果修改，而不是先猜修法

- **若日志消费／证据目录造成阻塞：**修实际执行路径，例如本地非同步目录、及时完整消费 stdout、避免本轮测试并发。不能丢弃输出或停止未知用户进程。
- **若数据库同步延迟主导波动：**固定正常持久存储上的环境，排除有记录的竞争。不能移入 RAM 盘、改 WAL、放松 `synchronous`、批量提交或降低请求压力。
- **若库中多余阻塞被证实：**新增生产路径回归，只修具体阻塞，再跑相关可靠性测试。
- **若只是测量器 observer effect：**交付独立的新测量器和旧新对照，但在“原测试不改”的约束下，**它不自动关闭原 E06**。

SQLite 的同步设置直接关系崩溃和掉电边界，不能用降低其强度换取性能稳定。:chatgpt-content-reference{index="5"}

## 3.5 E06 的退出条件保持不变

最终候选仍须用原程序满足：

```text
吞吐及 p99 的各组 population CV ≤ 15%
p99 未落入无法确定上界的桶
trace-on 吞吐损失 ≤ 20%
trace-on p99 上升 ≤ 25%
```

随后完整原 Release profile 通过。

允许使用**事先指定的 macOS arm64 本地等价环境**，但必须记录环境，不在多台机器间事后挑最好结果；本地通过也不把旧 hosted run 改成通过。

**如果根因干预后仍不可判定，E06 不能伪关闭，但其他工作包继续完成。** 这次留下的应是“定位到了什么、排除了什么、哪项条件仍不满足”，而不只是又一组相似失败数字。

---

# 四、WSS：关闭已知实现缺陷，历史事件单独归档

## 4.1 保留现有修复，不重做状态机

保留 `Pending.local_write_complete`：

```text
writer.write 正常返回
→ 置 local_write_complete
→ 外层 timeout／task-group 收尾
→ pending 结算
```

保留 first-result-wins、未开始为 NotSent、可能写入但未完成为 OutcomeUnknown。

closeout 已有通过真实 `Session::write_one` 检查 marker 的测试；它不是单纯手工设置字段。**这项实现不应被再次拆掉重做。** 

必须重新审查的调用链：

```text
Session::request
→ Session::write_one
→ WebSocketWriter::write_once
→ Conn::send_binary / end_message / flush_frame_unprotected
→ Tls::write_once / flush_write
→ Session::abort_with / Pending::finish
→ Client::disconnect / worker join
```

同时核对实际固定 async 的 timeout／取消行为，而不是当前上游版本。

## 4.2 补齐的是时序证据，不是正常重复次数

先读取已有强制 deadline／scope-cancel 的 5/5 原始 marker。已真正命中的实验不必无意义重做；但要把新增回归保留到最终候选。

新增确定性矩阵：

| 时序 | 必须成立 |
|---|---|
| 未开始写 | NotSent |
| writer 已进入但没有完成 | OutcomeUnknown |
| peer 已有全包，本地 writer 仍挂起 | 不能仅凭 peer receipt 宣告成功 |
| writer 完成、marker 已真、pending 尚未结算 | 后续 reader close 不抹掉本地成功 |
| deadline 实际到达 | 根据真实写入边界判断，不把“时间接近”当命中 |
| scope cancellation 实际生效 | 正确清理；取消退出不冒充 MQTT 写成功 |

三种关闭来源：WS Close、TLS close_notify、transport EOF。每个适用组合固定三次，门闩使用条件／信号同步，不能只 sleep 后宣称命中。

保存：

```text
write-entry / write-return / marker-set
first-abort / pending-settle / worker-join
generation、slot 0、peer 报文、退出结果
```

隔离副本禁用 marker 后，目标回归必须失败；正常候选通过。保留 mutation 编译成功与回归失败的证据。

MQTT DISCONNECT 后不能继续发送 MQTT 控制报文，且发送方需要关闭连接；协议没有给它一个 PUBACK 式确认。因此不能将 WS Close 或 EOF 当成 DISCONNECT ACK。:chatgpt-content-reference{index="7"}

## 4.3 真 broker 验证继续保留原断言

对最终**未插桩候选**运行原 `tests/ws_interop.py`，特别是：

```text
订阅成功
→ 收到预期 QoS1 消息
→ disconnect
→ 进程退出 0
```

原测试正是对这条路径作退出断言，不能改成捕获并忽略 Unknown。

另加固定十次 subscriber-close 短组，全部保留。任何新正常场景失败立即保留现场、进入该具体反例，不用后面成功冲淡。

## 4.4 本轮采用的工程关闭标准

满足生产路径红绿／mutation、强制边界、原 broker 测试及最终平台回归后：

```text
已知本地完成结算缺陷 = CLOSED_ENGINEERING
历史首次 Linux 事件 = HISTORICAL_CAUSE_UNDETERMINED
```

**这两个状态可以同时存在。**

原先未保存的历史时序，不可能靠未来测试补成事实。工程上应保留原失败、列清未证实的假设，并建立复发时能定位的记录入口，而不是永久要求“先证明过去那一次到底发生了什么”。

但若新候选仍在正常场景发生同类失败，或负控存在未解释反例，**不能用这条规则将其归档免责**。

---

# 五、逐拨证据：下一次运行候选本身，不再交另一份诊断 build

这里主要是证据对象不一致，不是要求改生产 `disconnects`。

## 5.1 推荐路线：候选不改，观测放到外部

使用：

**候选独占 listener＋外部 socket 归属＋broker 侧结构化 TLS／CONNECT／CONNACK 记录。**

Paho observer 和健康检查走单独端口或明确不同连接身份，不再和目标候选混在同一个不可区分的流量集合里。

每个 attempt 关联：

```text
process instance / binary hash
attempt ID
socket lifetime 与四元组
broker boot ID
TCP 结果
TLS 阶段及数字 alert／验证结果
真实 CONNACK 数字码与 Session Present
对应 generation／客户端事件
```

失败在 CONNECT 前没有 client ID 是正常的；不能因此靠“时间接近”猜归属。FD 会复用，必须记录生命周期。

若 broker 需要小型诊断补丁，只增加 accept、TLS、CONNECT、CONNACK 的观测，**不改变认证、session 或报文行为**。OpenSSL info callback 可以提供 TLS 状态和 alert，但它本身不提供 MQTT CONNACK，需要在 MQTT 解析／发送点记录后者。:chatgpt-content-reference{index="9"}

日志采用有界记录，不能重演 always-on stderr 阻塞／panic；丢记录则该次观测不完整。PR #8 已记录过这种诊断输出影响普通 Integration 的真实失败。

## 5.2 先校准八类，再长跑

正常、initial 拒连、恢复 CONNACK 拒绝后成功、TCP refused、TLS 验证失败、TLS timeout、TLS 成功后 CONNACK 前 EOF、maintenance 到 ordinary 的转换，各三次。

必须有负例：

- 漏一条记录，gate 失败；
- FD 复用不误配；
- observer／probe 不算 candidate；
- 数字码来自真实报文／typed 值，不解析展示字符串。

校准成立后，最终 candidate TCP/mTLS 各 **1800 秒、100 次恢复**。原事件守恒、结果账、资源与恢复期限全部保留。

**观测到的是同一个候选二进制，但观测环境有扰动，这一点仍要标注。** 它补的是该候选的阶段证据，不是无观测性能；完整原 Release 另保留未插桩运行。

外部观测权限不足时，不关闭 SIP、不改变路由、不往用户机器安装常驻探针。只把具体缺失的观测点标为阻塞，其他工作继续。

---

# 六、ESP：先识别与校准，再实施物理输出；query 不急着加字段

## 6.1 白灯引脚不能从“同为 S3”推出

本轮公开检索没有确认 YN038-V1.1 的可信 LED 映射。

Espressif 官方 DevKitC-1 的早期板用 GPIO48，v1.1 用 GPIO38，且电源灯与可控 RGB LED 是不同部件。**这个事实恰好说明不能把官方板修订直接套到 YN038。** :chatgpt-content-reference{index="11"}

执行端先读取已存在的照片、板上丝印、购买附件、原固件／variant 以及本地 `b2_output`，生成：

```text
BOARD-PROFILE.json
板型／修订
普通LED、RGB或电源灯
GPIO与有效电平
Flash/PSRAM及USB占用
映射证据路径／hash
允许的动作
```

不再索要已有照片，不试扫引脚，也不按 38、48 轮流试。

## 6.2 两条可以真正完成的路径

**路径 A：板载 LED 的映射被可靠确认。**  
在固件副本中加入显式 board profile，默认禁用。使用现有、已授权且能实际看到该灯的相机／光传感器等独立记录验证。

**路径 B：白灯仍不明，但已有安全连接的外部负载或测量装置。**  
使用已确认空闲的 GPIO 驱动外接 LED，或由仪器观察电气输出。它可以关闭“MQTT 驱动物理输出”的项目验收，**但不关闭“白灯 GPIO 已识别”这一具体问题**。

如果只量到 GPIO 电平，就称 GPIO 电气输出验证，不称 LED 或机械动作验证。

必须避开本板实际占用的 Flash/PSRAM、USB 与 strapping 引脚；Espressif 文档明确列出 S3 的这些限制。:chatgpt-content-reference{index="12"}

没有接好的负载／仪器或映射时，执行 agent 完成 profile、可编译测试和明确接线步骤，留下精确的 `BLOCKED_PHYSICAL_OBSERVATION`。**不能把远程 agent 无法接线或无法看到灯的问题伪装成代码已解决。**

## 6.3 物理验收先证明测量器能分清 0、1、2 次

先做三种故意 apply 校准，再执行十条正式命令，覆盖 ON/OFF、同 ID 重发、冲突、过期、retained 重放、controller 重启和反馈抑制后的状态核对。

同样的 ON 写两次，最终电平不变。没有独立脉冲／执行观测时，只能证明状态变化，不能证明 apply 恰好一次。

固件更新必须从本地修复后的版本分叉，保留原镜像和 NVS，不重新配置 Wi-Fi。新镜像需要新 run，不能沿用旧 HIL 成功。结束后恢复原现场，并验证原 `5ee6912f` 桌面入口仍可用。

## 6.4 对 `last_applied_command_id` 的裁定

**本轮先不添加。**

公开固件的 `handleMessage()` 已在 apply 后输出：

```text
applied: id、state、applied_count
```

`status`／telemetry 提供 boot ID 和 count。query 的 `correlation_id` 则是本次 query 的 ID，不是原命令 ID。

因此首先补齐独立对账器：

```text
连续串口记录
＋前后同 boot 的 status
＋命令 ID／applied_count
＋独立 MQTT 命令捕获
```

有重启、记录断档、缺失 apply 时结果必须 unknown，不能把缺记录推成零次；新 query 只确认当前状态。

你报告已独立核对八条命令，执行端应先审查那套证据是否已经满足上述条件。**若已经满足，这个“有限窗口逻辑归因”问题可以关闭，不必再改固件。**

只有确实要求“串口缺席时，仅通过网络查询最近命令结果”，才需要新增 boot-scoped、只读、有限 receipt；一个单独的 last 字段仍不是永久完成账，更不是掉电 exactly-once。它不应进入 MQTT library 核心。

---

# 七、最终双平台验收：同一新候选，原套件，明确环境

完成修复后统一冻结新 commit/tree。执行：

```text
原 Core / Integration 的全部适用场景
＋原 13×3 benchmark 与 trace A/B
＋原 E06
＋TCP/mTLS 各1800秒、100次恢复
＋storage / recovery
＋Linux 固定 EMQX WS/WSS
＋候选 library/operator 隔离消费者
＋新增 closure 回归
＋完整 evidence inventory
```

现有 Release workflow 已包含 Linux、macOS 和独立 rolling compatibility，入口是 `./scripts/check.sh release`。无需为本轮另造一个含义更宽松的“Release-lite”。

**执行渠道二选一，提前指定：**

已有授权的测试 ref 和 CI；或者已获授权的 Linux x86_64＋Docker 与 macOS arm64 本地等价环境。

没有测试分支推送授权时，不暗推；没有 Linux 环境时，也不能拿旧 c52 的 Linux 42/42 拼接新 Mac 结果。

每个冻结候选、每平台一轮完整验收。失败后，只有**定位并实施了真实修复**才建立新修订重跑；不能因结果不好直接点 rerun。

---

# 八、给执行 agent 的任务依赖与退出方式

```text
S0 保护旧交付、核验输入、预注册
 ├─ S1 E06 根因干预与实际修复
 ├─ S2 WSS 工程缺陷关闭
 ├─ S3 exact-candidate 逐拨证据
 └─ S4/S5 ESP 物理路径与命令归因
          ↓
S6 同一最终候选双平台原套件
          ↓
逐项裁定、复现材料、证据包
```

同一主机上不并发 benchmark 和长测；一个 ESP 只允许一个 owner。WSS 或物理项阻塞时，其余独立包继续。

最终每项同时给出：

```text
historical_status
current_defect_status
candidate_acceptance
causal_claim
evidence
```

例如允许：

```text
WSS 已知结算缺陷：CLOSED_ENGINEERING
历史第一次异常：UNATTRIBUTED
新候选双平台关闭回归：PASS
```

不允许：

```text
历史第一次异常：PASS
```

因为它从来没有通过。

**这轮的完成目标不是再写一遍“仍然 HOLD”，而是把能修的实现缺陷修掉、能补的候选证据补齐、能通过现有设备记录解决的归因问题关闭。真正缺机器权限、接线或独立物理观测的，只留下那个具体阻塞；已经有明确关闭标准的工作，不再反复等待另一轮 Pro 才继续。**
