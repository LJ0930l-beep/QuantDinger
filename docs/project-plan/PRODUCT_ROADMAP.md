# 产品路线图：Quant Trading Dashboard

## 正式产品链

```text
Safety Core
  -> Multi-Asset Domain
  -> Gate Capability Matrix
  -> Market Data
  -> Backtest / Research
  -> Paper / Shadow
  -> Deterministic Strategy Platform
  -> SMC / ICT
  -> Position Sizing / Leverage / Margin
  -> Account Cooldown Risk
  -> Frontend Product
  -> Operations
  -> Controlled Live Ready / Live OFF
```

## 阶段定义

| 阶段 | 状态 | 关键边界 |
| --- | --- | --- |
| SC-13 Entry Convergence | IN_PROGRESS | REST/MANUAL/STRATEGY/PROTECTION 收口；Agent/MCP/Grid DISABLED/退役 |
| SC-14 Read Cutover | DEFERRED | 只读 Projection/Shadow/Reconciliation/Health；需 G4-B 重新授权 |
| SC-15 Legacy Retirement | NOT_STARTED | 只清理已证明迁移的旁路并完成故障演练 |
| MAD-01 Multi-Asset Domain | PLANNED | Gate 产品类型和账户/持仓语义先独立建模 |
| DATA-01 / BT-01 / PS-01 | PLANNED | 数据、回测、Paper/Shadow 同一确定性语义 |
| STRAT-01 / SMC-01 | PLANNED | 策略只生成 Candidate Trade Plan，不直接触发交易 |
| PORT-01 / COOLDOWN | PLANNED | 用户选择杠杆与保证金成本，系统不静默改写 |
| FE-01 / OPS-01 | PLANNED | 前端可读、可审计、显示 stale/unavailable，不推断权威事实 |
| LIVE-R01 | PLANNED | 仅 Controlled Live Ready，Live 永久保持 OFF 直到独立批准 |

## AI 产品裁定

AI、LLM、Agent Trading Authority = 0%。产品名称使用 Quant Trading Dashboard，不使用 AI Quant Dashboard；不存在 AI 一键下单、Prompt 下单或 AI 自动调参。
