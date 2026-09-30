# E06 测量与 Linux 诊断修复记录

本次修复测量可审计性与证据复算，不宣称已解决历史 Linux 吞吐不稳定。基于 `5cde198ed7a5257127b372f8bf30546c8a32f3a8` 工作区修改，尚未提交或推送，也没有形成新候选的正式 Release 证据。原 Word 方案与旧失败产物均未改动，Release HOLD 保持。

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

新增 `.github/workflows/linux-sync-diagnostic.yml` 提供手动入口，并仅在推送 `codex/linux-sync-diagnostic` 分支时自动运行独立诊断。该工作流在临时 GitHub runner 上记录原值并显式启用 sched_schedstats；这是新增观测条件，不能将所得结果冒充原环境无干预性能。没有触发该工作流。

完整 Linux runner 尚未端到端执行；本地 aarch64 容器校准不能替代原 GitHub Linux x64 证据。当前没有足够新证据判定 fsync、调度等待或锁等待为历史长尾根因，也没有资格声称吞吐 CV 已恢复。正式新候选双平台验收、逐拨外部证据和 GPIO 独立物理观测仍未完成。

## 项目推送授权

2026-09-30 用户确认执行本次诊断分支推送，并明确该项目今后的对外推送不再逐次索取授权。此授权覆盖 Git 推送；合并与版本发布仍遵循各自的任务范围和验收条件。
