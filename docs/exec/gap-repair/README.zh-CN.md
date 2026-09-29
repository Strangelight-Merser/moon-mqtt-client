# 未关闭项的证据补齐

基线是 5ee6912fdfc8b9a9fd7747a316f1138d41000aca。既有 333 个文件、原测试、门槛、固件与月末交付包保持不变。

本目录记录实验约束；新增 tests/closure 为独立诊断，不能替代原 Release。disconnect_closure_wbtest.mbt 覆盖真实 writer 的准入前关闭、写入中关闭、超时和作用域取消。WSS 探针在 git archive 生成的隔离副本里加屏障，45 个原始结果包含 9 个撤销完成标记的负控，不把这些二进制称为发布候选。

有限窗口 ESP 对账要求连续串口、同一 boot、前后 status、连续 applied_count、MQTT command ID 和目标值一致。query 不能单独证明原命令执行；该对账不证明 GPIO 或跨重启恰好一次。

本次初步验证：原生 162/162；WSS 隔离边界 45/45；未插桩 WSS subscriber-close 10/10；ESP 同一 boot 的 4 条真实 HA 命令可对账，最后 OFF。完整原始证据在工作树 _build/gap-repair，最终报告另行封存。

E06 四条件诊断显示延后日志降低观测成本，不能证明历史波动根因。原始 A/B 基线仍是 indeterminate；额外 fsync 采样 A 溢出 32603 条，因此该采样整体不支持 A/B 因果结论。保留这些失败，禁止用后续成功覆盖。

尚需独立处理：原 Release 的完整同版本平台验证；逐拨 TCP/TLS/CONNACK 与 socket 生命周期的完整绑定；YN038 确认引脚与独立物理观测。旧 Linux 首次 WSS 事件的历史根因仍不可恢复，不与当前已知竞态的工程验证混为一项。
