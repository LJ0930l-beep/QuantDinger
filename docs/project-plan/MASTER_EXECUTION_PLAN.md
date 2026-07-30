# 项目总控执行计划

## 产品总纲

目标是建设**安全、可审计、可回测、可重放的确定性加密量化交易系统**。任何可执行交易事实均必须来自版本化市场数据、确定性策略与参数、Decimal 数值、权威账户/仓位事实、Hard Risk、Reservation 和 Admission。

交易决策权限：确定性策略、仓位规模模型和 Hard Risk 均为 100%；AI / LLM / Agent 交易权限永久为 0%。

## 当前基线

| 项目 | 当前事实 |
| --- | --- |
| 后端仓库 | `LJ0930l-beep/QuantDinger` |
| 安全路线 | 13 / 16 = 81.25% |
| 已合并入口合同 | PR #30 `Add runtime entry ingress contracts` |
| Architecture Guard | 46 |
| Entry-Point legacy baseline | 44 |
| AI Boundary baseline | 2 个逐项登记的遗留导入；只能减少 |
| Live | OFF；不得启用 |

## 正式执行顺序

1. **REF-01 Runtime Entry Facts**：完成 caller-owned ingress persistence、认证 scope、凭证归属、账户/标的/持仓权威解析、同事务 Admission，以及旧 Quick Trade fail-closed。
2. **SC-13 Entry-Point Convergence**：REST、MANUAL、STRATEGY、PROTECTION 统一经 Admission；AGENT、MCP、GRID 保持 DISABLED 或退役。完成后为 14 / 16。
3. **SC-14 Read Cutover / G4-B**：Admission Outbox → 注册 Projection Consumer → Candidate Projection → Shadow Diff → Reconciliation → Derived Health → 只读 API。完成后为 15 / 16。
4. **SC-15 Legacy Retirement**：退役旧交易真相与旁路，并完成重启、回滚、重复事件、网络/数据库故障、孤儿事实演练。完成后为 16 / 16（Safety Core Complete，仍非 Live 启用）。
5. 在 Safety Core 后依次推进 DATA-01、BT-01、PS-01、STRAT-01、SMC-01、PORT-01、FE-01，最后仅准备 LIVE-R01（Controlled Live Ready / Live OFF）。

## 不在当前产品范围

- AI trading、AI Agent trading、LLM analysis 作为产品依赖、AI Candidate Plan、自然语言下单、机器学习实盘决策、强化学习执行。
- AI / LLM / Agent 不得决定方向、action、数量、目标仓位、风险效果、入场/出场、止损止盈、杠杆、策略切换或风险预算。

## 变更记录

| 日期 | 决定 | 说明 |
| --- | --- | --- |
| 2026-07-30 | GOV-00 Pure Quant Product Reset | AI / LLM / Agent trading scope removed by product decision. Direct AI trading authority is permanently zero under the current charter. |

历史 AI 路线保留在 [纯量化重置记录](../pure-quant-product-reset/README.md) 与 [AI Boundary Inventory](../pure-quant-product-reset/AI_BOUNDARY_INVENTORY.md)，用于审计和安全退役，不构成后续产品路线。
