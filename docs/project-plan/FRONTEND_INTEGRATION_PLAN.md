# 前端只读集成计划

产品前端名称为 **Quant Trading Dashboard**。当前独立原型可使用 Mock / PAPER / SHADOW 数据展示，但不代表交易功能已接入。

## 路线

| 阶段 | 状态 | 范围 |
| --- | --- | --- |
| F-01 | IN_PROGRESS | 只读 Mock Dashboard，路径 `/#/quant-dashboard`；无真实 API、Executor、Exchange 或交易按钮 |
| F-02 | NOT_STARTED | 鉴权 workspace 和权限 UX |
| F-03 | BLOCKED | 只读 Dashboard API 合同，依赖 SC-14 权威读模型 |
| F-04 | BLOCKED | 渲染账户、仓位、风险、Admission、Shadow、对账与健康的只读数据 |
| F-05 | NOT_STARTED | loading / stale / unavailable / unauthorized 状态 |

## 前端不变量

- 不得称为 “AI Quant Dashboard”，也不得提供 AI 一键交易、AI 推荐并执行、自动调仓、模型信心下单或 Prompt 下单。
- 金额与数量以字符串 Decimal 语义展示；前端不产生账户、仓位、风险或交易真相。
- Live 状态只由服务端权威事实提供；Dashboard 不得自行开启或推断 Live。
- 写交易 UX 即使未来讨论，也必须独立授权，且不得由此原型按钮演化而来。
