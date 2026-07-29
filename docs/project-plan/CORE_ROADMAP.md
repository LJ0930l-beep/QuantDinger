# 核心安全路线图

本文件只覆盖剩余正式安全路线：PR-13、PR-14、PR-15。基线为后端 `b111fae68d9034d48a649557da232939724c0e8f`，正式进度为 **13 / 16 = 81.25%**。

## 共同不可协商边界

- Live 保持 OFF；不得连接真实交易账户或启用自动下单。
- Architecture Guard 只能减少，不能高于 46；Entry-Point legacy baseline 只能减少，不能高于 44。
- 不得创建 fake Command / OrderIntent / Economic Order 以兼容旧路径。
- 不得绕过 Canonical Entry V2、Durable Entry、Hard Risk、Reservation 或 Admission Outbox。
- Gateway / Repository 的 caller-owned 核心不得自行 commit 或 rollback。
- `correlation_id` 只用于审计，不进入经济身份；随机 UUID 不能成为业务幂等身份。
- 任何 raw driver exception、Schema 前置不足或 CI 与批准 Head 不一致，均进入故障模式或停止。

## PR-13：Entry-Point Convergence

**目标状态：NOT_STARTED。** 完成后预期正式路线为 **14 / 16 = 87.5%**。

| Task ID | 状态 | 目标 | 依赖 | 允许范围 | 禁止范围 | 交付物 | 验证证据 | 闸门 | 停止条件 | 仓库 | 分支 / PR | 最后批准 Head |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C13-01 | NOT_STARTED | 重新确认 REST、MANUAL、STRATEGY、PROTECTION、AGENT、MCP、GRID 与 44 条 legacy bypass 的实际库存 | `b111fae` | 只读 inventory 与 Guard 证据 | 运行时改动、baseline 扩大 | 入口—旁路映射 | 路径、符号、调用证据 | PR-13 设计闸门 | 发现未知入口或 baseline 增加 | 后端 | TBD / TBD | `b111fae` |
| C13-02 | NOT_STARTED | 为每类入口定义到 `DurableEntryGraphV2` 的无损 Adapter 合同 | C13-01 | 纯合同、适配器测试 | Schema、Executor、Exchange | 七类 Adapter 合同 | action/actor/scope/idempotency/correlation/fingerprint 测试 | Adapter Contract Gate | 缺少权威事实或需 fake legacy fact | 后端 | `phase0/pr-13-*` / TBD | TBD |
| C13-03 | NOT_STARTED | 迁移 REST / MANUAL 入口 | C13-02 | 已批准入口收口与回归测试 | 直接交易执行、Live | 两类入口经 Admission 的证据 | caller-owned 原子链、CI、Guard 下降 | Entry-Point Convergence Gate | 绕过 Hard Risk 或产生 legacy 订单图 | 后端 | `phase0/pr-13-*` / TBD | TBD |
| C13-04 | NOT_STARTED | 迁移 STRATEGY 入口 | C13-02 | Strategy Adapter 与测试 | Strategy Runtime 业务重构、Exchange | Strategy 收口证据 | 仅通过 Admission；精确 Head CI | Entry-Point Convergence Gate | 直接 Executor/Exchange 调用 | 后端 | `phase0/pr-13-*` / TBD | TBD |
| C13-05 | NOT_STARTED | 迁移 PROTECTION，且只允许 REDUCE_RISK | C13-02 | Protection Adapter、权限与矩阵测试 | OPEN/INCREASE、Live | Protection 收口证据 | 减仓矩阵、无 reservation 增加风险事实 | Entry-Point Convergence Gate | Protection 可开仓、可加仓或可撤单 | 后端 | `phase0/pr-13-*` / TBD | TBD |
| C13-06 | NOT_STARTED | 迁移 AGENT / MCP / GRID，同时保持默认 DISABLED 且无 LIVE | C13-02 | 禁用模式、Paper/Shadow 合同与测试 | LIVE enum、真实执行、默认放行 | 受限来源收口证据 | DISABLED 零持久化/零 risk/零 outbox 调用 | Entry-Point Convergence Gate | Agent/MCP/Grid 获得直接执行权 | 后端 | `phase0/pr-13-*` / TBD | TBD |
| C13-07 | NOT_STARTED | 删除已迁移入口对应的 legacy bypass record | C13-03 至 C13-06 | 精确 Guard/baseline 收敛 | 未迁移入口、整目录 allowlist | 删除记录与差异说明 | baseline 只减不增、全回归 | Entry-Point Convergence Gate | 遗留路径仍在使用或 Guard 扩大 | 后端 | `phase0/pr-13-*` / TBD | TBD |

## PR-14：Read Cutover / G4-B

**目标状态：DEFERRED。** 它在 PR #21 中明确延后，必须在 PR-13 合并后重新获批。完成后预期正式路线为 **15 / 16 = 93.75%**。

| Task ID | 状态 | 目标 | 依赖 | 允许范围 | 禁止范围 | 交付物 | 验证证据 | 闸门 | 停止条件 | 仓库 | 分支 / PR | 最后批准 Head |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R14-01 | DEFERRED | 定义 Admission Outbox Consumer Contract | PR-13 DONE；G4-B 重启授权 | 类型化只读 Consumer 合同 | 交易决策、下单 | Consumer Contract | schema version、aggregate、payload parser 测试 | Read Cutover / G4-B Gate | 未知 payload 或不完整身份 | 后端 | TBD / TBD | `b111fae` |
| R14-02 | DEFERRED | 注册 `DURABLE_ENTRY_ADMITTED`、`DURABLE_CANCEL_ADMITTED`、`entry-admission-v2` | R14-01 | 注册与版本校验 | 自动交易或旧事件猜测 | 受控注册表 | unknown event fail-closed 测试 | Read Cutover / G4-B Gate | 未注册事件被接受 | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-03 | DEFERRED | 定义事件到 Projection 输入事实的无损映射 | R14-01 至 R14-02 | 纯映射与 fixture | correlation 进入经济身份 | 映射合同 | 字段覆盖、canonical payload、replay 测试 | Read Cutover / G4-B Gate | 事实丢失或从内存猜测 | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-04 | DEFERRED | 实现 caller-owned、幂等、可重建 Consumer | R14-03 | Consumer、DB 事务、并发测试 | 自行 commit/rollback、交易调用 | 可重放 Consumer | offset/event id 冲突、rollback、connection reuse | Read Cutover / G4-B Gate | raw driver error、不可重建投影 | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-05 | DEFERRED | 建立 Candidate Projection Generation | R14-04 | generation/watermark/rebuild | 覆盖 READY generation | Candidate Generation | 完整性、高水位、promotion/fail 测试 | Read Cutover / G4-B Gate | BUILDING 修改当前 READY | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-06 | DEFERRED | 连接 Shadow Diff，且 Shadow 永不产生交易决策 | R14-05 | deterministic comparison、tolerance | 下单、risk decision | Shadow 差异报告 | exact replay、容差、冲突测试 | Read Cutover / G4-B Gate | Shadow 影响 Admission | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-07 | DEFERRED | 连接 Reconciliation Checkpoint | R14-05 | checkpoint、health 输入事实 | 内存权威状态 | 对账检查点链路 | scoped checkpoint、rebuild、冲突测试 | Read Cutover / G4-B Gate | 对账健康被伪造或越 scope | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-08 | DEFERRED | 从持久化事实派生 Health | R14-06 至 R14-07 | pure derivation、read model | 交易决策、瞬时内存权威 | Derived Health | stale/failed/conflict 映射与重放 | Read Cutover / G4-B Gate | health 直接下单或覆盖事实 | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-09 | DEFERRED | 执行 G4-B | R14-01 至 R14-08 | 闸门验证、故障演练 | 未批准的生产切换 | G4-B 记录 | Consumer、Shadow、Reconciliation、Health 的精确 Head CI | Read Cutover / G4-B Gate | 任一链路仅靠测试替代权威合同 | 后端 | `phase0/pr-14-*` / TBD | TBD |
| R14-10 | DEFERRED | 建立供前端未来接入的只读 API 合同 | R14-09 | read-only API 合同和测试 | 交易命令、WebSocket 先行 | API Contract | 认证、stale/unavailable、无副作用测试 | Read Cutover / G4-B Gate | 返回非权威或可交易端点 | 后端 | `phase0/pr-14-*` / TBD | TBD |

## PR-15：Legacy Retirement

**目标状态：NOT_STARTED。** 仅当 PR-13 与 PR-14 均为 DONE 后重新计划。完成后预期正式路线为 **16 / 16 = 100%（Safety Core Complete）**，不等于 Live 自动启用。

| Task ID | 状态 | 目标 | 依赖 | 允许范围 | 禁止范围 | 交付物 | 验证证据 | 闸门 | 停止条件 | 仓库 | 分支 / PR | 最后批准 Head |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L15-01 | NOT_STARTED | 列出剩余 legacy reads / writes / routes / workers | PR-13、PR-14 DONE | 只读 inventory | 删除未证明路径 | 退役清单 | 路径、符号、调用和数据真相证据 | Legacy Retirement Gate | 真相来源不清 | 后端 | TBD / TBD | TBD |
| L15-02 | NOT_STARTED | 移除 legacy CommandGraph 与 OrderIntent 的权威角色 | L15-01 | 已迁移路径清理 | 数据丢失、fake V2 facts | 清理提交与兼容说明 | read/write 无残留、replay 回归 | Legacy Retirement Gate | 仍有权威读取或写入 | 后端 | `phase0/pr-15-*` / TBD | TBD |
| L15-03 | NOT_STARTED | 移除 direct Executor / Exchange / quick trade bypass | L15-01 | 已证实旁路收口 | 新 allowlist、Live | 旁路清理证据 | Guard / baseline 降低、静态架构测试 | Legacy Retirement Gate | 任何新增直接调用 | 后端 | `phase0/pr-15-*` / TBD | TBD |
| L15-04 | NOT_STARTED | 将所有读取切到 V2 Projection / Ledger / Reconciliation | L15-02 至 L15-03 | 只读切换、compat read | 未完成 projection 的读切换 | read cutover 记录 | rebuild、shadow、reconciliation 证据 | Legacy Retirement Gate | V2 读模型不完整 | 后端 | `phase0/pr-15-*` / TBD | TBD |
| L15-05 | NOT_STARTED | 执行升级、重启、回滚、重复消息、网络/DB 故障演练 | L15-04 | 容器/集成故障测试 | 真实交易账户、生产写入 | 演练矩阵与结果 | 订单、账本、projection、health 一致性 | Legacy Retirement and Failure Drill Gate | 演练出现未分类错误或无法恢复 | 后端 | `phase0/pr-15-*` / TBD | TBD |
| L15-06 | NOT_STARTED | 确认无孤儿 Reservation/Outbox/未知身份，并完成 16/16 | L15-05 | 审计查询、验证记录 | 删除历史证据、自动 Live | 最终 Safety Core 报告 | 精确 Head CI、Guard/baseline 目标、tree parity | Legacy Retirement and Failure Drill Gate | 任一孤儿或身份漂移 | 后端 | `phase0/pr-15-*` / TBD | TBD |
