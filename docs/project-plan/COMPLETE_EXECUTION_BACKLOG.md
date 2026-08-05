# Complete Execution Backlog

这是 V6 唯一授权任务表。未登记任务不得自动启动；每项进入实现前必须有 Gate、依赖、允许/禁止范围、DoD、测试和停止条件。

| ID | 阶段 | 依赖 | 交付物 | Gate |
| --- | --- | --- | --- | --- |
| SC13-C01 | 入口收口 | PR #33 | 完整入口 inventory 与 baseline 证据 | Entry Convergence |
| SC13-C02 | 入口收口 | C01 | REST/MANUAL/STRATEGY/PROTECTION/受限来源 Adapter 合同 | Adapter Contract |
| SC13-C03 | 入口收口 | C02 | REST/MANUAL caller-owned Admission | Entry Convergence |
| SC13-C04 | 入口收口 | C02 | Strategy Candidate Plan → Admission | Entry Convergence |
| SC13-C05 | 入口收口 | C02 | Protection reduce-only Admission | Entry Convergence |
| SC13-C06 | 入口收口 | C02 | Grid/Agent/MCP 禁用或退役 | Entry Convergence |
| SC14-R01..R10 | 只读切换 | SC13 | Consumer、Projection、Shadow、Reconciliation、Health、只读 API | G4-B |
| SC15-L01..L06 | 退役 | SC13+SC14 | legacy 清理与故障演练 | Legacy Retirement |
| MAD-01 | 多资产 | Safety Core | Gate 产品和账户/持仓/订单/费用/保证金合同 | Multi-Asset |
| GATE-00..10 | Gate | MAD-01 | capability、权限、余额、instrument、venue 垂直闭环 | Venue Gates |
| DATA-01 | 数据 | GATE-00 | PIT 数据集、规则快照、去重/缺失/冲突 | Data Gate |
| BT-01 | 回测 | DATA-01 | 可重放回测与指标 | Backtest Gate |
| PS-01 | Paper/Shadow | BT-01 | 共享策略和风险语义 | Paper Gate |
| STRAT-01/SMC-01 | 策略 | DATA/BT/PS | 内建确定性策略库与 SMC/ICT | Strategy Gate |
| PORT-01/COOLDOWN | 风险 | Safety Core | 用户杠杆、保证金成本、冷静期 | Risk Gate |
| FE-01/OPS-01 | 产品 | SC14/PS | 只读 Dashboard、监控和告警 | Product Gate |
| LIVE-R01 | 最终准备 | 全部 | Gate Spot/Perpetual TestNet、Paper/Shadow、Canary、恢复/对账和人工确认后的受控 Live 证据；Live 默认 OFF | Final Gate |

## V8 集成交付口径

上表任务是内部依赖索引，不代表把项目拆成互相独立的小产品。每个已登记任务必须最终接入真实纵向链路、持久化、API、前端和恢复/对账证据。当前集成分支继续推进 Gate Spot/Perpetual 的完整闭环：研究、回测、Paper、TestNet 仿真/适配、Admission、Worker、Executor、Ledger、Position/PnL、Reconciliation、Kill Switch 和 Dashboard。任何单独 Contract、Mock 或 Draft PR 均不能标记为产品完成。

当前已落地的 Paper 持久化基础包括 `qd_paper_execution_orders`、`qd_paper_execution_fills`、`qd_paper_execution_order_events` 与 `qd_paper_recovery_checkpoints`；它们采用 Decimal、幂等指纹、追加事件和 caller-owned 事务，作为后续真实 Worker/Projection 接入前的恢复事实边界。

当前 Gate 垂直集成还包括一个显式 TestNet-only 私有读取边界：加密凭证引用只能在
`QUANT_GATE_PRIVATE_READ_ENABLED=1` 时解析，HTTP 侧只允许固定 Gate TestNet HTTPS
主机和 GET；TestNet 下单适配器必须由调用方显式注入并开启写入，默认仍为 disabled。
这些适配器尚未接入 Worker、Executor 或任何默认启动路径，因而不会产生网络或交易副作用。

## 任务准入

任务必须从 main 创建独立 Draft PR；共享 Identity/Schema/Transaction/Risk Contract 不并行修改。出现真实交易、AI 权威、Guard 上升、未分类 DB 错误或无法重放，立即退回 Gate。
