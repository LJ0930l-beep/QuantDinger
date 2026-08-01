# Frontend Trading Workspace 计划

产品名称：**Quant Trading Dashboard**。当前页面是只读 Mock/PAPER/SHADOW 原型，不代表交易能力已接入。

## 阶段

| 阶段 | 状态 | 范围 |
| --- | --- | --- |
| F-01 Mock Dashboard | CANDIDATE | 只读展示账户、仓位、风险、Admission、Shadow、对账和健康；无真实 API |
| F-02 Trading Workspace | PLANNED | 只读 K 线、指标、信号、订单/成交事实和配置快照；不得前端推断权威事实 |
| F-03 Read API Contract | BLOCKED | 依赖 SC-14/G4-B 的权威 Projection 与 stale/unavailable/unauthorized 语义 |
| F-04 Account/Position/Risk views | BLOCKED | 依赖已验证的 Projection/Ledger/Reconciliation |
| F-05 Loading/Health UX | PLANNED | 明确显示 loading、stale、unavailable、unauthorized，不用空值伪装健康 |

## 不可变边界

- 前端不产生账户、持仓、风险、策略或交易事实；金额/数量按 Decimal 字符串展示。
- 不提供下单、撤单、改仓、自动调参或 AI 建议执行按钮。
- Live 状态只能来自服务端权威事实，不能由前端开启或推断。
- 任何未来写入口必须另行授权，并强制经过 Canonical Entry → Hard Risk → Admission。
