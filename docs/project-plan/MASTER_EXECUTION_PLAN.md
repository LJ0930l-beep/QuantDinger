# 项目剩余工作总控计划

> **唯一总控来源。** 后续任何获批工作开始前，必须先读取本文件与对应任务卡。本文只记录计划、状态和证据；不授权自动启动后续任务。

## 1. 项目目标

建立安全、可审计、可重放的 AI 加密量化交易平台，按受控顺序支持 Paper Trading、Shadow Trading、策略运行时、SMC / ICT 策略、AI 市场分析，以及经过单独决策批准的小额 Live Trading。

当前 **13 / 16 = 81.25%** 的正式路线仅表示安全核心建设进度，绝不表示产品、策略能力或 Live Trading 已完成。

## 2. 当前基线

| 项目 | 已证实基线 |
|---|---|
| 后端仓库 | `LJ0930l-beep/QuantDinger` |
| 后端基线 | `main` / `origin/main` = `b111fae68d9034d48a649557da232939724c0e8f` |
| 当前分支 | `docs/project-master-execution-plan`（仅本文档任务） |
| 已合并准入链路 | PR #21 `Add canonical entry admission gateway`；合并提交 `b111fae` |
| PR #21 审批 Head | `c6f52b2541f38addf386e8e55a70309999dff747` |
| 历史安全链路证据 | `git log` 可追溯 #8–#25：Command/Intent、Schema、Venue、Ledger、Outbox/Projection、Hard Risk、V2 Entry、Durable Entry、Admission Gateway |
| 正式安全路线 | 13 / 16 = 81.25% |
| Architecture Guard | 46 |
| Entry-Point legacy baseline | 44 |
| Live | OFF；`AGENT_LIVE_TRADING_ENABLED` 未启用 |
| G4-A | PASS（PR #21） |
| G4-B | DEFERRED 至 PR-14 Read Cutover |
| 前端引用 | `LJ0930l-beep/QuantDinger-Vue` Draft PR #1，Head `a72a8c80491da5057c50ab6408c5effd2451c8d1`，路由 `/#/quant-dashboard` |
| 前端边界 | 只读 Mock / PAPER / SHADOW；无真实 API、Executor、Exchange 或下单调用 |
| 不纳入版本控制 | 根仓库未跟踪 `docs/codex/`；本计划不得修改、暂存或提交它 |

### 已完成安全核心证据

| 里程碑 | 证据 |
|---|---|
| Canonical Entry V2 | PR #22，合并提交 `8b0b571` |
| Durable Entry | PR #23，合并提交 `91a6900` |
| Durable Hard Risk V2 | PR #25，合并提交 `f5f5fe2` |
| Reservation Matrix | PR #12，合并提交 `f4f8b69`；随后由 PR #25 的 V2 路径扩展 |
| Transactional Outbox | PR #15，合并提交 `fef5bcb` |
| Typed Admission Event | PR #21，合并提交 `b111fae` |

## 3. 完成定义分层

| 层级 | 定义 | 当前结论 |
|---|---|---|
| Safety Core Complete | 16/16 安全路线完成，所有入口收口、读模型切换、遗留权威路径退役与故障演练通过 | NOT_STARTED |
| Paper / Shadow Product Complete | 安全核心之上具备可长期运行的 Paper / Shadow 产品闭环 | BLOCKED：依赖 Safety Core Complete |
| Controlled Live Ready | 受控小额 Live 的全部技术、运维、人审和演练条件满足；仍需单独正式决定启用 | BLOCKED：依赖前两层及专门 Live 决策 |
| Full AI Quant Product Complete | 策略、AI、运营体验和商业化范围均达到产品验收 | BLOCKED：不是 16/16 的含义 |

## 4. 统一状态词典

仅使用以下状态：

- `DONE`：交付物和闸门证据均已存在。
- `READY`：依赖与范围已明确，但尚未获得本次执行授权。
- `IN_PROGRESS`：已获授权，正在执行。
- `BLOCKED`：缺少外部前置、事实、权限或安全证据。
- `DEFERRED`：经明确架构决策延后。
- `NOT_STARTED`：尚未准备或授权。

状态变化必须追加记录日期、批准 Head、PR、验证证据与变更原因；不得删除历史证据，也不得将估计日期写成承诺日期。

## 5. 执行协议

每次开始任何工作前，执行者必须：

1. 阅读本文件和当前阶段的任务卡。
2. 验证任务依赖已满足，且目标 Head / PR / CI 仍一致。
3. 检查 `main` 与 `origin/main`、活跃 PR 数量、Live OFF、Guard 及 Entry-Point baseline。
4. 确认本次只有一个明确获批任务；未获批的后续项不得自动启动。
5. 保持最多 **2 个** 活跃实现 PR；文档 PR 仅在获批时例外且不得夹带代码。
6. 阶段完成后停止、汇报、等待闸门；不得自行推进下一阶段。

## 6. 当前总控任务卡

| Task ID | 状态 | 目标 | 依赖 | 允许范围 | 禁止范围 | 交付物 | 验证证据 | 闸门 | 停止条件 | 仓库 | 分支 / PR | 最后批准 Head |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EXEC-13 | NOT_STARTED | 将七类入口收口到 Canonical Entry V2 Admission Gateway | PR #21 / `b111fae` | 仅 PR-13 计划内适配、测试、Guard 收敛 | Live、直接 Executor/Exchange、伪造 legacy fact | Entry-Point Convergence 变更与审计 | 七类入口映射、baseline 只减不增、精确 Head CI | Entry-Point Convergence Gate | Guard 增加、旁路、新增实盘能力 | 后端 | TBD / TBD | `b111fae` |
| EXEC-14 | DEFERRED | 完成 Admission Outbox 到 Projection / Shadow / Reconciliation / Health 的读切换 | EXEC-13 合并；G4-B 前置 | 仅 PR-14 Consumer、Candidate Projection、只读 API | 交易决策、直接下单、Live | 可重建读模型与 Read Cutover 证据 | watermark、generation、replay、shadow、checkpoint 演练 | Read Cutover / G4-B Gate | 任何 projection/shadow/reconciliation 交易决策 | 后端 | TBD / TBD | `b111fae` |
| EXEC-15 | NOT_STARTED | 退役遗留交易真相与所有已迁移旁路 | EXEC-13、EXEC-14 DONE | 仅已证实 legacy 读写、路由、Worker 清理与故障演练 | 删除可恢复证据、破坏性迁移、Live | Legacy Retirement 清单、移除证据、演练记录 | 无孤儿事实、baseline 降至批准目标、故障演练 | Legacy Retirement and Failure Drill Gate | 无法证明真相来源或回滚路径 | 后端 | TBD / TBD | TBD |
| EXEC-FE | IN_PROGRESS | 维护独立只读前端原型，作为未来只读集成参考 | 前端 Draft PR #1 | 仅 Mock、只读视觉与静态测试 | 后端安全链路、真实 API、真实交易 | `/quant-dashboard` 原型 | 前端构建、Mock 测试、Draft PR #1 | 前端独立评审 | 任何真实 API / Executor / Exchange 调用 | 前端 | `feature/frontend-quant-dashboard-prototype` / #1 | `a72a8c8` |

## 7. 关联文档

- [核心安全路线](CORE_ROADMAP.md)
- [产品路线](PRODUCT_ROADMAP.md)
- [前端集成计划](FRONTEND_INTEGRATION_PLAN.md)
- [闸门与停止条件](GATES_AND_STOP_CONDITIONS.md)
- [发布就绪检查表](RELEASE_READINESS_CHECKLIST.md)

## 8. 本次文档变更记录

| 日期 | 状态 | Head | PR | 证据 | 说明 |
|---|---|---|---|---|---|
| 2026-07-29 | IN_PROGRESS | `b111fae` | 本文档 Draft PR（待创建） | 当前 main、`git log`、前端 Draft PR #1 基线 | 初次建立唯一总控计划；不改变生产代码 |
