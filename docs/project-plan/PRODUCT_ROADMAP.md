# 产品路线图

```text
Safety Core Complete
  -> DATA-01 Market Data Foundation
  -> BT-01 Backtest and Research
  -> PS-01 Paper / Shadow Runtime
  -> STRAT-01 Deterministic Strategy Platform
  -> SMC-01 Deterministic SMC / ICT
  -> PORT-01 Portfolio and Risk Allocation
  -> FE-01 Frontend Read Integration
  -> LIVE-R01 Controlled Live Ready / Live OFF
```

## 当前安全核心路线

| 阶段 | 状态 | 交付边界 |
| --- | --- | --- |
| REF-01 Runtime Entry Facts | IN_PROGRESS | 入口身份、权威 scope/position、同事务 Admission；无 Exchange 写入 |
| SC-13 Entry-Point Convergence | BLOCKED | 等待 REF-01；收口 REST、MANUAL、STRATEGY、PROTECTION，并退役/禁用 AGENT、MCP、GRID |
| SC-14 Read Cutover | DEFERRED | 只读投影、Shadow、对账和健康；永不做交易决定 |
| SC-15 Legacy Retirement | NOT_STARTED | 清理已迁移旁路并完成故障演练 |

## 后续确定性产品能力

| ID | 目标 | 不可突破的边界 |
| --- | --- | --- |
| DATA-01 | Point-in-Time 市场数据、标的规则版本、数据新鲜度与确定性 K 线 | 不使用未来数据、隐式 forward fill、未记录来源或 float 权威事实 |
| BT-01 | 可重放回测：next-open、费用、滑点、Funding、部分成交、清算、walk-forward | 禁止 look-ahead、未版本化参数和未来账户/市场事实 |
| PS-01 | Paper / Shadow 订单、成交、仓位、PnL、恢复、对账与监控 | 与实盘无关；必须复用策略、风险与 Admission 语义 |
| STRAT-01 | 策略定义/版本/参数/数据依赖/信号/规模/生命周期/归因 | Candidate Trade Plan 只能是确定性策略输出 |
| SMC-01 | 可解释 SMC / ICT 规则引擎 | 每个信号带数据、规则、参数版本和命中/拒绝理由 |
| PORT-01 | 风险预算、暴露、相关性、回撤和确定性配置 | 禁止 online learning、RL 与自动改实盘参数 |
| FE-01 | Quant Trading Dashboard 的鉴权只读集成 | 无下单、撤单、调仓或前端自行推断权威事实 |
| LIVE-R01 | Controlled Live Ready 的技术准备 | 仍为 Live OFF，启用另需独立正式批准 |

## 已移出核心路线的历史范围

AI Analysis Layer、AI Candidate Plan、AI 市场判断、AI 参数建议、LLM / Agent 交易和 Full AI Quant Product 已因产品决定移出。它们不再是任何 Gate 或 Controlled Live 的前置条件。
