# 软件验收关闭记录（2026-10-13）

冻结候选 `978966cdf5188d4ceae27cf7ab6a00d916f48ebc` 已通过原完整双平台 Release 和原 E06 门槛；同一 Linux 原生可执行文件的两份外部长跑记录已补齐逐拨归属。软件侧这三项缺口关闭。硬件 B2 的独立物理输出与执行次数证据仍缺，整体 Release 保持 HOLD。

测量于 10 月 12 日开始；本记录包括 10 月 13 日对保存证据的复核。解析器修复提交 `f5d00f5c9b415a2d4ac0926af1448af9178cc359`（包含生命周期、SO_ERROR 首次定态与调用入口时钟修复） 只取得工具回归与校准结果，不继承冻结候选的完整 Release PASS。后续文档提交也不改变被测候选。原测试、场景、阈值、依赖、API 和持久化强度未改动。

## 原完整验收

预注册为 [SOFTWARE_PROTOCOL_20261012.json](SOFTWARE_PROTOCOL_20261012.json)。固定 MoonBit `0.1.20260904`；macOS arm64 本地 APFS 工作树及 Ubuntu 24.04 x64 独立 runner，各执行一次原 `scripts/check.sh release`。性能测量所在主机未并发本任务的其他负载；保存了磁盘、挂载、负载、进程及工具版本。没有 RAM 盘、同步强度调整、降低工作负载或挑选重跑。

| 项目 | macOS | Linux |
|---|---:|---:|
| 原 Release 场景 | 34 PASS，11 原清单 N/A | 45 PASS |
| Native 测试 | 165/165 | 165/165 |
| 隔离 library / operator 消费者 | 34/34；9/9 | 38/38；9/9 |
| 原 benchmark | 13×3 与原 trace A/B 完成 | 13×3 与原 trace A/B 完成 |
| 原 TCP / mTLS soak | 各 1800 秒、100 次恢复 PASS | 各 1800 秒、100 次恢复 PASS |
| 原 evidence-integrity | PASS | PASS |
| 起止源码、完整级别 | source_unchanged=true；full_level=true | source_unchanged=true；full_level=true |

Linux 原完整 Release 在 [38263817517 的 release job](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/38263817517) 通过。该 workflow 整体仍为失败，因为两条外部观测 job 的旧后处理报错；没有把整体状态描述为成功。[冻结候选 Core](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/38263818066)、[Integration](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/38263817557) 也已通过。

## E06 裁定

下表直接取原比较器，CV 为 population CV；均为百分比。每平台三对样本，5 秒预热、30 秒采样，原 off/on 顺序。

| 原指标 | 原上限 | macOS | Linux |
|---|---:|---:|---:|
| 吞吐 CV：off / on | 各 15% | 0.173 / 0.326 | 0.429 / 0.468 |
| p99 CV：off / on | 各 15% | 3.021 / 0.976 | 0.670 / 1.026 |
| trace-on 吞吐下降 | 20% | 0.273 | 1.087 |
| trace-on p99 上升 | 25% | −2.672 | 1.169 |
| 原比较器裁定 | within_trigger | PASS | PASS |

当前候选在预先指定的持久存储与串行执行环境中，通过了原门槛和完整验收，E06 当前候选验收缺口关闭。历史异常吞吐和每一次波动的根因仍未全部确定；本次结果不证明单一环境因素具有因果作用，也不把旧失败改为成功。之前 fsync／观测成本的诊断仍按其原条件保留。

## 逐拨证据与修复

旧关联器只按 TCP 四元组搜索全部历史 accept。长跑复用源端口后，一个新 socket 会撞上过去的 accept，从而报“missing/ambiguous”。两份 raw buffer 都完整、无丢失，原 soak 的事件守恒、结果账、恢复和资源门槛已通过；错误发生在结束后的关联步骤。

修复使用同一主机 `CLOCK_MONOTONIC` 的严格生命周期包含关系：**精确四元组相同，且 connect 记录 < accept < close 记录**，必须唯一。当前探针在 connect 系统调用入口取时钟、返回后保存实际返回码和四元组，close 在系统调用前记录。旧捕获的 connect 时间在返回后，重放继续严格使用其较窄窗口；边界外记录不会通过“时间接近”补配。resolver 分配／释放标识逻辑拨号，socket life 区分其 Happy Eyeballs 尝试；重叠原生生命周期、缺失／倒序 close、重复 peer、无数值完成状态均失败。TCP 完成态以首次有效结果定态，后续 SO_ERROR 清零不能改写失败。TCP 错误只读真实 connect 返回或成功 getsockopt 的 SO_ERROR，不能拿 close errno 或 getsockopt 调用失败的 errno 冒充连接结果。

macOS Python 默认 monotonic 时钟与 C CLOCK_MONOTONIC 不同域，旧 macOS 校准不能支撑新增严格时间窗口。已将外部 Python 记录明确改为 CLOCK_MONOTONIC，并重新完成工具校准；没有对旧记录平移时钟或宣称它们通过新规则。Linux 旧校准与长跑原始记录使用同域时钟，均可直接严格重放。

修复期间的 [Linux 校准 38339933950](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/38339933950) 在第 24 个案例暴露另一观测缺陷：实际 accept 比 connect 返回后记录早 82,254 ns，导致保守误拒。原失败、完整 raw 和日志保留在 `_build/remote-dial-calibration-6d66049/`。已把探针取时点移至调用入口，并加入强制“先 accept，connect 再返回”的 C 时序控制；同一控制配旧探针确定失败，新探针通过。没有对这份旧失败记录补时间或宣称重放成功。

| 保存的 Linux 长跑 | TCP | mTLS |
|---|---:|---:|
| 原持续时间 / 恢复数 | 1800 秒 / 100 | 1800 秒 / 100 |
| resolver 逻辑拨号 | 104 | 104 |
| 其中预热 / 主流程 | 3 / 101 | 3 / 101 |
| socket 生命周期 | 301 | 287 |
| 实际成功 CONNACK | 104 | 104 |
| 失败 TCP 尝试（实际 SO_ERROR=111） | 197 | 183 |
| 排除的 Paho 探测连接 | 101 | 101 |
| 主流程 connected / disconnected | 101 / 100 | 101 / 100 |
| 原 buffer 丢失 | 0 | 0 |

失败 TCP 尝试是逻辑拨号内部的连接尝试，不等于失败的 MQTT 拨号。本次两条长跑的 104 个逻辑拨号都取得成功 CONNACK。每个 mTLS 成功 peer 都有实际服务器握手成功、CERT_REQUIRED 验证与客户证书指纹。测试 gateway 终止 TLS 后通过本机 TCP 转发给 Mosquitto，这是外部阶段诊断；原完整 Release 另有直连 Mosquitto mTLS 验收。

四份实际原生文件——完整 Release 的 TCP/mTLS driver，以及外部观测的 TCP/mTLS driver——逐字节 SHA256 相同：

```text
08af7f45ef11a3ffaec60cd0e42e5c21074b3e7b5567a56025860603df19d054
```

旧外部 wrapper 在关联失败后未写出 source-end，因此不声称它有结束源码快照。离线重放通过实际二进制哈希、观测前源码清单、observer 哈希和完整 Release 的起止冻结快照绑定同一候选。新 wrapper 在后处理之前保存 source-end。原 `dial-report.json` 的 complete=false 与原失败 workflow 均保留；新结论写入独立 replay 文件。早期试验性的 `corrected-association.json` 与 `_build/dial-association-repair/` 未获资格，不作为关闭依据。

解析器修复的独立回归覆盖端口/FD 复用、历史 accept、后续 Paho 错配、歧义窗口、重叠生命期、缺 close、时钟倒序、错误码误读、SO_ERROR 清零和 CONNECT/CONNACK 顺序，14/14 通过。macOS 新校准为八类各三次，24/24；真实缓冲溢出、已有输出不覆盖及缺行/错误 PID/错误 dial 等负控全部通过。修复后原 wrapper 的 TCP/mTLS 各 24 秒、3 次恢复冒烟通过，且起止源码一致；短测仅验证工具入口，不替代冻结候选的完整长测。Linux 旧 24 组校准记录已通过严格重放；最终工具的 [Linux 新校准 38340407535](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/38340407535) 同样 24/24，14 项回归、全部负控和强制 connect/accept 时序控制通过，下载后的 24 份 raw 再次严格重放通过。

长验收工作流改为手动选择冻结候选运行；分支 push 继续运行校准与回归，避免仅修改解析器就自动重新抽样一次完整 E06。

## 证据与交付入口

以下路径相对于本修复工作树 `/Users/huaiyi/.codex/worktrees/release-gap-repair/moon-mqtt-client/`。核心文件哈希与裁定见 [SOFTWARE_ACCEPTANCE_20261013.json](SOFTWARE_ACCEPTANCE_20261013.json)。

| 材料 | 路径 |
|---|---|
| macOS 原完整 Release | `_build/software-closure/978966cdf5188d4ceae27cf7ab6a00d916f48ebc/Darwin/acceptance/release-20261012T143437.826726Z-7749/` |
| Linux 原完整 Release | `_build/remote-release-978966c/software-closure/978966cdf5188d4ceae27cf7ab6a00d916f48ebc/Linux/acceptance/release-20261012T143544.605089Z-3700/` |
| 原外部 TCP / mTLS | `_build/remote-dial-tcp-978966c/dial-tcp/`；`_build/remote-dial-mtls-978966c/dial-mtls/` |
| 严格重放 | `_build/dial-reanalysis-f5d00f5/tcp-replay.json`；`mtls-replay.json`；`linux-calibration-replay.json` |
| 新 Linux 校准 | `_build/remote-dial-calibration-f5d00f5/summary.json` |
| 新 macOS 校准 | `_build/dial-calibration-f5d00f5/summary.json` |
| 新工具入口短测 | `_build/dial-soak-tcp-f5d00f5-smoke/`；`_build/dial-soak-mtls-f5d00f5-smoke/` |
| 候选包 | 两个平台完整 Release 目录中的 `candidate.json` 与 `candidate-assets/` |

候选库为 `0.7.1-audit-978966cd-8adeccb85ec2`，运维包为 `0.1.0-audit-978966cd-55923ee8f2a2`。这些是已验收的审阅包，未发布为新 registry 版本。两平台库 ZIP 的 311 个 entry 内容相同，写入顺序不同，导致 ZIP 哈希不同；按对应平台清单核对，不混用哈希。运维 ZIP 两平台完全相同。

离线重放入口（使用已安装项目 Python 依赖）：

```sh
python tests/closure/dial_association_test.py
python tests/closure/replay_dial_evidence.py \
  --capture _build/remote-dial-tcp-978966c/dial-tcp \
  --release _build/remote-release-978966c/software-closure/978966cdf5188d4ceae27cf7ab6a00d916f48ebc/Linux/acceptance/release-20261012T143544.605089Z-3700 \
  --output _build/review-tcp-replay.json
```

输出路径必须为新文件。mTLS 使用相应的 capture 目录。重放会核对原完整 Release、源码、实际二进制、observer 与原始记录哈希，并重建逻辑拨号/客户端事件守恒；不会改写原失败报告。

剩余阻塞是 B2 物理输出、独立 0/1/2 次执行观测及正式硬件验收。逻辑状态、自报计数与软件 soak 不能代替这些材料。本轮未更改硬件、固件或旧桌面交付，也未合并、打 tag 或发布。
