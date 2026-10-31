# E06 测量与 Linux 诊断修复记录

本次修复测量可审计性与证据复算，不宣称已解决历史 Linux 吞吐不稳定。基于 `5cde198ed7a5257127b372f8bf30546c8a32f3a8` 修复，代码提交 `3f8deeee4300921377441d3294dad8e9f2ee3417` 已推送到 `codex/linux-sync-diagnostic`；同提交的远端 Core、Integration 和独立 Linux 诊断均已完成，但没有形成新候选的正式 Release 证据。原 Word 方案与旧失败产物均未改动，Release HOLD 保持。

## 实现

- 原 native benchmark 的计时起点、stage 日志之后的消息完成终点、10µs 桶和 ≥100ms 溢出证据保留。每 worker 预分配 4096 条 Int64 尾记录，热路径只写内存，循环结束后输出 sequence、worker、phase、容量和丢失计数。极长延迟先在 Int64 上截断桶号再转 Int，避免转换溢出。
- `measurement_audit.py` 是 v2 解析器。尾记录计数、唯一键、完成区间、worker、整数范围、最大值和守恒全部校验；p99 按原分位秩定位有限桶或精确尾记录，保留 `rank/tail_rank/ns`。原 `latency_us` 删失字段继续作为原始证据，新 `measurement.quantiles` 提供可复算值；没有将旧结果回填为精确值。
- benchmark 的 stdout 及 identity.json 绑定唯一 trial token。六次 trace A/B 逐项重算完成数、时间、吞吐、原 histogram 和新尾分位数，再计算四个 population CV、median 对比及 verdict。stored verdict、summary、每轮 result、实际二进制哈希、源码快照、debug-native 构建模式及协议不一致都会失败。新记录仍由现有 inventory 封存。
- 门槛仍为 CV ≤15%、median 吞吐下降 ≤20%、p99 上升 ≤25%；缺失或不可解指标不能通过。旧版数据不满足 v2 输入契约，不添加兼容分支。
- Linux 独立 `LD_PRELOAD` 观测器覆盖 fsync/fdatasync，保留 syscall 包围区间、线程 CPU、schedstat、返回值与 errno；逐次记录 FD 路径、设备/inode/type，并检查调用前后身份。FD 数字复用不会沿用上个文件的归属。固定缓冲区 131072 条，溢出、无调用命中或输出失败均非零退出。
- Linux 诊断入口保留原 stdout、SQLite 阶段、所有线程 stat/schedstat、进程 I/O、系统 I/O pressure/diskstats 和差分。固定 off1/on1/off2/on2/off3/on3、每轮 5 秒预热与 30 秒采样；校准失败即停止，不追加有利样本。它独立于正式 Release。

生产库、SQLite journal_mode=DELETE / synchronous=EXTRA、事务结构、API/schema 和原 held-ACK 测试文件均未修改。原独立 deferred-trace 驱动同步采用 v2 尾记录，避免解析入口失配。

## 已执行验证

| 验证 | 结果与边界 |
| --- | --- |
| MoonBit check / native / build | 通过；native 162/162。Core 的显式子集，不能称完整 Core 或 Release PASS |
| 测量资格测试 | 13 个 unittest 通过；包括损坏、丢失、截断、Int64、多 worker、分位秩跨桶、门槛两侧、伪造 verdict、来源错配、原始日志串换 |
| 原 held-ACK 100/160/500ms | 通过；完成数及溢出数分别 20/20、13/13、4/4；新精确 p99 分别 107199000、167450000、507479000ns |
| native 短测及 raw 复核 | durable 主测试 3 次及 A/B 6 次完成；逐个独立复核 stdout identity、histogram、精确尾记录与派生指标。0 秒预热/1 秒采样，仅接口资格验证 |
| deferred-trace A/D smoke | 两个诊断条件通过；不构成正式 A/B 开销结论 |
| 原 evidence_inventory 测试 | 通过；失败仍封存嵌套证据 |
| Linux 观测器资格校准 | Linux aarch64 Docker、GCC `-Wall -Wextra -Werror`；负控、50ms 正控、容量溢出、输出创建失败、flush 失败、关键调用未命中六项通过；同时验证两种调用、errno、目录和 FD 复用 |

本地证据目录：

- `_build/acceptance/core-20260930T065243.400232Z-77533/`
- `_build/measurement-validity/measurement-validity-1790751004660665000/`
- `_build/measurement-v2-identity-smoke/`
- `_build/measurement-v2-deferred-smoke/`
- `_build/linux-measurement-qualification/sync-calibration-qualified/`

校准调试中的失败产物未替换；最终六项资格结果单独保存。实验室合成 fixture 与短测只验证测量链路，不替代原 13×3 benchmark、三对正式 A/B、长测或真实 Linux 根因证据。

## 剩余诊断与运行入口

在有固定 MoonBit 工具链及 Python 验收依赖的 Linux 环境：

```sh
python tests/closure/linux_sync_calibration.py --output _build/linux-sync-calibration-new
python tests/closure/linux_sync_study.py --output _build/linux-sync-diagnostic-new
```

第二条会构建并冻结当前 debug-native benchmark，先校准再固定执行三对诊断；输出目录必须新建。需要可读的 proc 数据与启用的 kernel.sched_schedstats。脚本不会自行修改 sysctl，缺失或禁用会报告诊断不完整。

新增 `.github/workflows/linux-sync-diagnostic.yml` 提供手动入口，并仅在推送 `codex/linux-sync-diagnostic` 分支时自动运行独立诊断。该工作流在临时 GitHub runner 上记录原值并显式启用 sched_schedstats；这是新增观测条件，不能将所得结果冒充原环境无干预性能。该工作流已按授权执行一次，运行与复核结果见下文。

Linux x64 端到端诊断现已执行。它包含 v2 测量、LD_PRELOAD 观测器和显式启用的 schedstats，与原历史环境并不完全相同。本轮证据可定位本轮偶发长尾，但不能回溯证明历史运行的根因或宣称其已被修复。正式新候选双平台 Release、逐拨外部证据和 GPIO 独立物理观测仍未完成。

## 项目推送授权

2026-09-30 用户确认执行本次诊断分支推送，并明确该项目今后的对外推送不再逐次索取授权。此授权覆盖 Git 推送；合并与版本发布仍遵循各自的任务范围和验收条件。

## 已完成的 GitHub Linux 诊断

代码提交：`3f8deeee4300921377441d3294dad8e9f2ee3417`，源码快照为 clean，355 个源码文件逐个与该提交核对一致。

- [Linux 诊断 36681894178](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/36681894178)：成功，固定三对 5/30 秒运行完整，无追加样本。
- [Core 36681894521](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/36681894521)：Linux/macOS 均成功。
- [Integration 36681894107](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/36681894107)：Linux/macOS 均成功。
- 产物 ID `11081119798`，ZIP 19,596,077 bytes；SHA-256 `1a999485b454ca633d858f752ee1c2f1e78e0eb0c1bc98fb402ec0bc100d81d5` 与 GitHub artifact digest 一致，ZIP CRC 检查通过，inventory 68 个文件全部哈希匹配。
- 六轮原始 identity、trial result、完成数、正式 histogram、精确 tail 和吞吐均独立复算通过。实际观测到 fsync 调用；校准另行证明 fdatasync 拦截、两类错误透传、FD 复用、目录同步和失败闭合能力。

| 顺序 | 完成数 | 吞吐 /s | 正式 p99 ms | ≥100ms 条数 |
| --- | ---: | ---: | ---: | ---: |
| off1 | 3502 | 116.640 | 11.16 | 2 |
| on1 | 3564 | 118.700 | 11.89 | 0 |
| off2 | 3544 | 118.034 | 12.29 | 0 |
| on2 | 3515 | 117.048 | 11.74 | 0 |
| off3 | 3574 | 119.043 | 11.35 | 0 |
| on3 | 3520 | 117.238 | 12.33 | 0 |

按原数值门槛计算，诊断数据的 off/on 吞吐 population CV 为 0.8353% / 0.6275%，p99 CV 为 4.2589% / 2.0888%，median 吞吐下降 0.6745%、p99 上升 4.7577%。因此该诊断数据的比较结果为 within_trigger；这不是正式 E06 或 Release PASS，因为它带有额外观测器与不同的观测条件。

### 两条长尾的定位

`off1` 中两条连续消息在正式终点分别为 138.219764ms 和 445.483260ms。对应日志前的 stage 区间与其中同步调用如下：

| 消息 | stage 区间 ms | fsync 区间合计 ms | 线程 CPU 合计 ms | runqueue 等待合计 ms |
| --- | ---: | ---: | ---: | ---: |
| bench-sample-0-3014 | 138.145796 | 133.728867 | 0.646124 | 0.079940 |
| bench-sample-0-3015 | 445.391578 | 439.077025 | 0.866835 | 0.032991 |

两条均有 16 次 fsync，路径归属于 outbox.sqlite3 或 outbox.sqlite3-journal，同一 TID 4102。较长消息约 98.6% 的 stage 墙钟时间位于 fsync 内；单次 journal fsync 最长 156.350907ms。正式延迟与 stage 区间分别保留，未互相替换。

这支持本轮长尾主要出现在同步系统调用内部，CPU 执行与运行队列等待不是主要耗时。同期约一秒的系统 I/O pressure full 增量为 371170µs，sda 写入扇区增量为 784600，而本进程 write_bytes 增量为 6488064 bytes；这些系统级指标只作相关旁证，不能直接归因为其他进程、具体磁盘、宿主机限流或 noisy neighbor。

runner 的工作目录在 `/dev/sda1`、ext4，记录到 `journal_async_commit,nobarrier,commit=30,data=writeback` 等挂载参数。诊断没有修改挂载配置，没有测试断电持久性，也没有以这些环境特性更改生产库的持久化保证。

本轮未复现历史 off/on 吞吐 population CV 97.75% / 49.33% 的整体崩落。固定预算结束后停止，不追加样本，不从这一次较稳定运行推断历史问题已解决。下一步若继续收敛存储环境原因，应另行冻结可控持久存储环境与干预方案，并保存新证据；当前不据此修改生产库或启动正式 Release。

### 复算入口

保留的本地产物：`_build/linux-hosted-20260930/verified-36681894178/`。本轮摘要计算结果与可执行复算脚本已保存于 `docs/exec/gap-repair/linux-sync-20260930/`：

```sh
python3 docs/exec/gap-repair/linux-sync-20260930/analyze.py \
  _build/linux-hosted-20260930/verified-36681894178/linux-sync-diagnostic
```

远端 artifact 依工作流设置保留 30 天；本地已保留校验通过的完整 ZIP 和解压数据。
